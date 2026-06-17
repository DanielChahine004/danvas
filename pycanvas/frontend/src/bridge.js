import { createShapeId, createBindingId } from 'tldraw'
import { COMPONENT_TO_SHAPE } from './canvas'
import { BIN_VIDEO, BIN_AUDIO, BIN_CUSTOM, BIN_REACT } from './protocol.generated.js'

// Single shared WebSocket connection. All components are multiplexed over it,
// keyed by component id. State lives in Python + tldraw shape props only.
let editor = null
let ws = null

// Auto-place panels that arrive without an explicit x/y. We flow them
// left-to-right, top-to-bottom and pack by each panel's *real* size (+ a gap),
// so they never overlap — unlike a fixed-step grid, which collided whenever a
// panel was wider/taller than the step (plots, video, custom panels all are).
// Uniform panels still read as a tidy grid; mixed sizes pack like masonry.
const FLOW_GAP = 24
const FLOW_X0 = 80
const FLOW_Y0 = 80
const FLOW_MAX_W = 1500 // wrap to a new row past this width (keeps rows readable)
let flowX = FLOW_X0
let flowY = FLOW_Y0
let flowRowH = 0
function nextPosition(w, h) {
  w = typeof w === 'number' ? w : 240
  h = typeof h === 'number' ? h : 96
  // Wrap to the next row when this panel wouldn't fit (but never wrap a panel
  // that's already at the row start, or a too-wide one would loop forever).
  if (flowX > FLOW_X0 && flowX + w > FLOW_X0 + FLOW_MAX_W) {
    flowX = FLOW_X0
    flowY += flowRowH + FLOW_GAP
    flowRowH = 0
  }
  const pos = { x: flowX, y: flowY }
  flowX += w + FLOW_GAP
  flowRowH = Math.max(flowRowH, h)
  return pos
}

// Shape ids (insertion order) of panels placed by the flow and not since moved
// or resized by the user. nextPosition() gives each a sane spot the instant it
// registers; relayoutFlow() then re-packs the whole set once the content-fit
// sizes have settled (see scheduleRelayout) — the registration-time size is the
// panel's *default*, before auto-width/height shrink it, so the first pass alone
// would leave big gaps. A user gesture drops the panel from the set (see
// setupGeometrySync), pinning it where they put it.
const flowItems = new Set()
let relayoutTimer = null

// Shortest-column masonry: balance the panels across columns by running height,
// placing each (in insertion order) into the currently-shortest column. The
// column width is the widest panel, so none overflows its column (no horizontal
// overlap); the column count derives from FLOW_MAX_W. Mixed heights pack tight
// like masonry instead of a tall panel reserving a tall row for its neighbours.
function packMasonry(items, { x0 = FLOW_X0, y0 = FLOW_Y0, gap = FLOW_GAP, maxRowW = FLOW_MAX_W } = {}) {
  const pos = new Map()
  if (!items.length) return pos
  const colW = Math.max(...items.map((it) => it.w))
  const cols = Math.max(1, Math.floor((maxRowW + gap) / (colW + gap)))
  const colH = new Array(cols).fill(y0)
  for (const it of items) {
    let j = 0
    for (let k = 1; k < cols; k++) if (colH[k] < colH[j] - 0.5) j = k
    pos.set(it.id, { x: x0 + j * (colW + gap), y: colH[j] })
    colH[j] += it.h + gap
  }
  return pos
}

// Re-pack every still-auto-placed panel by its *current* (post-fit) size. Runs
// as a remote change so it neither echoes back through the user-move handler nor
// re-drops the panels from the flow; the new positions are reported to Python so
// they persist across reconnects (mirrors nextPosition's pin).
function relayoutFlow() {
  relayoutTimer = null
  if (!editor || flowItems.size === 0) return
  const items = []
  for (const shapeId of flowItems) {
    const shape = editor.getShape(shapeId)
    if (!shape) { flowItems.delete(shapeId); continue }
    items.push({ id: shapeId, w: shape.props.w, h: shape.props.h })
  }
  if (!items.length) return
  const pos = packMasonry(items)
  const moves = []
  applyRemote(() => {
    for (const it of items) {
      const shape = editor.getShape(it.id)
      const p = pos.get(it.id)
      if (!shape || (Math.abs(shape.x - p.x) < 0.5 && Math.abs(shape.y - p.y) < 0.5)) continue
      editor.updateShape({ id: it.id, type: shape.type, x: p.x, y: p.y })
      moves.push({ id: componentIdOf(it.id), x: p.x, y: p.y })
    }
  })
  for (const m of moves) sendRaw({ type: 'layout', ...m })
}

// Debounced: a burst of content fits lands over a short window after load, so
// re-pack once they've settled rather than on every individual resize.
function scheduleRelayout() {
  if (relayoutTimer) clearTimeout(relayoutTimer)
  relayoutTimer = setTimeout(relayoutFlow, 150)
}

function resetFlow() {
  flowX = FLOW_X0
  flowY = FLOW_Y0
  flowRowH = 0
  flowItems.clear()
  if (relayoutTimer) { clearTimeout(relayoutTimer); relayoutTimer = null }
}

// component id <-> tldraw shape id helpers.
export function componentIdOf(shapeId) {
  return String(shapeId).replace(/^shape:/, '')
}

// The mounted editor, for overlays that need to map page<->screen coords.
export function getEditor() {
  return editor
}

export function setEditor(e) {
  editor = e
  setupGeometrySync(e)
  setupSelectionFilter(e)
  setupDrawSync(e)
  setupCursorReporting(e)
  // The view config usually arrives (in `welcome`) just after mount, but if it
  // was already known before this editor instance existed, apply it now.
  if (viewConfig) setViewConfig(viewConfig)
  connect()
}

// Run store mutations driven by Python as "remote" changes. The geometry-sync
// handler below only reacts to "user" changes, so this keeps our own updates
// (move/resize/register/load) from echoing straight back to Python.
//
// tldraw's high-level mutators (createShape/updateShape/deleteShapes/…) all
// no-op while the instance is read-only. But a read-only view is meant to stop
// the *viewer* drawing, not the host's Python-driven panels — without this lift,
// `view={read_only:True}` (or set_view(read_only=True, roles=...)) would silently
// drop every register/update and the canvas would render empty. So drop the flag
// for the duration of our own remote batch, then restore it; the toggle is plain
// instance state (not shape data), so it doesn't echo to Python or to peers.
function applyRemote(fn) {
  const wasReadonly = editor.getInstanceState().isReadonly
  if (wasReadonly) editor.updateInstanceState({ isReadonly: false })
  try {
    editor.store.mergeRemoteChanges(fn)
  } finally {
    if (wasReadonly) editor.updateInstanceState({ isReadonly: true })
  }
}

// Send any message to Python over the shared socket.
function sendRaw(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
}

