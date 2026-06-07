import { createShapeId, createBindingId } from 'tldraw'
import { COMPONENT_TO_SHAPE } from './canvas'

// Single shared WebSocket connection. All components are multiplexed over it,
// keyed by component id. State lives in Python + tldraw shape props only.
let editor = null
let ws = null

// Cascade newly registered components so they don't stack on one spot.
let placeIndex = 0
function nextPosition() {
  const col = placeIndex % 3
  const row = Math.floor(placeIndex / 3)
  placeIndex += 1
  return { x: 80 + col * 280, y: 80 + row * 200 }
}

// component id <-> tldraw shape id helpers.
export function componentIdOf(shapeId) {
  return String(shapeId).replace(/^shape:/, '')
}

export function setEditor(e) {
  editor = e
  setupGeometrySync(e)
  setupDrawSync(e)
  connect()
}

// Run store mutations driven by Python as "remote" changes. The geometry-sync
// handler below only reacts to "user" changes, so this keeps our own updates
// (move/resize/register/load) from echoing straight back to Python.
function applyRemote(fn) {
  editor.store.mergeRemoteChanges(fn)
}

// Send any message to Python over the shared socket.
function sendRaw(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
}

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
    // Debounce: a drag fires many changes; report the settled position once.
    dirtyShapes.add(next.id)
    if (flushTimer) clearTimeout(flushTimer)
    flushTimer = setTimeout(flushGeometry, 120)
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

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${location.host}/ws`
  ws = new WebSocket(url)

  ws.onopen = () => {
    // Periodic heartbeat so the server can tell a live (but idle) viewer from a
    // dead tab and keep the viewer count/roster accurate (the WS keepalive ping
    // is disabled server-side). 10s is comfortably under the server's timeout.
    if (heartbeatTimer) clearInterval(heartbeatTimer)
    heartbeatTimer = setInterval(() => sendRaw({ type: 'heartbeat' }), 10000)
  }

  ws.onmessage = (ev) => {
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

function handle(msg) {
  if (!editor || !msg || !msg.type) return
  if (msg.type === 'register') {
    registerComponent(msg)
  } else if (msg.type === 'arrow') {
    createArrow(msg)
  } else if (msg.type === 'update') {
    updateComponent(msg.id, msg.payload || {})
  } else if (msg.type === 'remove') {
    removeComponent(msg.id)
  } else if (msg.type === 'get_snapshot') {
    // Python is asking for the user's free-form drawings only — pycanvas panels
    // and connector arrows are recreated from code, not persisted.
    sendRaw({ type: 'snapshot', reqId: msg.reqId, data: userContent(msg.panelIds || []) })
  } else if (msg.type === 'load_snapshot') {
    loadSnapshot(msg.data)
  } else if (msg.type === 'draw') {
    applyDraw(msg.diff)
  } else if (msg.type === 'presence') {
    setPresence(msg.count || 0)
    setRoster(msg.viewers || [])
  } else if (msg.type === 'welcome') {
    setIdentity(msg.you || null)
  } else if (msg.type === 'chat') {
    pushChat(msg)
  } else if (msg.type === 'complete_result') {
    resolveCompletion(msg.reqId, msg.completions)
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

function removeComponent(id) {
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
function lockMeta(base, movable, resizable, interactive) {
  const meta = { ...(base || {}) }
  if (typeof movable === 'boolean') meta.lockMove = !movable
  if (typeof resizable === 'boolean') meta.lockResize = !resizable
  if (typeof interactive === 'boolean') meta.lockInput = !interactive
  return meta
}

function registerComponent({ id, component, props = {}, x, y, rotation, locked, movable, resizable, interactive }) {
  const shapeType = COMPONENT_TO_SHAPE[component]
  if (!shapeType) return

  const shapeId = createShapeId(id)
  managedIds.add(shapeId) // exclude from free-form drawing sync
  if (editor.getShape(shapeId)) return // already on canvas (reconnect)

  // Use the position Python supplied; cascade only the axes left unspecified.
  let px = x
  let py = y
  if (typeof px !== 'number' || typeof py !== 'number') {
    const auto = nextPosition()
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
      typeof interactive === 'boolean') {
    shape.meta = lockMeta({}, movable, resizable, interactive)
  }
  applyRemote(() => editor.createShape(shape))
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

  // Custom panels: `push()` data is forwarded straight into the iframe (see
  // CustomView) instead of touching shape props, so streaming doesn't reload the
  // frame. Dropped if the panel isn't mounted yet (the next push will land).
  if (payload && payload.post !== undefined) {
    const handler = liveHandlers.get(id)
    if (handler) handler(payload.post)
    return
  }

  // AudioFeed chunks ride the same live channel straight to the Web Audio
  // scheduler (see AudioView). Not buffered: stale audio must never replay, so a
  // chunk that arrives before the panel mounts (or while muted) is simply
  // dropped rather than stored like plot/post data.
  if (payload && payload.audio !== undefined) {
    const handler = liveHandlers.get(id)
    if (handler) handler(payload.audio)
    return
  }

  const shapeId = createShapeId(id)
  const shape = editor.getShape(shapeId)
  if (!shape) return
  // x/y/rotation are top-level shape fields, not props; everything else
  // (incl. w/h) is a shape prop. Split them so live move/resize/rotate works.
  const { x, y, rotation, locked, movable, resizable, interactive, ...props } = payload
  const patch = { id: shapeId, type: shape.type, props: { ...props } }
  if (typeof x === 'number') patch.x = x
  if (typeof y === 'number') patch.y = y
  if (typeof rotation === 'number') patch.rotation = rotation
  if (typeof locked === 'boolean') patch.isLocked = locked
  if (typeof movable === 'boolean' || typeof resizable === 'boolean' ||
      typeof interactive === 'boolean') {
    patch.meta = lockMeta(shape.meta, movable, resizable, interactive)
  }
  applyRemote(() => editor.updateShape(patch))
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
    if (d && typeof d === 'object' && d.__pycanvas) {
      sendInput(d.__pycanvas, d.data)
    }
  })
}
