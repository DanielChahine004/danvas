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

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${location.host}/ws`
  ws = new WebSocket(url)

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
  }
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
  if (editor.getShape(shapeId)) applyRemote(() => editor.deleteShape(shapeId))
}

// Build a shape `meta` object from the Python movable/resizable flags, merging
// onto any existing meta so a partial update doesn't clobber the other flag.
function lockMeta(base, movable, resizable) {
  const meta = { ...(base || {}) }
  if (typeof movable === 'boolean') meta.lockMove = !movable
  if (typeof resizable === 'boolean') meta.lockResize = !resizable
  return meta
}

function registerComponent({ id, component, props = {}, x, y, rotation, locked, movable, resizable }) {
  const shapeType = COMPONENT_TO_SHAPE[component]
  if (!shapeType) return

  const shapeId = createShapeId(id)
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
  if (typeof movable === 'boolean' || typeof resizable === 'boolean') {
    shape.meta = lockMeta({}, movable, resizable)
  }
  applyRemote(() => editor.createShape(shape))
}

// Draw a tldraw arrow bound to two existing panels. The bindings make the
// arrow reroute automatically as the panels move or resize. `props` carries any
// tldraw arrow props (color, dash, size, text, bend, arrowheadStart/End, ...);
// later changes arrive as normal `update` messages and patch these props.
function createArrow({ id, start, end, props = {} }) {
  const arrowId = createShapeId(id)
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

  const shapeId = createShapeId(id)
  const shape = editor.getShape(shapeId)
  if (!shape) return
  // x/y/rotation are top-level shape fields, not props; everything else
  // (incl. w/h) is a shape prop. Split them so live move/resize/rotate works.
  const { x, y, rotation, locked, movable, resizable, ...props } = payload
  const patch = { id: shapeId, type: shape.type, props: { ...props } }
  if (typeof x === 'number') patch.x = x
  if (typeof y === 'number') patch.y = y
  if (typeof rotation === 'number') patch.rotation = rotation
  if (typeof locked === 'boolean') patch.isLocked = locked
  if (typeof movable === 'boolean' || typeof resizable === 'boolean') {
    patch.meta = lockMeta(shape.meta, movable, resizable)
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