// --- cursor reporting: stream this viewer's pointer to Python ----------------
// Off until the server enables it (welcome.cursors — gated to a private bind by
// default). When on, report the pointer in *page* coords (zoom/pan-independent,
// so Python's canvas.viewers[i].cursor lines up with panel x/y). Throttled to one
// send per animation frame and dead-banded to skip sub-pixel jitter, so a moving
// mouse can't flood the socket.
let cursorsEnabled = false
function setupCursorReporting(e) {
  const container = e.getContainer()
  let pending = false
  let lastClient = null
  let lastSent = null
  const flush = () => {
    pending = false
    if (!cursorsEnabled || !lastClient) return
    const p = e.screenToPage(lastClient)
    if (lastSent && Math.abs(p.x - lastSent.x) < 0.5 &&
        Math.abs(p.y - lastSent.y) < 0.5) return  // dead-band
    lastSent = p
    sendRaw({ type: 'cursor', x: p.x, y: p.y })
  }
  container.addEventListener('pointermove', (ev) => {
    if (!cursorsEnabled) return
    lastClient = { x: ev.clientX, y: ev.clientY }
    if (!pending) { pending = true; requestAnimationFrame(flush) }
  })
}
function setCursorsEnabled(on) { cursorsEnabled = !!on }

// --- peer cursors: render other viewers' pointers ----------------------------
// The server relays each viewer's cursor (with their id/name/colour) to the
// others; we cache the latest per viewer and notify subscribers (the overlay).
const peerCursors = new Map() // id -> { id, x, y, color, name }
const peerCursorListeners = new Set()

function emitPeerCursors() {
  const list = [...peerCursors.values()]
  for (const cb of peerCursorListeners) cb(list)
}
function setPeerCursor(c) {
  peerCursors.set(c.id, c)
  emitPeerCursors()
}
function removePeerCursor(id) {
  if (peerCursors.delete(id)) emitPeerCursors()
}
function clearPeerCursors() {
  if (peerCursors.size) { peerCursors.clear(); emitPeerCursors() }
}

export function subscribePeerCursors(cb) {
  peerCursorListeners.add(cb)
  cb([...peerCursors.values()]) // prime with the latest known set
  return () => peerCursorListeners.delete(cb)
}

// Reserved component id Python uses for the native-UI ephemeral Inspector
// (see Canvas._toggle_ui_inspector). The toolbar button tracks this shape's
// presence to reflect its open/closed state.
export const UI_INSPECTOR_ID = '__ui_inspector__'

// --- read-back: report user move/resize/rotate of panels to Python ----------
// Built at mount time (not module load) to avoid a temporal-dead-zone error:
// bridge.js and canvas.js import each other, so COMPONENT_TO_SHAPE isn't yet
// initialized while this module is first evaluated.
let PANEL_TYPES = null
const dirtyShapes = new Set()
let flushTimer = null

// tldraw shape ids (`shape:<id>`) of every pycanvas-managed panel and connector
// arrow. These are recreated from Python code, so they're excluded from the
// free-form drawing sync below — only the user's own shapes are relayed.
const managedIds = new Set()

function setupGeometrySync(ed) {
  PANEL_TYPES = new Set(Object.values(COMPONENT_TO_SHAPE))
  ed.sideEffects.registerAfterChangeHandler('shape', (prev, next, source) => {
    // Only user gestures; our own (remote) updates are ignored to avoid loops.
    if (source !== 'user') return
    if (!PANEL_TYPES.has(next.type)) return
    const moved =
      prev.x !== next.x ||
      prev.y !== next.y ||
      prev.rotation !== next.rotation ||
      prev.props.w !== next.props.w ||
      prev.props.h !== next.props.h
    if (!moved) return
    // A user gesture pins the panel: drop it from the auto-flow so a later
    // re-pack (another panel's fit settling) never yanks it from where they
    // dragged or sized it.
    flowItems.delete(next.id)
    // Debounce: a drag fires many changes; report the settled position once.
    dirtyShapes.add(next.id)
    if (flushTimer) clearTimeout(flushTimer)
    flushTimer = setTimeout(flushGeometry, 120)
  })
}

// Make `selectable=False` (meta.noGrab) mean what it says: the panel can never
// be hovered or selected by the user — not by body click, edge click, or
// marquee — so no highlight or selection box ever outlines it. Without this,
// tldraw hit-tests the shape's geometry directly (pointer events that miss the
// panel's interactive content land on the canvas underneath), so an "empty"
// region of a frameless panel would still hover-highlight and click-select.
// Implemented as a page-state filter rather than per-event handlers so every
// selection path (click, marquee, select-all) is covered. Python updates aren't
// affected — they never go through the editor's selection state.
function setupSelectionFilter(ed) {
  ed.sideEffects.registerBeforeChangeHandler('instance_page_state', (prev, next) => {
    const noGrab = (id) => {
      const shape = ed.getShape(id)
      return !!(shape && shape.meta && shape.meta.noGrab)
    }
    let out = next
    if (next.hoveredShapeId && next.hoveredShapeId !== prev.hoveredShapeId &&
        noGrab(next.hoveredShapeId)) {
      out = { ...out, hoveredShapeId: null }
    }
    if (next.selectedShapeIds !== prev.selectedShapeIds &&
        next.selectedShapeIds.some(noGrab)) {
      out = { ...out, selectedShapeIds: next.selectedShapeIds.filter((id) => !noGrab(id)) }
    }
    return out
  })
}

function flushGeometry() {
  flushTimer = null
  for (const shapeId of dirtyShapes) {
    const shape = editor.getShape(shapeId)
    if (!shape) continue
    sendRaw({
      type: 'layout',
      id: componentIdOf(shapeId),
      x: shape.x,
      y: shape.y,
      rotation: shape.rotation, // radians
      w: shape.props.w,
      h: shape.props.h,
    })
  }
  dirtyShapes.clear()
}

// --- free-form drawing sync: relay user shapes to the other browsers ---------
// pycanvas panels/arrows travel as register/update/arrow messages; everything
// *else* the user draws (pen, geo, text, notes, their own arrows, ...) is synced
// here as tldraw store diffs so every browser on this canvas sees the same ink.
// Only document-scope, user-originated records are watched; our own remote
// applies (mergeRemoteChanges) and pycanvas-managed shapes are filtered out, so
// neither echoes back into a loop.
const DRAW_TYPES = new Set(['shape', 'binding', 'asset'])

function isManaged(record) {
  if (record.typeName === 'shape') {
    // Panels match by type; pycanvas arrows are plain `arrow` shapes, so they
    // only match by id — hence the managedIds set rather than a type check.
    return managedIds.has(record.id) || (PANEL_TYPES && PANEL_TYPES.has(record.type))
  }
  if (record.typeName === 'binding') {
    // A binding belongs to pycanvas if either end is one of our shapes (this is
    // how connector-arrow bindings are recognised without tracking their ids).
    return managedIds.has(record.fromId) || managedIds.has(record.toId)
  }
  return false
}

