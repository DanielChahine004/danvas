import { createShapeId } from 'tldraw'
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
  connect()
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
  } else if (msg.type === 'update') {
    updateComponent(msg.id, msg.payload || {})
  } else if (msg.type === 'remove') {
    removeComponent(msg.id)
  }
}

function removeComponent(id) {
  // Drop any live-data wiring (LivePlot) so its buffer doesn't leak.
  liveHandlers.delete(id)
  liveBuffer.delete(id)
  const shapeId = createShapeId(id)
  if (editor.getShape(shapeId)) editor.deleteShape(shapeId)
}

function registerComponent({ id, component, props = {}, x, y, rotation }) {
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
  editor.createShape(shape)
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
  const { x, y, rotation, ...props } = payload
  const patch = { id: shapeId, type: shape.type, props: { ...props } }
  if (typeof x === 'number') patch.x = x
  if (typeof y === 'number') patch.y = y
  if (typeof rotation === 'number') patch.rotation = rotation
  editor.updateShape(patch)
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
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'input', id, payload }))
  }
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
