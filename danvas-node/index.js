// danvas-node — the Node SDK for danvas: dial into a running canvas (danvasd
// or any hub) as a SOURCE, register native panels, react to what browsers do.
//
// This is the plan's acceptance test made real: written against PROTOCOL.md
// and the per-template `contract` blocks in danvas/templates/components.json,
// not against the Python or Rust source. Zero dependencies — Node ≥22 ships a
// WebSocket, and the single-threaded event loop gives ordered dispatch for
// free. It passes tests/test_sdk_conformance.py via its conformance target.
//
//   import { connect } from 'danvas-node'
//   const c = await connect('127.0.0.1:8000', 'telemetry')
//   c.registerTemplate('temp', 'slider', { data: { min: 0, max: 100, value: 20 },
//                                          x: 40, y: 40 })
//   c.onInput('temp', (p) => console.log('browser set', p.value))
//   setInterval(() => c.update('temp', 'post', readSensor()), 500)

import { readFileSync, existsSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import { spawn as spawnProcess, exec } from 'node:child_process'
import { createConnection } from 'node:net'
import { delimiter, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HEARTBEAT_MS = 10_000  // hubs reap connections silent for ~30 s
const RECONNECT_MS = 1_000
const DOWNLOAD_TTL_MS = 300_000

// The language-neutral panel templates (the same asset the Python and Rust
// SDKs ship). Resolution order: $DANVAS_TEMPLATES, the repo-checkout path,
// else FETCHED from the hub's /__templates__ on connect -- the no-shipping
// option, version-matched to the hub's embedded frontend by construction.
function loadLocalTemplates() {
  const path = process.env.DANVAS_TEMPLATES
    || join(dirname(fileURLToPath(import.meta.url)),
            '..', 'danvas', 'templates', 'components.json')
  try {
    return JSON.parse(readFileSync(path, 'utf-8')).templates
  } catch {
    return null
  }
}

function normalizeWs(url) {
  const t = String(url).trim()
  if (t.includes('://')) {
    const [scheme, rest] = t.split('://', 2)
    const ws = scheme === 'http' ? 'ws' : scheme === 'https' ? 'wss' : scheme
    const base = rest.replace(/\/+$/, '')
    return `${ws}://${base.endsWith('/ws') ? base : base + '/ws'}`
  }
  const hostport = t.includes(':') ? t : `localhost:${t}`
  return `ws://${hostport}/ws`
}

// The binary envelope: [code u8][idLen u8][id utf-8][payload] (PROTOCOL.md).
function binFrame(code, id, payload) {
  const idb = new TextEncoder().encode(id)
  const buf = new Uint8Array(2 + idb.length + payload.length)
  buf[0] = code
  buf[1] = idb.length
  buf.set(idb, 2)
  buf.set(payload, 2 + idb.length)
  return buf
}

function binParse(data) {
  const u8 = new Uint8Array(data)
  if (u8.length < 2) return null
  const idLen = u8[1]
  if (u8.length < 2 + idLen) return null
  return {
    code: u8[0],
    id: new TextDecoder().decode(u8.subarray(2, 2 + idLen)),
    payload: u8.subarray(2 + idLen),
  }
}

const mintToken = () => randomBytes(24).toString('base64url')

export class Client {
  constructor(url, label) {
    this._uri = `${normalizeWs(url)}${normalizeWs(url).includes('?') ? '&' : '?'}source=1&label=${label}&vname=${label}`
    this._httpBase = normalizeWs(url).replace(/^ws/, 'http').replace(/\/ws$/, '')
    this.label = label
    this._templates = loadLocalTemplates()
    this._ws = null
    this._closing = false
    this._connected = null // resolver for the first connection
    // Everything declared, for replay on (re)connect (PROTOCOL: the client
    // must re-send registers + current state; the hub replays to browsers).
    this._regOrder = []
    this._registers = new Map()   // cid -> register frame
    this._updates = new Map()     // cid -> accumulated update payload
    this._subs = new Set()
    // Handlers.
    this._onInput = new Map()
    this._onLayout = new Map()
    this._onBinary = new Map()
    this._onRequest = new Map()   // one answer per panel
    this._frameTaps = []
    // Hub features from welcome ("rel": the frontend owns relative placement).
    this.hubFeatures = []
    // Local mirror of the canvas we joined (id -> {component,name,props,state}).
    this.panels = new Map()
    // File transfer: minted download tokens and upload endpoints.
    this._downloads = new Map()   // token -> {filename, bytes, expires}
    this._uploads = new Map()     // token -> handler(file)
    this._pendingPushes = new Map() // reqId -> file_push meta
  }

  // -- lifecycle --------------------------------------------------------------
  async connect(timeoutMs = 10_000) {
    if (this._ws) return this
    const first = new Promise((resolve, reject) => {
      this._connected = resolve
      setTimeout(() => reject(new Error(`could not reach the hub at ${this._uri}`)),
                 timeoutMs)
    })
    this._dial()
    this._heart = setInterval(() => this._sendRaw({ type: 'heartbeat' }),
                              HEARTBEAT_MS)
    await first
    if (!this._templates) {
      // No local asset: fetch the hub's own copy (served by danvasd and the
      // Python hub alike), guaranteed to match the frontend it embeds.
      const resp = await fetch(`${this._httpBase}/__templates__`)
      if (!resp.ok) throw new Error(`hub has no /__templates__ (${resp.status})`)
      this._templates = (await resp.json()).templates
    }
    return this
  }

  close() {
    this._closing = true
    clearInterval(this._heart)
    try { this._ws?.close() } catch { /* already down */ }
  }

  _dial() {
    if (this._closing) return
    const ws = new WebSocket(this._uri)
    ws.binaryType = 'arraybuffer'
    ws.addEventListener('open', () => {
      this._ws = ws
      this._replay()
      this._connected?.(this)
      this._connected = null
    })
    ws.addEventListener('message', (ev) => this._onRaw(ev.data))
    ws.addEventListener('close', () => {
      this._ws = null
      if (!this._closing) setTimeout(() => this._dial(), RECONNECT_MS)
    })
    ws.addEventListener('error', () => { /* close follows */ })
  }

  _replay() {
    for (const cid of this._regOrder) this._sendRaw(this._registers.get(cid))
    for (const [cid, payload] of this._updates) {
      this._sendRaw({ type: 'update', id: cid, payload })
    }
    for (const cid of this._subs) this._sendRaw({ type: 'subscribe', id: cid })
  }

  _sendRaw(msg) {
    if (this._ws?.readyState === WebSocket.OPEN) this._ws.send(JSON.stringify(msg))
  }

  _sendBinary(bytes) {
    if (this._ws?.readyState === WebSocket.OPEN) this._ws.send(bytes)
  }

  // -- authoring panels ---------------------------------------------------------
  /** Register a raw panel (arbitrary component/props) — the escape hatch. */
  register(cid, component, props = {}, place = {}) {
    const msg = { type: 'register', id: cid, name: cid, component, props, ...place }
    if (!this._registers.has(cid)) this._regOrder.push(cid)
    this._registers.set(cid, msg)
    this._updates.delete(cid)
    this._sendRaw(msg)
    return this
  }

  /**
   * Register a NATIVE built-in from the shared template asset. `opts`:
   * `data` (merged over the template's data defaults — see the template's
   * `contract.data`), `x`/`y`/`w`/`h`, `label`, `rel` ({kind, anchor, gap} —
   * PROTOCOL.md § relative placement), extra `props` (e.g. Custom's html).
   */
  registerTemplate(cid, kind, opts = {}) {
    const tpl = this._templates[kind]
    if (!tpl) throw new Error(`unknown template kind ${kind}`)
    const props = { ...tpl.props, ...(opts.props || {}) }
    props.data = JSON.stringify({ ...tpl.data, ...(opts.data || {}) })
    props.label = opts.label || cid
    if (opts.w !== undefined) props.w = opts.w
    if (opts.h !== undefined) props.h = opts.h
    const place = {}
    if (opts.x !== undefined) place.x = opts.x
    if (opts.y !== undefined) place.y = opts.y
    if (opts.rel) place.rel = opts.rel
    return this.register(cid, tpl.component, props, place)
  }

  /** Stream new state: `update(id, 'post', value)` — keys per the contract. */
  update(cid, key, value) {
    const acc = this._updates.get(cid) || {}
    acc[key] = value
    this._updates.set(cid, acc)
    this._sendRaw({ type: 'update', id: cid, payload: { [key]: value } })
    return this
  }

  remove(cid) {
    this._regOrder = this._regOrder.filter((c) => c !== cid)
    this._registers.delete(cid)
    this._updates.delete(cid)
    this._sendRaw({ type: 'remove', id: cid })
    return this
  }

  // -- callbacks -----------------------------------------------------------------
  onInput(cid, fn) { this._push(this._onInput, cid, fn); return this }
  onLayout(cid, fn) { this._push(this._onLayout, cid, fn); return this }
  onBinary(cid, fn) { this._push(this._onBinary, cid, fn); return this }
  /** Answer a browser's `await canvas.request(data)`: `fn(data) -> result`. */
  onRequest(cid, fn) { this._onRequest.set(cid, fn); return this }
  /** Tap every frame the hub sends (read access to the whole canvas). */
  onFrame(fn) { this._frameTaps.push(fn); return this }

  _push(map, cid, fn) {
    if (!map.has(cid)) map.set(cid, [])
    map.get(cid).push(fn)
  }

  // -- the shared plane ------------------------------------------------------------
  setProps(cid, props) { this._sendRaw({ type: 'set_props', id: cid, props }); return this }
  subscribe(cid, fn) {
    this._subs.add(cid)
    this._sendRaw({ type: 'subscribe', id: cid })
    if (fn) this.onInput(cid, fn)
    return this
  }
  unsubscribe(cid) {
    this._subs.delete(cid)
    this._sendRaw({ type: 'unsubscribe', id: cid })
    return this
  }

  // -- binary media -------------------------------------------------------------------
  sendMedia(code, cid, bytes) { this._sendBinary(binFrame(code, cid, bytes)); return this }
  sendVideo(cid, jpeg) { return this.sendMedia(1, cid, jpeg) }
  sendAudio(cid, pcm) { return this.sendMedia(2, cid, pcm) }

  // -- file transfer (PROTOCOL.md § file transfer) ---------------------------------------
  /** Stash bytes under an unguessable token; returns the /__download__ URL. */
  serveBytes(filename, bytes) {
    const token = mintToken()
    const now = Date.now()
    for (const [t, d] of this._downloads) if (d.expires < now) this._downloads.delete(t)
    this._downloads.set(token, { filename, bytes, expires: now + DOWNLOAD_TTL_MS })
    return `/__download__/${token}`
  }

  /** Answer a download panel's click: `fn() -> [filename, bytes]`. */
  onDownload(cid, fn) {
    return this.onRequest(cid, () => {
      const [filename, bytes] = fn()
      return { url: this.serveBytes(filename, bytes), filename }
    })
  }

  /** A file-receiving endpoint: returns the /__upload__ URL; `fn(file)`. */
  uploadEndpoint(fn) {
    const token = mintToken()
    this._uploads.set(token, fn)
    return `/__upload__/${token}`
  }

  /** Wire an upload panel: mints an endpoint into its `url` data field. */
  onUpload(cid, fn) {
    return this.update(cid, 'data_patch', { url: this.uploadEndpoint(fn) })
  }

  // -- inbound -----------------------------------------------------------------------------
  _onRaw(data) {
    if (typeof data !== 'string') return this._handleBinary(data)
    let msg
    try { msg = JSON.parse(data) } catch { return }
    this._handle(msg)
  }

  _handleBinary(data) {
    const env = binParse(data)
    if (!env) return
    if (env.code === 6) return this._handleFileBytes(env.id, env.payload)
    for (const fn of this._onBinary.get(env.id) || []) fn(env.payload)
  }

  _handle(msg) {
    for (const tap of this._frameTaps) {
      try { tap(msg) } catch (e) { console.error(e) }
    }
    const { type: kind, id: cid } = msg
    const run = (map, arg) => {
      for (const fn of map.get(cid) || []) {
        try { fn(arg) } catch (e) { console.error(e) }
      }
    }
    switch (kind) {
      case 'welcome':
        this.hubFeatures = Array.isArray(msg.features) ? msg.features : []
        break
      case 'input':
        run(this._onInput, msg.payload ?? {})
        break
      case 'layout': {
        // Fold a browser's move/resize of OUR panel into the replay cache so
        // hand-arranged layouts survive a hub restart (a conformance duty).
        if (this._registers.has(cid)) {
          const acc = this._updates.get(cid) || {}
          for (const k of ['x', 'y', 'w', 'h', 'rotation']) {
            if (typeof msg[k] === 'number') acc[k] = msg[k]
          }
          this._updates.set(cid, acc)
        }
        run(this._onLayout, msg)
        break
      }
      case 'request': {
        const fn = this._onRequest.get(cid)
        if (fn) {
          try {
            const result = fn(msg.data ?? null)
            this._sendRaw({ type: 'response', reqId: msg.reqId, result })
          } catch (e) {
            this._sendRaw({ type: 'response', reqId: msg.reqId,
                            error: String(e?.message || e) })
          }
        }
        break
      }
      case 'register':
        this.panels.set(cid, { component: msg.component, name: msg.name,
                               owner: msg.owner, props: msg.props || {},
                               state: {} })
        break
      case 'update': {
        const entry = this.panels.get(cid)
        if (entry && msg.payload && typeof msg.payload === 'object') {
          Object.assign(entry.state, msg.payload)
        }
        break
      }
      case 'remove':
        this.panels.delete(cid)
        break
      case 'set_props': {
        // The shared property plane, hub-routed: apply a peer's write on OUR
        // panel and echo it — the owner's echoed state is canonical. A thin
        // SDK folds placement keys like a layout and merges everything else
        // into the panel's data blob (replay stays canonical), echoing a
        // data_patch so every browser converges.
        const reg = this._registers.get(cid)
        if (!reg || !msg.props || typeof msg.props !== 'object') break
        const echo = {}
        const patch = {}
        for (const [k, v] of Object.entries(msg.props)) {
          if (['x', 'y', 'w', 'h', 'rotation', 'opacity'].includes(k)) {
            const acc = this._updates.get(cid) || {}
            acc[k] = v
            this._updates.set(cid, acc)
            echo[k] = v
          } else {
            patch[k] = v
          }
        }
        if (Object.keys(patch).length) {
          let blob = {}
          try { blob = JSON.parse(reg.props.data || '{}') } catch { blob = {} }
          reg.props.data = JSON.stringify({ ...blob, ...patch })
          echo.data_patch = patch
        }
        if (Object.keys(echo).length) {
          this._sendRaw({ type: 'update', id: cid, payload: echo })
        }
        break
      }
      case 'file_pull': {
        // Broadcast: answer EVERY pull — silence would hang the hub's HTTP
        // reply for its full 15 s deadline (a protocol MUST).
        const item = this._downloads.get(msg.token)
        if (!item || item.expires < Date.now()) {
          this._sendRaw({ type: 'file_meta', reqId: msg.reqId, ok: false })
        } else {
          this._sendRaw({ type: 'file_meta', reqId: msg.reqId, ok: true,
                          filename: item.filename })
          this._sendBinary(binFrame(6, String(msg.reqId), item.bytes))
        }
        break
      }
      case 'file_push':
        this._pendingPushes.set(msg.reqId, msg)
        break
      default:
        break
    }
  }

  _handleFileBytes(reqId, payload) {
    const push = this._pendingPushes.get(reqId)
    if (!push) return
    this._pendingPushes.delete(reqId)
    const fn = this._uploads.get(push.token)
    if (!fn) {
      this._sendRaw({ type: 'file_ack', reqId, ok: false }) // decline-fast
      return
    }
    const name = String(push.name || 'upload.bin').split(/[/\\]/).pop()
    try {
      fn({ name, size: payload.length,
           contentType: push.content_type || 'application/octet-stream',
           data: payload })
    } catch (e) {
      console.error(e)
    }
    this._sendRaw({ type: 'file_ack', reqId, ok: true, name,
                    size: payload.length })
  }

  /** Resolve a panel's composed id from its register `name`. */
  find(name) {
    for (const [cid, entry] of this.panels) if (entry.name === name) return cid
    return null
  }
}

/** Dial into a canvas as a source; resolves once the first connection is up.
 * This is the OPT-OUT of self-serving: use it when a hub is already being
 * served elsewhere (another process, another machine). The default entry
 * point for a danvas program is [serve]. */
export function connect(url, label = 'source') {
  return new Client(url, label).connect()
}

// -- self-contained serving (the default entry point) ---------------------------
// The danvas SDK convention: a program's default move is to OWN its canvas —
// find/spawn danvasd on the port (or attach to one already serving it),
// dial in, open the browser. Dial-only (`connect`) is the explicit opt-out.

function findDanvasd() {
  const exe = process.platform === 'win32' ? 'danvasd.exe' : 'danvasd'
  const env = process.env.DANVASD
  if (env && existsSync(env)) return env
  for (const dir of (process.env.PATH || '').split(delimiter)) {
    if (dir && existsSync(join(dir, exe))) return join(dir, exe)
  }
  const here = dirname(fileURLToPath(import.meta.url))
  for (const rel of ['../broker/target/release', '../broker/target/debug']) {
    const p = join(here, rel, exe)
    if (existsSync(p)) return p
  }
  return null
}

function portOpen(port) {
  return new Promise((resolve) => {
    const s = createConnection({ host: '127.0.0.1', port, timeout: 300 })
    s.once('connect', () => { s.destroy(); resolve(true) })
    s.once('error', () => resolve(false))
    s.once('timeout', () => { s.destroy(); resolve(false) })
  })
}

function openBrowser(url) {
  const cmd = process.platform === 'win32' ? `start "" "${url}"`
    : process.platform === 'darwin' ? `open "${url}"` : `xdg-open "${url}"`
  exec(cmd, () => {})
}

/**
 * Serve a canvas from this process alone (the default entry point): spawn
 * `danvasd` on `port` — or attach to one already serving it — dial in as
 * `label`, and open the browser (when we spawned). The broker child dies
 * with this process. `opts`: `host` ('127.0.0.1'; '0.0.0.0' for LAN),
 * `openBrowser` (default true when spawning).
 *
 * Resolves to the connected [Client]; `client.broker` is the danvasd child
 * process (or null when attached to an existing hub).
 */
export async function serve(port = 8000, label = 'source', opts = {}) {
  const host = opts.host || '127.0.0.1'
  let broker = null
  if (!(await portOpen(port))) {
    const binary = findDanvasd()
    if (!binary) {
      throw new Error([
        'danvasd (the serving binary) was not found. Fix by one of:',
        '  - point $DANVASD at a danvasd binary',
        '  - put danvasd on $PATH',
        '  - build it from a checkout: cargo build --release --manifest-path broker/Cargo.toml',
        '(or dial into an already-served canvas with connect())',
      ].join('\n'))
    }
    broker = spawnProcess(binary, ['--port', String(port), '--host', host],
                          { stdio: 'ignore' })
    const kill = () => { try { broker.kill() } catch { /* gone */ } }
    process.on('exit', kill)
    const deadline = Date.now() + 15_000
    while (!(await portOpen(port))) {
      if (broker.exitCode !== null) throw new Error('danvasd exited on startup')
      if (Date.now() > deadline) { kill(); throw new Error('danvasd never opened its port') }
      await new Promise((r) => setTimeout(r, 100))
    }
  }
  const client = await new Client(`127.0.0.1:${port}`, label).connect()
  client.broker = broker
  if (broker && opts.openBrowser !== false) {
    openBrowser(`http://127.0.0.1:${port}`)
  }
  return client
}