// Keep only the non-managed records of a store diff; null if nothing is left.
function filterDiff(changes) {
  const added = {}
  const updated = {}
  const removed = {}
  let any = false
  for (const [id, rec] of Object.entries(changes.added || {})) {
    if (DRAW_TYPES.has(rec.typeName) && !isManaged(rec)) { added[id] = rec; any = true }
  }
  for (const [id, pair] of Object.entries(changes.updated || {})) {
    const next = pair[1]
    if (DRAW_TYPES.has(next.typeName) && !isManaged(next)) { updated[id] = pair; any = true }
  }
  for (const [id, rec] of Object.entries(changes.removed || {})) {
    if (DRAW_TYPES.has(rec.typeName) && !isManaged(rec)) { removed[id] = rec; any = true }
  }
  return any ? { added, updated, removed } : null
}

function setupDrawSync(ed) {
  ed.store.listen(
    ({ changes }) => {
      const diff = filterDiff(changes)
      if (diff) sendRaw({ type: 'draw', diff })
    },
    { source: 'user', scope: 'document' }
  )
}

// Apply a peer's (or the server replay's) free-form diff. Wrapped in
// mergeRemoteChanges so it's tagged `remote` and our own listener above doesn't
// rebroadcast it.
function applyDraw(diff) {
  if (!diff) return
  try {
    applyRemote(() => editor.store.applyDiff(diff))
  } catch (err) {
    console.error('[pycanvas] failed to apply remote drawing', err)
  }
}

let heartbeatTimer = null

// The backend run this page last talked to (from the welcome frame). Survives
// socket reconnects (module state lives as long as the page); a fresh page
// starts at null and adopts whatever run it first joins.
let lastRunId = null

// Per-tab viewer identity. Stored in sessionStorage (per-tab, not shared like
// localStorage — two tabs stay two viewers) and re-sent on every reconnect, so a
// background tab whose socket keeps flapping keeps one stable identity/name
// instead of churning a fresh animal name each time. Survives page reloads within
// the tab; a brand-new tab starts fresh.
const VIEWER_KEY = 'pc_viewer'
function loadStoredIdentity() {
  try { return JSON.parse(sessionStorage.getItem(VIEWER_KEY) || 'null') } catch { return null }
}
function persistIdentity(v) {
  try {
    if (v && v.id) {
      sessionStorage.setItem(VIEWER_KEY, JSON.stringify({ id: v.id, name: v.name, color: v.color }))
    }
  } catch { /* private mode / disabled storage — fall back to per-connection ids */ }
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  let url = `${proto}://${location.host}/ws`
  const me = loadStoredIdentity()
  if (me && me.id) {
    const q = new URLSearchParams({ vid: me.id })
    if (me.name) q.set('vname', me.name)
    if (me.color) q.set('vcolor', me.color)
    url += `?${q.toString()}`
  }
  ws = new WebSocket(url)
  // High-rate media (video) arrives as binary frames; take them as ArrayBuffers
  // so payloads go straight into a Blob with no base64/text decode.
  ws.binaryType = 'arraybuffer'

  ws.onopen = () => {
    // Periodic heartbeat so the server can tell a live (but idle) viewer from a
    // dead tab and keep the viewer count/roster accurate (the WS keepalive ping
    // is disabled server-side). 10s is comfortably under the server's timeout.
    if (heartbeatTimer) clearInterval(heartbeatTimer)
    heartbeatTimer = setInterval(() => sendRaw({ type: 'heartbeat' }), 10000)
  }

  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      handleBinary(ev.data)
      return
    }
    let msg
    try {
      msg = JSON.parse(ev.data)
    } catch {
      return
    }
    handle(msg)
  }

  ws.onclose = () => {
    // Server gone or restarting — retry so a reloaded backend reconnects.
    ws = null
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
    setTimeout(connect, 1000)
  }

  ws.onerror = () => {
    try {
      ws.close()
    } catch {
      // ignore
    }
  }
}

// Binary-frame type codes (BIN_VIDEO/AUDIO/CUSTOM/REACT) are imported at the top
// from ./protocol.generated.js, which is rendered from the server's canonical
// pycanvas/_protocol.py — so the two sides can't drift.
const frameDecoder = new TextDecoder()

// --- shared React assets (Python canvas.define / canvas.style) ---------------
// Component sources made available by name in every React panel's compile scope,
// plus a single global stylesheet injected once into <head>. Delivered by the
// server in a `shared` frame on connect (before panels register) and again when
// changed live. ReactHost reads getSharedComponents() and re-compiles when
// sharedVersion bumps, so a define() while serving updates the live panels.
let sharedComponents = {}   // name -> JSX source
let sharedVersion = 0
const sharedListeners = new Set()

export function getSharedComponents() {
  return sharedComponents
}
export function getSharedVersion() {
  return sharedVersion
}
export function subscribeShared(cb) {
  sharedListeners.add(cb)
  return () => sharedListeners.delete(cb)
}

function applyShared(msg) {
  sharedComponents = msg.components || {}
  // One global <style> shared by every panel (vs a panel's own css=, which is
  // rendered inside that one panel). Replace its text so re-sends don't stack.
  let el = document.getElementById('pc-shared-styles')
  if (!el) {
    el = document.createElement('style')
    el.id = 'pc-shared-styles'
    document.head.appendChild(el)
  }
  el.textContent = msg.styles || ''
  sharedVersion += 1
  for (const cb of sharedListeners) cb(sharedVersion)
}

// Decode a binary frame — `[type][idLen][id bytes][payload]` — and route its
// raw payload (an ArrayBuffer) to the matching component's live handler, the
// same channel LivePlot/Custom use. Dropped if the panel isn't mounted. Video
// (JPEG) and audio (int16 PCM) share this path; the handler interprets the bytes.
function handleBinary(buf) {
  // Guard against a truncated/malformed frame so a bad packet can't throw out
  // of onmessage: need the 2-byte header plus the declared id length.
  if (buf.byteLength < 2) return
  const head = new Uint8Array(buf, 0, 2)
  const type = head[0]
  const idLen = head[1]
  if (buf.byteLength < 2 + idLen) return
  const id = frameDecoder.decode(new Uint8Array(buf, 2, idLen))
  const payload = buf.slice(2 + idLen) // ArrayBuffer of the media bytes
  // Video (JPEG) / audio (int16 PCM) feed their decoders; a Custom panel's
  // push_binary lands on the same liveHandler as its JSON push(), forwarding the
  // ArrayBuffer straight into the iframe (canvas.onPush receives it untouched).
  // React.push_binary is the native equivalent: the same liveHandler is the one
  // ReactHost registered, so the ArrayBuffer reaches its canvas.onFrame untouched.
  if (type === BIN_VIDEO || type === BIN_AUDIO || type === BIN_CUSTOM || type === BIN_REACT) {
    const handler = liveHandlers.get(id)
    if (handler) handler(payload)
  }
}

function handle(msg) {
  if (!editor || !msg || !msg.type) return
  if (msg.type === 'register') {
    registerComponent(msg)
  } else if (msg.type === 'arrow') {
    createArrow(msg)
  } else if (msg.type === 'update') {
    updateComponent(msg.id, msg.payload || {})
  } else if (msg.type === 'order') {
    reorderComponent(msg.id, msg.op)
  } else if (msg.type === 'remove') {
    removeComponent(msg.id)
  } else if (msg.type === 'get_snapshot') {
    // Python is asking for the user's free-form drawings only — pycanvas panels
    // and connector arrows are recreated from code, not persisted.
    sendRaw({ type: 'snapshot', reqId: msg.reqId, data: userContent(msg.panelIds || []) })
  } else if (msg.type === 'load_snapshot') {
    loadSnapshot(msg.data)
    scheduleInitialFit()
  } else if (msg.type === 'draw') {
    applyDraw(msg.diff)
  } else if (msg.type === 'presence') {
    setPresence(msg.count || 0)
    setRoster(msg.viewers || [])
  } else if (msg.type === 'cursor') {
    setPeerCursor(msg)            // a peer moved — render their cursor
  } else if (msg.type === 'cursor_gone') {
    removePeerCursor(msg.id)      // a peer left — drop their cursor
  } else if (msg.type === 'view') {
    applyLiveView(msg.view || {})
  } else if (msg.type === 'welcome') {
    // The socket reconnected without the page reloading — if the backend is a
    // *different run* (re-run script, crash + restart, hot reload), the previous
    // run's panels are still on the canvas. Panel ids change every run, so the
    // new ones would appear *alongside* (stacked on top of) the stale, dead
    // ones. Detect the run change via the welcome runId and drop the managed
    // shapes first; the server replays this run's panels right after.
    // (msg.reload is the older hot-reload-only signal, kept for compatibility.)
    if (msg.reload || (lastRunId !== null && msg.runId && msg.runId !== lastRunId)) {
      console.info('[pycanvas] backend is a new run; clearing the previous run\'s panels')
      clearManaged()
      clearPeerCursors()   // stale peers from the previous run shouldn't linger
    }
    if (msg.runId) lastRunId = msg.runId
    setIdentity(msg.you || null)
    setUiInspectorEnabled(!!msg.uiInspector)
    setAuthEnabled(!!msg.auth)
    setCursorsEnabled(!!msg.cursors)
    setViewConfig(msg.view || null)
  } else if (msg.type === 'shared') {
    applyShared(msg)              // canvas.define / canvas.style assets
  } else if (msg.type === 'chat') {
    pushChat(msg)
  } else if (msg.type === 'complete_result') {
    resolveCompletion(msg.reqId, msg.completions)
  } else if (msg.type === 'response') {
    resolveRequest(msg.reqId, msg.result, msg.error)
  }
}

// --- editor autocomplete round-trip (used by the Repl's Monaco editor) -------
// A request carries a reqId; Python answers with a `complete_result` carrying
// the same id. Pending requests resolve their promise when the answer lands (or
// after a short timeout, so a stuck/busy backend never hangs the editor).
const pendingCompletions = new Map() // reqId -> { resolve, timer }
let completionSeq = 0

export function requestCompletions(id, text) {
  return new Promise((resolve) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      resolve([])
      return
    }
    const reqId = `cmp${++completionSeq}`
    const timer = setTimeout(() => {
      pendingCompletions.delete(reqId)
      resolve([])
    }, 1500)
    pendingCompletions.set(reqId, { resolve, timer })
    sendRaw({ type: 'input', id, payload: { action: 'complete', reqId, text } })
  })
}

function resolveCompletion(reqId, completions) {
  const pending = pendingCompletions.get(reqId)
  if (!pending) return
  clearTimeout(pending.timer)
  pendingCompletions.delete(reqId)
  pending.resolve(completions || [])
}

// --- request/response RPC (canvas.request from a React panel) ----------------
// The awaitable twin of sendInput: a panel asks Python a question and gets the
// matching @on_request handler's return value back. Generalises the completions
// round-trip and component-routes it — { type:'request', id, reqId, data } gets a
// { type:'response', reqId, result|error } back, correlated by reqId. reqId is
// namespaced by a per-tab nonce so a broadcast response never resolves another
// tab's pending request. The Promise rejects on a handler error or timeout.
const pendingRequests = new Map() // reqId -> { resolve, reject, timer }
const REQUEST_NONCE = Math.random().toString(36).slice(2)
let requestSeq = 0

export function requestData(id, data, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      reject(new Error('not connected'))
      return
    }
    const reqId = `${REQUEST_NONCE}:${++requestSeq}`
    const timer = setTimeout(() => {
      pendingRequests.delete(reqId)
      reject(new Error('canvas.request timed out'))
    }, timeoutMs)
    pendingRequests.set(reqId, { resolve, reject, timer })
    sendRaw({ type: 'request', id, reqId, data })
  })
}

function resolveRequest(reqId, result, error) {
  const pending = pendingRequests.get(reqId)
  if (!pending) return
  clearTimeout(pending.timer)
  pendingRequests.delete(reqId)
  if (error !== undefined && error !== null) pending.reject(new Error(error))
  else pending.resolve(result)
}

// Collect tldraw "content" for everything on the page except the pycanvas
// panels/arrows. `panelIds` are the shape ids Python owns; we also drop any
// shape whose type is a panel type, as a belt-and-suspenders guard.
function userContent(panelIds) {
  const exclude = new Set(panelIds)
  const ids = [...editor.getCurrentPageShapeIds()].filter((id) => {
    if (exclude.has(id)) return false
    const shape = editor.getShape(id)
    return shape && !(PANEL_TYPES && PANEL_TYPES.has(shape.type))
  })
  if (ids.length === 0) return null
  // getContentFromCurrentPage bundles the shapes plus their bindings/assets.
  return editor.getContentFromCurrentPage(ids) || null
}

// Merge saved user drawings onto the current page, on top of the live panels.
// Unlike a full loadSnapshot this is additive, so code-created panels survive.
// preserveIds:false means repeated loads/reconnects don't collide; the saved
// positions are kept so drawings land where they were relative to the panels.
function loadSnapshot(data) {
  if (!data) return
  if (data.document) {
    console.warn('[pycanvas] ignoring an old full-canvas file; re-save with this version')
    return
  }
  try {
    applyRemote(() =>
      editor.putContentOntoCurrentPage(data, { select: false, preservePosition: true })
    )
  } catch (err) {
    console.error('[pycanvas] failed to load drawings', err)
  }
}

// Drop every pycanvas-managed shape (panels + connector arrows) and its live
// wiring. Used on a hot-reload reconnect: the new backend run replays the full
// panel set right after, so this clears the previous run's shapes (whose ids no
// longer match anything) to stop them lingering. User free-form drawings aren't
// managed, so they're untouched. Deleting a panel also cascades its arrow
// bindings, so arrows go cleanly even though they're deleted in the same pass.
function clearManaged() {
  if (!editor) return
  const ids = [...managedIds]
  applyRemote(() => {
    for (const shapeId of ids) {
      if (editor.getShape(shapeId)) editor.deleteShape(shapeId)
    }
  })
  managedIds.clear()
  liveHandlers.clear()
  liveBuffer.clear()
  setUiInspectorOpen(false)
  // Rewind the auto-placement flow so panels without an explicit x/y land in
  // the same spots after the reload as before it. The server replays components
  // in stable insertion order, so from the start the same panels get the same
  // slots; without this the cursor keeps advancing and they drift each reload.
  resetFlow()
}

function removeComponent(id) {
  if (id === UI_INSPECTOR_ID) setUiInspectorOpen(false)
  // Drop any live-data wiring (LivePlot) so its buffer doesn't leak.
  liveHandlers.delete(id)
  liveBuffer.delete(id)
  const shapeId = createShapeId(id)
  managedIds.delete(shapeId)
  if (editor.getShape(shapeId)) applyRemote(() => editor.deleteShape(shapeId))
}

// Build a shape `meta` object from the Python movable/resizable/interactive
// flags, merging onto any existing meta so a partial update doesn't clobber the
// others. `lockInput` blocks the user from touching the panel's controls while
// the shape stays *unlocked*, so programmatic value updates still render (unlike
// the top-level isLocked, which tldraw also refuses prop updates to).
function lockMeta(base, movable, resizable, interactive, selectable, frame) {
  const meta = { ...(base || {}) }
  if (typeof movable === 'boolean') meta.lockMove = !movable
  if (typeof resizable === 'boolean') meta.lockResize = !resizable
  if (typeof interactive === 'boolean') meta.lockInput = !interactive
  // noGrab drops the click-to-select cover on content-heavy panels: their
  // content is hover/click-live immediately and a body click never selects.
  if (typeof selectable === 'boolean') meta.noGrab = !selectable
  // noFrame strips the card chrome (background/border/shadow/label) and the
  // hover/selection indicator, so the content sits bare on the canvas.
  if (typeof frame === 'boolean') meta.noFrame = !frame
  return meta
}

function registerComponent({ id, component, props = {}, x, y, rotation, locked, movable, resizable, interactive, selectable, frame }) {
  const shapeType = COMPONENT_TO_SHAPE[component]
  if (!shapeType) return

  if (id === UI_INSPECTOR_ID) setUiInspectorOpen(true)
  const shapeId = createShapeId(id)
  managedIds.add(shapeId) // exclude from free-form drawing sync
  if (editor.getShape(shapeId)) return // already on canvas (reconnect)

  // Use the position Python supplied; cascade only the axes left unspecified.
  let px = x
  let py = y
  const autoPlaced = typeof px !== 'number' || typeof py !== 'number'
  if (autoPlaced) {
    const auto = nextPosition(props.w, props.h)
    if (typeof px !== 'number') px = auto.x
    if (typeof py !== 'number') py = auto.y
  }
  // Pass through only known props (incl. optional w/h); getDefaultProps fills the rest.
  const shape = {
    id: shapeId,
    type: shapeType,
    x: px,
    y: py,
    props: { ...props },
  }
  if (typeof rotation === 'number') shape.rotation = rotation // radians
  if (typeof locked === 'boolean') shape.isLocked = locked
  if (typeof movable === 'boolean' || typeof resizable === 'boolean' ||
      typeof interactive === 'boolean' || typeof selectable === 'boolean' ||
      typeof frame === 'boolean') {
    shape.meta = lockMeta({}, movable, resizable, interactive, selectable, frame)
  }
  applyRemote(() => editor.createShape(shape))
  // Record an auto-assigned position back to Python so the panel keeps it on the
  // next viewer/reconnect instead of being re-flowed. Without this, a panel that
  // was placed by the masonry flow stays x=None in Python; once *other* panels
  // get a concrete position (a user move, or an auto-height fit), they skip the
  // flow on reconnect while this one re-flows from the origin — and they collide.
  // Pinning every auto-placed panel the same way keeps positions stable for all.
  if (autoPlaced) {
    sendRaw({ type: 'layout', id, x: px, y: py })
    // Track it for the masonry re-pack once its content-fit size settles.
    flowItems.add(shapeId)
    scheduleRelayout()
  }
  // Frame this (and every other just-registered) panel on first load when no
  // explicit camera was configured. Debounced, so a burst of registers fits the
  // whole set once rather than zooming in on the first one.
  scheduleInitialFit()
}

// Draw a tldraw arrow bound to two existing panels. The bindings make the
// arrow reroute automatically as the panels move or resize. `props` carries any
// tldraw arrow props (color, dash, size, text, bend, arrowheadStart/End, ...);
// later changes arrive as normal `update` messages and patch these props.
function createArrow({ id, start, end, props = {} }) {
  const arrowId = createShapeId(id)
  managedIds.add(arrowId) // exclude from free-form drawing sync
  if (editor.getShape(arrowId)) return // already on canvas (reconnect)
  applyRemote(() => {
    editor.createShape({ id: arrowId, type: 'arrow', props: { ...props } })
    editor.createBindings(
      ['start', 'end'].map((terminal) => ({
        id: createBindingId(),
        fromId: arrowId,
        toId: createShapeId(terminal === 'start' ? start : end),
        type: 'arrow',
        props: {
          terminal,
          normalizedAnchor: { x: 0.5, y: 0.5 },
          isExact: false,
          isPrecise: false,
        },
      }))
    )
  })
}

function updateComponent(id, payload) {
  // Live telemetry (LivePlot) bypasses the tldraw store: the data is buffered
  // here and pushed straight to the mounted Plotly node, so high-frequency
  // updates don't pollute shape props / undo history.
  if (payload && payload.plot) {
    liveBuffer.set(id, payload.plot)
    const handler = liveHandlers.get(id)
    if (handler) handler(payload.plot)
    return
  }

  // LivePlot streaming delta: append the new point(s) rather than re-sending the
  // whole figure (see LivePlot.push). We keep the buffered full figure current
  // too — independent of whether the node is mounted — so a panel that unmounts
  // and later remounts (tldraw viewport culling) re-renders with every point,
  // not just those since the last full frame. The mounted node is grown with
  // Plotly.extendTraces via the handler.
  if (payload && payload.plot_extend) {
    const ext = payload.plot_extend
    const fig = liveBuffer.get(id)
    if (!fig || !fig.data) return // no base figure yet; a full frame will seed it
    ext.indices.forEach((ti, k) => {
      const tr = fig.data[ti]
      if (!tr) return
      tr.x = (tr.x || []).concat(ext.x[k])
      tr.y = (tr.y || []).concat(ext.y[k])
      if (ext.max && tr.x.length > ext.max) {
        tr.x = tr.x.slice(-ext.max)
        tr.y = tr.y.slice(-ext.max)
      }
    })
    const handler = liveHandlers.get(id)
    if (handler) handler({ __extend: ext })
    return
  }

  // Custom panels: `push()` data is forwarded straight into the iframe (see
  // CustomView) instead of touching shape props, so streaming doesn't reload the
  // frame. Dropped if the panel isn't mounted yet (the next push will land).
  if (payload && payload.post !== undefined) {
    const handler = liveHandlers.get(id)
    if (handler) handler(payload.post)
    return
  }

  // AudioFeed chunks no longer travel here — they ride a binary frame straight
  // to the Web Audio scheduler (see handleBinary / AudioView).

  const shapeId = createShapeId(id)
  const shape = editor.getShape(shapeId)
  if (!shape) return
  // x/y/rotation are top-level shape fields, not props; everything else
  // (incl. w/h) is a shape prop. Split them so live move/resize/rotate works.
  const { x, y, rotation, locked, movable, resizable, interactive, selectable, frame, ...props } = payload
  const patch = { id: shapeId, type: shape.type, props: { ...props } }
  if (typeof x === 'number') patch.x = x
  if (typeof y === 'number') patch.y = y
  if (typeof rotation === 'number') patch.rotation = rotation
  if (typeof locked === 'boolean') patch.isLocked = locked
  if (typeof movable === 'boolean' || typeof resizable === 'boolean' ||
      typeof interactive === 'boolean' || typeof selectable === 'boolean' ||
      typeof frame === 'boolean') {
    patch.meta = lockMeta(shape.meta, movable, resizable, interactive, selectable, frame)
  }
  applyRemote(() => editor.updateShape(patch))
}

// Change a managed panel's stacking order (Python to_front/to_back/forward/
// backward). tldraw owns the fractional z-index; we just invoke its reorder ops
// as a remote change so the move doesn't echo back to Python as a user edit.
// `front`/`back` jump past everything; `forward`/`backward` step one place,
// respecting tldraw's overlap-aware ordering.
function reorderComponent(id, op) {
  if (!editor) return
  const shapeId = createShapeId(id)
  if (!editor.getShape(shapeId)) return
  const ids = [shapeId]
  applyRemote(() => {
    if (op === 'front') editor.bringToFront(ids)
    else if (op === 'back') editor.sendToBack(ids)
    else if (op === 'forward') editor.bringForward(ids)
    else if (op === 'backward') editor.sendBackward(ids)
  })
}

// --- live-data side channel (used by LivePlot shapes) -----------------------
const liveHandlers = new Map() // componentId -> (plot) => void
const liveBuffer = new Map() // componentId -> last plot payload

export function registerLive(id, handler) {
  liveHandlers.set(id, handler)
  // Render immediately if data arrived before the node mounted.
  if (liveBuffer.has(id)) handler(liveBuffer.get(id))
}

export function unregisterLive(id) {
  liveHandlers.delete(id)
}

// Browser -> Python: user input from a component (slider move, etc.).
export function sendInput(id, payload) {
  sendRaw({ type: 'input', id, payload })
}

// --- presence: how many browsers are connected to this canvas ---------------
// The server broadcasts a `presence` count on every join/leave; UI subscribes
// here so the live-viewer badge updates without prop-drilling through tldraw.
let presenceCount = 0
const presenceListeners = new Set()

function setPresence(n) {
  presenceCount = n
  for (const cb of presenceListeners) cb(n)
}

export function subscribePresence(cb) {
  presenceListeners.add(cb)
  cb(presenceCount) // prime with the latest known count
  return () => presenceListeners.delete(cb)
}

// --- native UI Inspector toggle ---------------------------------------------
// The server advertises (in `welcome`) whether this canvas permits spawning an
// ephemeral Inspector from the toolbar; `open` tracks whether one is currently
// on the canvas (by watching register/remove of UI_INSPECTOR_ID). The button
// subscribes here and calls toggleUiInspector() to flip it.
let uiInspector = { enabled: false, open: false }
const uiInspectorListeners = new Set()

function emitUiInspector() {
  for (const cb of uiInspectorListeners) cb(uiInspector)
}
function setUiInspectorEnabled(enabled) {
  uiInspector = { ...uiInspector, enabled }
  emitUiInspector()
}
function setUiInspectorOpen(open) {
  uiInspector = { ...uiInspector, open }
  emitUiInspector()
}

export function subscribeUiInspector(cb) {
  uiInspectorListeners.add(cb)
  cb(uiInspector) // prime with the latest known state
  return () => uiInspectorListeners.delete(cb)
}

// Whether this canvas is password-protected (welcome.auth). When true the app
// shows a sign-out button that navigates to /__logout__ (the server clears the
// session cookie and the password page returns). Shown regardless of a `ui:false`
// kiosk view — signing out is an auth escape hatch, not app chrome — so even a
// chrome-free viewer can switch accounts.
let authEnabled = false
const authListeners = new Set()

function setAuthEnabled(enabled) {
  authEnabled = enabled
  for (const cb of authListeners) cb(authEnabled)
}

export function subscribeAuth(cb) {
  authListeners.add(cb)
  cb(authEnabled) // prime with the latest known state
  return () => authListeners.delete(cb)
}

export function signOut() {
  // Full-page nav: the server clears the httponly cookie and redirects to the
  // login page. (A plain link would do, but this keeps the call site declarative.)
  window.location.href = '/__logout__'
}

export function toggleUiInspector() {
  sendRaw({ type: 'ui', action: 'toggle_inspector' })
}

// --- viewport / navigation config (set from Python via serve(view=...)) ------
// The server sends a `view` dict in `welcome`: initial camera, zoom limits,
// pan/zoom lock, and UI-chrome/grid/read-only flags. Camera + instance state are
// applied straight to the editor here; App subscribes for the `ui` flag (which
// is a <Tldraw hideUi> prop, set before render, not on the editor). The initial
// camera is applied only once per page so a viewer who pans away isn't yanked
// back when the same config replays on a reconnect.
let viewConfig = null
let initialCameraApplied = false
const viewConfigListeners = new Set()

// Initial config from `welcome`: replace what we know, notify subscribers (the
// `ui` flag), apply the non-camera options, and place the camera once. The
// once-guard is module-level so a reconnect's `welcome` replay doesn't yank a
// viewer who has since panned away back to the configured start.
function setViewConfig(view) {
  viewConfig = view
  for (const cb of viewConfigListeners) cb(view)
  if (!editor || !viewConfig) return
  applyViewOptions()
  if (!initialCameraApplied) {
    applyCameraFrom(viewConfig)
    initialCameraApplied = true
  }
}

// A live `view` change from Python (Canvas.set_view): merge the delta over the
// current config, notify, re-apply options, and move the camera *only* if the
// delta carried x/y/zoom — so toggling, say, `ui` or `grid` live never disturbs
// where the viewer is looking.
function applyLiveView(delta) {
  viewConfig = { ...(viewConfig || {}), ...delta }
  for (const cb of viewConfigListeners) cb(viewConfig)
  if (!editor) return
  applyViewOptions()
  applyCameraFrom(delta)
}

export function subscribeViewConfig(cb) {
  viewConfigListeners.add(cb)
  cb(viewConfig) // prime with the latest known config
  return () => viewConfigListeners.delete(cb)
}

// Apply zoom limits + pan/zoom lock + grid/read-only from the merged config.
// Idempotent, so it's safe to call on first load and on every live change.
function applyViewOptions() {
  const v = viewConfig
  if (!v) return
  // zoomSteps' first/last bound the zoom range, so weave any caller min/max
  // around tldraw's default stops. Partial<options>: unspecified fields keep
  // their defaults.
  const opts = {}
  if (typeof v.locked === 'boolean') opts.isLocked = v.locked
  if (typeof v.min_zoom === 'number' || typeof v.max_zoom === 'number') {
    const min = typeof v.min_zoom === 'number' ? v.min_zoom : 0.1
    const max = typeof v.max_zoom === 'number' ? v.max_zoom : 8
    const mids = [0.25, 0.5, 1, 2, 4].filter((z) => z > min && z < max)
    opts.zoomSteps = [min, ...mids, max]
  }
  if (Object.keys(opts).length) editor.setCameraOptions(opts)

  // Read-only / grid are instance state, not camera options.
  const inst = {}
  if (typeof v.read_only === 'boolean') inst.isReadonly = v.read_only
  if (typeof v.grid === 'boolean') inst.isGridMode = v.grid
  if (Object.keys(inst).length) editor.updateInstanceState(inst)
}

// --- initial auto-fit --------------------------------------------------------
// When no explicit camera (x/y/zoom) is configured, frame every panel this
// viewer can see, centred, on first load — so the canvas never opens on empty
// space (e.g. because tldraw restored a panned camera from this browser's
// localStorage, or because the panels live far from the origin). Runs once per
// page load and only over the shapes actually present, which already reflects
// role filtering: a viewer who isn't sent a panel simply isn't fit to it.
let initialFitDone = false
let initialFitTimer = null

// (Re)arm the one-shot fit. Each newly-registered panel calls this; the debounce
// lets a burst of register messages settle so we fit the whole set at once
// instead of zooming in on the first panel. A configured camera, or a fit that
// already ran, cancels it.
function scheduleInitialFit() {
  if (initialFitDone || !editor) return
  const v = viewConfig
  // An explicit camera wins — respect serve(view={x/y/zoom}) / set_view.
  if (v && (typeof v.x === 'number' || typeof v.y === 'number' ||
            typeof v.zoom === 'number')) return
  clearTimeout(initialFitTimer)
  initialFitTimer = setTimeout(runInitialFit, 180)
}

function runInitialFit() {
  if (initialFitDone || !editor) return
  const bounds = editor.getCurrentPageBounds() // union of all shapes, or null
  if (!bounds) return // no panels yet; a later register reschedules
  initialFitDone = true
  fitCameraToBounds(bounds)
}

// Centre the camera on `bounds` and zoom so it fits the viewport with a margin,
// honouring any configured min/max zoom. Uses the same screen-centre math as
// applyCameraFrom, and `force` so it lands even under a locked camera.
function fitCameraToBounds(bounds) {
  const vsb = editor.getViewportScreenBounds()
  const pad = 80 // px of breathing room around the panels on every side
  const fitW = Math.max(1, vsb.w - pad * 2)
  const fitH = Math.max(1, vsb.h - pad * 2)
  // Zoom out to fit when the panels overflow the viewport, but never zoom *in*
  // past 100% — a single small panel should sit centred at its natural size, not
  // blown up to fill the screen. Configured min/max zoom still bound the result.
  let z = Math.min(fitW / bounds.w, fitH / bounds.h, 1)
  const v = viewConfig || {}
  const minZ = typeof v.min_zoom === 'number' ? v.min_zoom : 0.1
  const maxZ = typeof v.max_zoom === 'number' ? v.max_zoom : 8
  z = Math.max(minZ, Math.min(maxZ, z))
  const cx = bounds.x + bounds.w / 2
  const cy = bounds.y + bounds.h / 2
  editor.setCamera(
    { x: vsb.w / (2 * z) - cx, y: vsb.h / (2 * z) - cy, z },
    { immediate: true, force: true }
  )
}

// Centre the view on a canvas point at a given zoom, taking each of x/y/zoom
// from `src` and leaving the rest at the current camera. No-op if `src` has none
// of them. Derived from tldraw's own camera math: the page point at screen
// centre is `-camera.x + vsb.w/z/2`, so to put (x, y) there we invert that.
// `force` overrides a locked camera (lock is applied separately), `immediate`
// skips the animation. setCameraOptions may clamp zoom, so call this after it.
export function applyCameraFrom(src) {
  const hasX = typeof src.x === 'number'
  const hasY = typeof src.y === 'number'
  const hasZ = typeof src.zoom === 'number'
  if (!(hasX || hasY || hasZ)) return
  const cur = editor.getViewportPageBounds().center
  const z = hasZ ? src.zoom : editor.getZoomLevel()
  const x = hasX ? src.x : cur.x
  const y = hasY ? src.y : cur.y
  const vsb = editor.getViewportScreenBounds()
  editor.setCamera(
    { x: vsb.w / (2 * z) - x, y: vsb.h / (2 * z) - y, z },
    { immediate: true, force: true }
  )
}

// --- viewer identity, roster, and chat --------------------------------------
// The server assigns each connection an identity (id/name/color), keeps a live
// roster, and relays chat. Components subscribe here; chat history that arrives
// before a Chat panel mounts is retained in `chatLog` so the panel can backfill.
let myViewer = null
let roster = []
const chatLog = []
const identityListeners = new Set()
const rosterListeners = new Set()
const chatListeners = new Set()

function setIdentity(v) {
  myViewer = v
  persistIdentity(v)   // remember across this tab's reconnects/reloads
  for (const cb of identityListeners) cb(v)
}

function setRoster(vs) {
  roster = vs
  // Keep our own identity in step with the server's record (e.g. after a
  // rename), since `welcome` is only sent once at connect.
  if (myViewer) {
    const mine = vs.find((v) => v.id === myViewer.id)
    if (mine && (mine.name !== myViewer.name || mine.color !== myViewer.color)) {
      setIdentity({ ...myViewer, ...mine })
    }
  }
  for (const cb of rosterListeners) cb(vs)
}

function pushChat(entry) {
  chatLog.push(entry)
  if (chatLog.length > 300) chatLog.shift()
  for (const cb of chatListeners) cb(entry)
}

export function subscribeIdentity(cb) {
  identityListeners.add(cb)
  cb(myViewer)
  return () => identityListeners.delete(cb)
}

export function subscribeRoster(cb) {
  rosterListeners.add(cb)
  cb(roster)
  return () => rosterListeners.delete(cb)
}

// New chat entries only (after subscription). Use getChatLog() to backfill.
export function subscribeChat(cb) {
  chatListeners.add(cb)
  return () => chatListeners.delete(cb)
}

export function getChatLog() {
  return chatLog
}

export function sendChat(text) {
  sendRaw({ type: 'chat', text })
}

export function setMyName(name) {
  sendRaw({ type: 'set_name', name })
}

// Zoom the canvas at a screen point, the way tldraw's own Ctrl+wheel does.
// Used to honour a zoom gesture that happened *inside* an iframe panel (which
// tldraw can't see): the iframe forwards the wheel delta + cursor, and we apply
// it to the camera here so it matches scrolling over the bare canvas.
//
// Camera math mirrors applyCameraFrom: to keep a page point P fixed under the
// cursor at viewport-relative screen point S, camera = S/z - P, so after a zoom
// z0 -> z1 the camera shifts by S*(1/z1 - 1/z0). Screen coords are made
// viewport-relative (minus the viewport's screen origin) first.
function zoomCanvasAtClient(clientX, clientY, deltaY) {
  if (!editor) return
  if (viewConfig && viewConfig.locked) return // a locked camera doesn't zoom
  const cam = editor.getCamera()
  const vsb = editor.getViewportScreenBounds()
  const sx = clientX - vsb.x
  const sy = clientY - vsb.y
  const min = viewConfig && typeof viewConfig.min_zoom === 'number' ? viewConfig.min_zoom : 0.1
  const max = viewConfig && typeof viewConfig.max_zoom === 'number' ? viewConfig.max_zoom : 8
  const z0 = cam.z
  // Exponential so a trackpad's many small deltas and a mouse's coarse notches
  // both feel right; sign matches wheel (up/negative deltaY zooms in).
  const z1 = Math.min(max, Math.max(min, z0 * Math.exp(-deltaY * 0.0015)))
  if (z1 === z0) return
  const k = 1 / z1 - 1 / z0
  editor.setCamera({ x: cam.x + sx * k, y: cam.y + sy * k, z: z1 }, { immediate: true })
}

// Map an iframe's own (clientX, clientY) into the parent viewport using the
// iframe element's position, then zoom there. The message's `source` is the
// iframe's contentWindow, which we match back to its <iframe> element.
function zoomFromIframe(sourceWin, w) {
  const iframe = [...document.querySelectorAll('iframe')].find(
    (f) => f.contentWindow === sourceWin
  )
  if (!iframe) return
  const rect = iframe.getBoundingClientRect()
  zoomCanvasAtClient(rect.left + w.x, rect.top + w.y, w.d)
}

// Auto-height (`h="auto"` on Custom/Markdown panels): the iframe measures its
// own document and posts the content height; resize the shape to fit and report
// the new geometry to Python — same read-back path as a user resize, so
// `comp.h` stays in sync. The card chrome around the iframe (header, padding)
// is measured via offsetHeight, which is in layout px (CSS transforms don't
// affect it), i.e. already in shape units.
function fitFromIframe(sourceWin, fit) {
  if (!editor) return
  const iframe = [...document.querySelectorAll('iframe')].find(
    (f) => f.contentWindow === sourceWin
  )
  if (!iframe) return
  const shapeId = createShapeId(fit.id)
  const shape = editor.getShape(shapeId)
  if (!shape) return
  // A fit may carry a height (h="auto", measured continuously), a width
  // (w="auto", a one-shot at load), or both. Apply each axis independently;
  // the card chrome around the iframe (header/padding) is its overhead in that
  // axis, measured via offset* in layout px (already in shape units).
  const props = {}
  const report = { type: 'layout', id: fit.id }
  if (typeof fit.h === 'number') {
    const overhead = Math.max(0, shape.props.h - iframe.offsetHeight)
    const h = Math.max(40, Math.ceil(fit.h + overhead))
    if (Math.abs(h - shape.props.h) >= 3) { props.h = h; report.h = h } // else settled
  }
  if (typeof fit.w === 'number') {
    const overhead = Math.max(0, shape.props.w - iframe.offsetWidth)
    const w = Math.max(40, Math.ceil(fit.w + overhead))
    if (Math.abs(w - shape.props.w) >= 3) { props.w = w; report.w = w } // else settled
  }
  if (!props.h && !props.w) return // both settled — don't ping-pong
  applyRemote(() =>
    editor.updateShape({ id: shapeId, type: shape.type, props })
  )
  // Report only the size: a fit never moves the panel, and echoing x/y would
  // pin an auto-arranged panel (x=None in Python) to a number — which then makes
  // it skip the placement flow on the next viewer and collide with others.
  sendRaw(report)
  // The panel's footprint changed — re-pack the auto-flow around its real size.
  if (flowItems.has(shapeId)) scheduleRelayout()
}

// Content-fit for native panels (the React `h="auto"` / `w="auto"` path). The
// native twin of fitFromIframe: where that reads an iframe's document size over
// postMessage, here the panel is a native React subtree, so ReactHost measures
// its content directly and calls this with `fit = { h?, w? }` and the host
// element that fills the card's body. Overhead (card header/padding) is
// `shape.<axis> - hostEl.offset<Axis>`, in layout px — the same read-back path
// as a user resize, keeping `comp.w`/`comp.h` in sync. Each axis is applied
// independently; pass only the axis the panel is fitting.
export function fitNative(componentId, hostEl, fit) {
  if (!editor || !hostEl || !fit) return
  const shapeId = createShapeId(componentId)
  const shape = editor.getShape(shapeId)
  if (!shape) return
  const props = {}
  const report = { type: 'layout', id: componentId }
  if (typeof fit.h === 'number') {
    const overhead = Math.max(0, shape.props.h - hostEl.offsetHeight)
    const h = Math.max(40, Math.ceil(fit.h + overhead))
    if (Math.abs(h - shape.props.h) >= 3) { props.h = h; report.h = h } // else settled
  }
  if (typeof fit.w === 'number') {
    const overhead = Math.max(0, shape.props.w - hostEl.offsetWidth)
    const w = Math.max(40, Math.ceil(fit.w + overhead))
    if (Math.abs(w - shape.props.w) >= 3) { props.w = w; report.w = w } // else settled
  }
  if (props.h === undefined && props.w === undefined) return // both settled — don't ping-pong
  applyRemote(() =>
    editor.updateShape({ id: shapeId, type: shape.type, props })
  )
  // Size only — see fitFromIframe: echoing x/y would pin an auto-arranged panel
  // and break the placement flow for the next viewer.
  sendRaw(report)
  // The panel grew/shrank — re-pack the auto-flow around its real size.
  if (flowItems.has(shapeId)) scheduleRelayout()
}

// Global helper available on the top-level page (non-iframe Custom usage).
if (typeof window !== 'undefined') {
  window.canvas = {
    send: (data) => sendInput('__custom__', data),
  }

  // Custom HTML panels run inside sandboxed iframes and emit data via
  // postMessage. Each panel's injected canvas.send() tags messages with its
  // component id; forward them to Python over the shared WebSocket.
  window.addEventListener('message', (e) => {
    const d = e.data
    if (!d || typeof d !== 'object') return
    if (d.__pycanvas_wheel) {
      // A Ctrl/Cmd+wheel inside an iframe panel: zoom the canvas, not the browser.
      zoomFromIframe(e.source, d.__pycanvas_wheel)
    } else if (d.__pycanvas_fit) {
      // An h="auto" panel reporting its content height.
      fitFromIframe(e.source, d.__pycanvas_fit)
    } else if (d.__pycanvas) {
      sendInput(d.__pycanvas, d.data)
    }
  })
}
