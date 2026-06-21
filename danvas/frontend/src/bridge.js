import { createShapeId, createBindingId } from 'tldraw'
import { COMPONENT_TO_SHAPE } from './canvas'
import { BIN_VIDEO, BIN_AUDIO, BIN_CUSTOM, BIN_REACT, BIN_INPUT } from './protocol.generated.js'

// Single shared WebSocket connection. All components are multiplexed over it,
// keyed by component id. State lives in Python + tldraw shape props only.
let editor = null
let ws = null

// Auto-place panels that arrive without an explicit x/y. We flow them
// left-to-right, top-to-bottom and pack by each panel's *real* size (+ a gap),
// so they never overlap â€” unlike a fixed-step grid, which collided whenever a
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
// sizes have settled (see scheduleRelayout) â€” the registration-time size is the
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
  for (const m of moves) sendRaw({ type: 'layout', auto: true, ...m })
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
  armedReflows.clear()
  if (relayoutTimer) { clearTimeout(relayoutTimer); relayoutTimer = null }
  containers.clear()
  panelToContainer.clear()
  containerParent.clear()
  if (_fillWObserver) { _fillWObserver.disconnect(); _fillWObserver = null }
}

// Explicit re-pack of a Python `canvas.column`/`row` (column.refit()). Unlike the
// masonry flow above this is *manual*: it never re-packs on its own, so a panel
// that routinely changes height doesn't make its neighbours jitter. A refit packs
// the group at the panels' current sizes right now, then arms a short one-shot so
// the next content-fit a member reports re-packs once more â€” catching the resize
// the refit() was called for (e.g. a log line just added in Python, whose taller
// height hasn't been measured here yet when the reflow message arrives) â€” then
// disarms. Keyed by the Python container's id so repeated refits just re-arm.
const armedReflows = new Map() // key -> { spec, deadline }
const REFLOW_ARM_MS = 800

// Lay a group's members out in insertion order along one axis at their current
// sizes: a column pins x and stacks y by each panel's height; a row pins y and
// advances x by each width. Moves are applied as a remote change (no echo) and
// reported to Python so the new positions persist across reconnects.
function packFlow(spec) {
  if (!editor || !spec || !spec.ids || !spec.ids.length) return
  const moves = []
  applyRemote(() => {
    let cx = spec.x0
    let cy = spec.y0
    for (const cid of spec.ids) {
      const shapeId = createShapeId(cid)
      const shape = editor.getShape(shapeId)
      if (!shape) continue // removed since insert â€” skip, don't reserve space
      const x = spec.kind === 'row' ? cx : spec.x0
      const y = spec.kind === 'row' ? spec.y0 : cy
      if (Math.abs(shape.x - x) >= 0.5 || Math.abs(shape.y - y) >= 0.5) {
        editor.updateShape({ id: shapeId, type: shape.type, x, y })
        moves.push({ id: cid, x, y })
      }
      if (spec.kind === 'row') cx += shape.props.w + spec.gap
      else cy += shape.props.h + spec.gap
    }
  })
  for (const m of moves) sendRaw({ type: 'layout', ...m })
}

function reflowGroup(msg) {
  const spec = { ids: msg.ids || [], kind: msg.kind, x0: msg.x0, y0: msg.y0, gap: msg.gap }
  packFlow(spec) // settle the already-known sizes immediatelyâ€¦
  if (spec.ids.length) {
    // â€¦and once more on the imminent fit of whichever member just changed.
    armedReflows.set(msg.key, { spec, deadline: Date.now() + REFLOW_ARM_MS })
  }
}

// A member's content-fit just settled (see fitFromIframe / fitNative). If a recent
// refit() armed its group, re-pack now that this panel's real height is known.
function settleArmedReflows(componentId) {
  if (!armedReflows.size) return
  const now = Date.now()
  for (const [key, entry] of armedReflows) {
    if (now > entry.deadline) { armedReflows.delete(key); continue }
    if (entry.spec.ids.includes(componentId)) packFlow(entry.spec)
  }
}

// --- Container auto-repack ---------------------------------------------------
// Persistent layout containers (canvas.column / row / container). When any
// member's size changes (fitFromIframe / fitNative), the whole tree repacks
// automatically so panels never overlap after h="auto" content settles.

const containers = new Map()        // key -> container_sync spec
const panelToContainer = new Map()  // panelId -> containerKey (direct parent)
const containerParent = new Map()   // childContainerKey -> parentContainerKey
let _fillWObserver = null           // ResizeObserver for fill_w viewport tracking

function _hasFillW() {
  for (const spec of containers.values()) if (spec.fill_w) return true
  return false
}

// Repack all fill_w root containers (called on viewport resize).
function _refreshFillW() {
  const seen = new Set()
  for (const [key, spec] of containers) {
    if (!spec.fill_w) continue
    const root = getRootContainerKey(key)
    if (!seen.has(root)) { seen.add(root); repackContainer(root) }
  }
}

function _setupFillWObserver() {
  if (_fillWObserver || !editor || typeof ResizeObserver === 'undefined') return
  const el = editor.getContainer()
  if (!el) return
  _fillWObserver = new ResizeObserver(_refreshFillW)
  _fillWObserver.observe(el)
}

function syncContainer(spec) {
  containers.set(spec.key, spec)
  rebuildContainerIndices()
  if (spec.fill_w) {
    _setupFillWObserver()
    // Immediately apply the viewport width to this container's panels.
    if (editor) repackContainer(getRootContainerKey(spec.key))
  }
}

function rebuildContainerIndices() {
  panelToContainer.clear()
  containerParent.clear()
  for (const [key, spec] of containers) {
    for (const m of spec.members || []) {
      if (m.kind === 'panel') panelToContainer.set(m.id, key)
      else if (m.kind === 'container') containerParent.set(m.key, key)
    }
  }
}

function getRootContainerKey(key) {
  while (containerParent.has(key)) key = containerParent.get(key)
  return key
}

// Recursive repack. Returns {w, h} for the parent container to use when
// advancing its cursor past this child.
function repackContainer(key, ox, oy) {
  const spec = containers.get(key)
  if (!spec || !editor) return { w: 0, h: 0 }
  // fill_w: expand to the visible viewport width (in canvas units = CSS px Ã· zoom).
  // In scroll_y mode zoom=1 and the camera is at x=0, so canvas px === screen px.
  const specW = spec.fill_w
    ? Math.floor(editor.getContainer().offsetWidth / editor.getZoomLevel())
        - 2 * (spec.padding ?? 0)
    : spec.w
  const x0 = ox !== undefined ? ox : (spec.x0 ?? 0)
  const y0 = oy !== undefined ? oy : (spec.y0 ?? 0)
  let cx = x0, cy = y0
  let crossSize = 0
  const reports = []

  for (const m of spec.members || []) {
    const mx = spec.mode === 'row' ? cx : x0
    const my = spec.mode === 'column' ? cy : y0
    let mw = 0, mh = 0

    if (m.kind === 'panel') {
      const shapeId = createShapeId(m.id)
      const shape = editor.getShape(shapeId)
      if (!shape) continue  // removed since sync â€” skip, don't reserve space
      mw = specW != null ? specW : shape.props.w
      mh = spec.h != null ? spec.h : shape.props.h

      const needsMove = Math.abs(shape.x - mx) >= 0.5 || Math.abs(shape.y - my) >= 0.5
      const needsW = spec.mode === 'column' && specW != null && Math.abs(shape.props.w - specW) >= 0.5
      const needsH = spec.mode === 'row'    && spec.h != null && Math.abs(shape.props.h - spec.h) >= 0.5

      if (needsMove || needsW || needsH) {
        const patch = { id: shapeId, type: shape.type }
        if (needsMove) { patch.x = mx; patch.y = my }
        if (needsW || needsH) patch.props = { ...(needsW ? { w: specW } : {}), ...(needsH ? { h: spec.h } : {}) }
        applyRemote(() => editor.updateShape(patch))
        const report = { type: 'layout', id: m.id }
        if (needsMove) { report.x = mx; report.y = my }
        if (needsW) report.w = specW
        if (needsH) report.h = spec.h
        reports.push(report)
      }
    } else if (m.kind === 'container') {
      const size = repackContainer(m.key, mx, my)
      mw = size.w
      mh = size.h
    }

    if (spec.mode === 'column') {
      cy += mh + spec.gap
      crossSize = Math.max(crossSize, mw)
    } else {
      cx += mw + spec.gap
      crossSize = Math.max(crossSize, mh)
    }
  }

  for (const r of reports) sendRaw(r)

  // Remove the trailing gap added after the last child to get the true extent.
  const mainSize = spec.mode === 'column'
    ? Math.max(0, cy - y0 - spec.gap)
    : Math.max(0, cx - x0 - spec.gap)

  return spec.mode === 'column'
    ? { w: specW ?? crossSize, h: spec.h ?? mainSize }
    : { w: specW ?? mainSize, h: spec.h ?? crossSize }
}

// Called when a panel's size settles. Finds the root container and repacks.
function autoRepackForPanel(panelId) {
  const key = panelToContainer.get(panelId)
  if (!key) return
  repackContainer(getRootContainerKey(key))
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
  setupGraveyardSync(e)
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
// tldraw's high-level mutators (createShape/updateShape/deleteShapes/â€¦) all
// no-op while the instance is read-only. But a read-only view is meant to stop
// the *viewer* drawing, not the host's Python-driven panels â€” without this lift,
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
// Off until the server enables it (welcome.cursors â€” gated to a private bind by
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

// tldraw shape ids (`shape:<id>`) of every danvas-managed panel and connector
// arrow. These are recreated from Python code, so they're excluded from the
// free-form drawing sync below â€” only the user's own shapes are relayed.
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
// be hovered or selected by the user â€” not by body click, edge click, or
// marquee â€” so no highlight or selection box ever outlines it. Without this,
// tldraw hit-tests the shape's geometry directly (pointer events that miss the
// panel's interactive content land on the canvas underneath), so an "empty"
// region of a frameless panel would still hover-highlight and click-select.
// Implemented as a page-state filter rather than per-event handlers so every
// selection path (click, marquee, select-all) is covered. Python updates aren't
// affected â€” they never go through the editor's selection state.
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
// danvas panels/arrows travel as register/update/arrow messages; everything
// *else* the user draws (pen, geo, text, notes, their own arrows, ...) is synced
// here as tldraw store diffs so every browser on this canvas sees the same ink.
// Only document-scope, user-originated records are watched; our own remote
// applies (mergeRemoteChanges) and danvas-managed shapes are filtered out, so
// neither echoes back into a loop.
const DRAW_TYPES = new Set(['shape', 'binding', 'asset'])

function isManaged(record) {
  if (record.typeName === 'shape') {
    // Panels match by type; danvas arrows are plain `arrow` shapes, so they
    // only match by id â€” hence the managedIds set rather than a type check.
    return managedIds.has(record.id) || (PANEL_TYPES && PANEL_TYPES.has(record.type))
  }
  if (record.typeName === 'binding') {
    // A binding belongs to danvas if either end is one of our shapes (this is
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

// Detect when the user deletes a danvas-managed shape in tldraw and notify
// Python. Python keeps the component (callbacks etc. stay live) and shows it
// in the graveyard panel so it can be restored without restarting the script.
// { source: 'user' } means this only fires for user-initiated deletes, not
// Python-initiated removes (which go through applyRemote / mergeRemoteChanges).
function setupGraveyardSync(ed) {
  ed.store.listen(
    ({ changes }) => {
      for (const [id, rec] of Object.entries(changes.removed || {})) {
        if (rec.typeName === 'shape' && managedIds.has(id)) {
          sendRaw({ type: 'graveyard', id: id.replace(/^shape:/, '') })
        }
      }
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
    console.error('[danvas] failed to apply remote drawing', err)
  }
}

let heartbeatTimer = null

// The backend run this page last talked to (from the welcome frame). Survives
// socket reconnects (module state lives as long as the page); a fresh page
// starts at null and adopts whatever run it first joins.
let lastRunId = null

// Per-tab viewer identity. Stored in sessionStorage (per-tab, not shared like
// localStorage â€” two tabs stay two viewers) and re-sent on every reconnect, so a
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
  } catch { /* private mode / disabled storage â€” fall back to per-connection ids */ }
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
    // Server gone or restarting â€” retry so a reloaded backend reconnects.
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
// danvas/_protocol.py â€” so the two sides can't drift.
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

// Decode a binary frame â€” `[type][idLen][id bytes][payload]` â€” and route its
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
  } else if (msg.type === 'shape') {
    createManagedShape(msg)
  } else if (msg.type === 'shape_update') {
    updateManagedShape(msg)
  } else if (msg.type === 'update') {
    updateComponent(msg.id, msg.payload || {})
  } else if (msg.type === 'order') {
    reorderComponent(msg.id, msg.op)
  } else if (msg.type === 'remove') {
    removeComponent(msg.id)
  } else if (msg.type === 'container_sync') {
    syncContainer(msg)            // Container: register/update for auto-repack
  } else if (msg.type === 'reflow') {
    reflowGroup(msg)              // column.refit(): manual re-pack of a flow group
  } else if (msg.type === 'get_snapshot') {
    // Python is asking for the user's free-form drawings only â€” danvas panels
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
    setPeerCursor(msg)            // a peer moved â€” render their cursor
  } else if (msg.type === 'cursor_gone') {
    removePeerCursor(msg.id)      // a peer left â€” drop their cursor
  } else if (msg.type === 'view') {
    applyLiveView(msg.view || {})
  } else if (msg.type === 'welcome') {
    // The socket reconnected without the page reloading â€” if the backend is a
    // *different run* (re-run script, crash + restart, hot reload), the previous
    // run's panels are still on the canvas. Panel ids change every run, so the
    // new ones would appear *alongside* (stacked on top of) the stale, dead
    // ones. Detect the run change via the welcome runId and drop the managed
    // shapes first; the server replays this run's panels right after.
    // (msg.reload is the older hot-reload-only signal, kept for compatibility.)
    if (msg.reload || (lastRunId !== null && msg.runId && msg.runId !== lastRunId)) {
      console.info('[danvas] backend is a new run; clearing the previous run\'s panels')
      clearManaged()
      clearPeerCursors()   // stale peers from the previous run shouldn't linger
    }
    if (msg.runId) lastRunId = msg.runId
    setIdentity(msg.you || null)
    setUiInspectorEnabled(!!msg.uiInspector)
    setGraveyardEnabled(!!msg.uiGraveyard)
    setAuthEnabled(!!msg.auth)
    setCursorsEnabled(!!msg.cursors)
    setViewConfig(msg.view || null)
  } else if (msg.type === 'graveyard_update') {
    setGraveyardItems(msg.items || [])
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
// round-trip and component-routes it â€” { type:'request', id, reqId, data } gets a
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

// Collect tldraw "content" for everything on the page except the danvas
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
    console.warn('[danvas] ignoring an old full-canvas file; re-save with this version')
    return
  }
  try {
    applyRemote(() =>
      editor.putContentOntoCurrentPage(data, { select: false, preservePosition: true })
    )
  } catch (err) {
    console.error('[danvas] failed to load drawings', err)
  }
}

// Drop every danvas-managed shape (panels + connector arrows) and its live
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
  for (const compId of [..._camPanels.keys()]) stopCameraCapture(compId)
  for (const compId of [..._micPanels.keys()]) stopMicCapture(compId)
  liveHandlers.clear()
  liveBuffer.clear()
  styleHandlers.clear()
  styleBuffer.clear()
  setUiInspectorOpen(false)
  setGraveyardItems([])
  // Rewind the auto-placement flow so panels without an explicit x/y land in
  // the same spots after the reload as before it. The server replays components
  // in stable insertion order, so from the start the same panels get the same
  // slots; without this the cursor keeps advancing and they drift each reload.
  resetFlow()
}

function removeComponent(id) {
  if (id === UI_INSPECTOR_ID) setUiInspectorOpen(false)
  stopCameraCapture(id) // stop any parent-side camera capture for this panel
  stopMicCapture(id)    // stop any parent-side mic capture for this panel
  // Drop any live-data wiring (LivePlot) so its buffer doesn't leak.
  liveHandlers.delete(id)
  liveBuffer.delete(id)
  styleHandlers.delete(id)
  styleBuffer.delete(id)
  const shapeId = createShapeId(id)
  managedIds.delete(shapeId)
  if (editor.getShape(shapeId)) applyRemote(() => editor.deleteShape(shapeId))
}

// Build a shape `meta` object from the Python movable/resizable/interactive
// flags, merging onto any existing meta so a partial update doesn't clobber the
// others. `lockInput` blocks the user from touching the panel's controls while
// the shape stays *unlocked*, so programmatic value updates still render (unlike
// the top-level isLocked, which tldraw also refuses prop updates to).
function lockMeta(base, movable, resizable, interactive, selectable, frame, frameColor) {
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
  // frameColor tints the card chrome to match the component's accent color.
  if (typeof frameColor === 'string') meta.frameColor = frameColor
  return meta
}

function registerComponent({ id, component, props = {}, x, y, rotation, opacity, locked, movable, resizable, interactive, selectable, frame, frameColor }) {
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
  if (typeof opacity === 'number') shape.opacity = opacity
  if (typeof locked === 'boolean') shape.isLocked = locked
  if (typeof movable === 'boolean' || typeof resizable === 'boolean' ||
      typeof interactive === 'boolean' || typeof selectable === 'boolean' ||
      typeof frame === 'boolean' || typeof frameColor === 'string') {
    shape.meta = lockMeta({}, movable, resizable, interactive, selectable, frame, frameColor)
  }
  applyRemote(() => editor.createShape(shape))
  // Record an auto-assigned position back to Python so the panel keeps it on the
  // next viewer/reconnect instead of being re-flowed. Without this, a panel that
  // was placed by the masonry flow stays x=None in Python; once *other* panels
  // get a concrete position (a user move, or an auto-height fit), they skip the
  // flow on reconnect while this one re-flows from the origin â€” and they collide.
  // Pinning every auto-placed panel the same way keeps positions stable for all.
  if (autoPlaced) {
    sendRaw({ type: 'layout', id, x: px, y: py, auto: true })
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

// Convert a Python plain-text string to a minimal tldraw richText document.
// All text-bearing shapes (geo, text, note) now use richText internally;
// sending a bare `text` prop is silently dropped by tldraw >= 3.x.
function toRichText(s) {
  return {
    type: 'doc',
    content: s
      ? [{ type: 'paragraph', content: [{ type: 'text', text: s }] }]
      : [{ type: 'paragraph' }],
  }
}

// Normalise Python-supplied props before handing them to tldraw.createShape.
// â€¢ text -> richText conversion for all text-bearing shape types.
// â€¢ geo/line/draw/highlight/note/frame are all expected here; arrow is handled
//   separately by createArrow.
function normaliseManagedProps(shapeType, props) {
  const p = { ...props }
  // Text-bearing shape types
  if (['geo', 'text', 'note', 'arrow'].includes(shapeType) && p.text !== undefined) {
    p.richText = toRichText(p.text)
    delete p.text
  }
  return p
}

// Create a Python-managed tldraw shape (geo, text, note, draw, line, frame,
// highlight).  Added to managedIds so the free-form drawing sync ignores it.
// Camera mode -------------------------------------------------------------------

const CAMERA_LARGE = 100_000
const DEFAULT_ZOOM_STEPS = [0.1, 0.25, 0.5, 1, 2, 4, 8]

// Wheel-event interceptor installed when a scroll mode is active.
// tldraw 3.x has no wheelBehavior option, so we capture the event before
// tldraw sees it, prevent its default zoom, and manually pan the camera.
let _wheelCleanup = null

function setupScrollWheelPan(mode) {
  if (_wheelCleanup) {
    _wheelCleanup()
    _wheelCleanup = null
  }
  if (!mode || mode === 'free') return

  const container = editor.getContainer()

  const onWheel = (e) => {
    if (!editor) return
    e.preventDefault()
    e.stopPropagation() // stop tldraw's bubble-phase zoom handler

    const camera = editor.getCamera()
    // deltaMode: 0 = pixels, 1 = lines (~20 px), 2 = pages (~400 px)
    const scale = e.deltaMode === 1 ? 20 : e.deltaMode === 2 ? 400 : 1

    if (mode === 'scroll_y') {
      // Vertical scroll â†’ vertical pan.  Horizontal delta is discarded;
      // the x:'fixed' constraint would block it anyway.
      editor.setCamera({ x: camera.x, y: camera.y - e.deltaY * scale, z: camera.z })
    } else {
      // scroll_x: map vertical scroll to horizontal pan so a standard
      // mouse wheel advances the timeline/deck without needing shift.
      const dx = (e.deltaX + e.deltaY) * scale
      editor.setCamera({ x: camera.x - dx, y: camera.y, z: camera.z })
    }
  }

  // capture: true fires before tldraw's bubble-phase handler
  container.addEventListener('wheel', onWheel, { passive: false, capture: true })
  _wheelCleanup = () => container.removeEventListener('wheel', onWheel, { capture: true })
}

function applyCameraMode(mode, zoom = 1) {
  if (mode === 'free') {
    editor.setCameraOptions({
      constraints: undefined,
      zoomSteps: DEFAULT_ZOOM_STEPS,
    })
    setupScrollWheelPan('free')
    return
  }
  const behavior = mode === 'scroll_y'
    ? { x: 'fixed', y: 'free' }
    : { x: 'free',  y: 'fixed' }
  editor.setCameraOptions({
    constraints: {
      bounds: { x: 0, y: 0, w: CAMERA_LARGE, h: CAMERA_LARGE },
      padding: { x: 0, y: 0 },
      origin: { x: 0, y: 0 },
      initialZoom: 'default',
      baseZoom: 'default',
      behavior,
    },
    zoomSteps: [zoom],  // lock to the requested zoom; wheel handler prevents gesture zoom
  })
  setupScrollWheelPan(mode)
  // scheduleInitialFit fires 180 ms after the last shape registers â€” after
  // this applyCameraMode call â€” and would re-centre + re-fit the content,
  // overriding the position we set.  Cancelling the timer and marking the
  // fit as done prevents that; we own the camera from here on.
  if (initialFitTimer) { clearTimeout(initialFitTimer); initialFitTimer = null }
  initialFitDone = true
  editor.setCamera({ x: 0, y: 0, z: zoom })
}

// Managed tldraw shapes --------------------------------------------------------

function createManagedShape({ id, shapeType, x, y, rotation, opacity, props = {} }) {
  const shapeId = createShapeId(id)
  managedIds.add(shapeId) // exclude from free-form drawing sync and graveyard sync
  if (editor.getShape(shapeId)) return // already on canvas (reconnect)
  applyRemote(() => {
    editor.createShape({
      id: shapeId,
      type: shapeType,
      x: x ?? 0,
      y: y ?? 0,
      rotation: rotation ?? 0,
      opacity: opacity ?? 1,
      props: normaliseManagedProps(shapeType, props),
    })
  })
}

// Apply a live shape_update message from Python: patch top-level fields
// (x, y, rotation, opacity) and/or shape props.
function updateManagedShape({ id, x, y, rotation, opacity, props }) {
  const shapeId = createShapeId(id)
  const shape = editor.getShape(shapeId)
  if (!shape) return
  const patch = { id: shapeId, type: shape.type }
  if (x != null) patch.x = x
  if (y != null) patch.y = y
  if (rotation != null) patch.rotation = rotation
  if (opacity != null) patch.opacity = opacity
  if (props && Object.keys(props).length) {
    patch.props = normaliseManagedProps(shape.type, props)
  }
  applyRemote(() => editor.updateShape(patch))
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
  // too â€” independent of whether the node is mounted â€” so a panel that unmounts
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

  // Live theme/style update (React.color setter): bypasses the tldraw store so
  // the CSS custom-property change reaches React state immediately, the same fast
  // path as post. Buffered so a panel that mounts after the push still gets the
  // latest style.
  if (payload && payload.post_style !== undefined) {
    styleBuffer.set(id, payload.post_style)
    const handler = styleHandlers.get(id)
    if (handler) handler(payload.post_style)
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

  // AudioFeed chunks no longer travel here â€” they ride a binary frame straight
  // to the Web Audio scheduler (see handleBinary / AudioView).

  const shapeId = createShapeId(id)
  const shape = editor.getShape(shapeId)
  if (!shape) return
  // x/y/rotation are top-level shape fields, not props; everything else
  // (incl. w/h) is a shape prop. Split them so live move/resize/rotate works.
  const { x, y, rotation, opacity, locked, movable, resizable, interactive, selectable, frame, frameColor, ...props } = payload
  const patch = { id: shapeId, type: shape.type, props: { ...props } }
  if (typeof x === 'number') patch.x = x
  if (typeof y === 'number') patch.y = y
  if (typeof rotation === 'number') patch.rotation = rotation
  if (typeof opacity === 'number') patch.opacity = opacity
  if (typeof locked === 'boolean') patch.isLocked = locked
  if (typeof movable === 'boolean' || typeof resizable === 'boolean' ||
      typeof interactive === 'boolean' || typeof selectable === 'boolean' ||
      typeof frame === 'boolean' || typeof frameColor === 'string') {
    patch.meta = lockMeta(shape.meta, movable, resizable, interactive, selectable, frame, frameColor)
  }
  // If Python explicitly sets a position, pin the panel out of the masonry
  // flow so relayoutFlow() doesn't move it back on the next repack.
  if (typeof x === 'number' && typeof y === 'number') flowItems.delete(shapeId)
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

// --- live-style side channel (used by React panels' .color setter) -----------
const styleHandlers = new Map() // componentId -> (th) => void
const styleBuffer = new Map()   // componentId -> last _th payload

export function registerStyle(id, handler) {
  styleHandlers.set(id, handler)
  if (styleBuffer.has(id)) handler(styleBuffer.get(id))
}

export function unregisterStyle(id) {
  styleHandlers.delete(id)
}

// --- parent-side camera capture (Custom panels' canvas.requestCamera) --------
// getUserMedia is blocked inside sandboxed iframes without allow-same-origin,
// which would break panel isolation. Instead the panel requests camera access
// via postMessage; the parent runs getUserMedia and relays each JPEG frame
// both to Python (as BIN_INPUT, same path as canvas.sendBinary) and back into
// the iframe (via liveHandlers, same path as push_binary â†’ canvas.onPush).
// One shared MediaStream is reused across all panels requesting camera access.

let _camStream = null   // shared MediaStream (one getUserMedia for all panels)
let _camVideo = null    // hidden <video> element that consumes the stream
let _camPending = null  // in-flight getUserMedia Promise (de-duplicates concurrent requests)
const _camPanels = new Map() // compId -> { interval }

async function startCameraCapture(compId, opts) {
  if (_camPanels.has(compId)) return
  // fps > 0 throttles to that rate; 0 / omitted = max rate (every rAF tick).
  const fps = typeof opts.fps === 'number' && opts.fps > 0 ? opts.fps : 0
  const minInterval = fps > 0 ? 1000 / fps : 0
  const quality = typeof opts.quality === 'number' ? opts.quality : 0.7
  const width = typeof opts.width === 'number' ? opts.width : 320
  const height = typeof opts.height === 'number' ? opts.height : 240

  try {
    // One shared stream: if getUserMedia is already in flight or done, reuse it.
    if (!_camStream && !_camPending) {
      _camPending = navigator.mediaDevices.getUserMedia({ video: { width, height } })
    }
    if (_camPending) {
      const stream = await _camPending
      // Only the first awaiter sets up the shared objects; concurrent awaiters
      // see _camStream already set (single-threaded microtask ordering).
      if (!_camStream) {
        _camStream = stream
        _camPending = null
        _camVideo = document.createElement('video')
        _camVideo.srcObject = _camStream
        _camVideo.autoplay = true
        _camVideo.muted = true
        _camVideo.playsInline = true
        _camVideo.play().catch(() => {}) // getUserMedia is already user-gestured
      }
    }

    const cap = document.createElement('canvas')
    cap.width = width
    cap.height = height
    const ctx = cap.getContext('2d')

    // rAF loop: runs at display rate (â‰¤60 fps) and skips a tick when the
    // previous blob encode hasn't finished, so frames never pile up.
    const entry = { rafId: null, lastCapture: 0, pending: false }
    _camPanels.set(compId, entry)

    const capture = () => {
      if (!_camPanels.has(compId)) return
      entry.rafId = requestAnimationFrame(capture)
      if (!_camVideo || _camVideo.readyState < 2 || entry.pending) return
      const now = performance.now()
      if (minInterval > 0 && now - entry.lastCapture < minInterval) return
      entry.lastCapture = now
      entry.pending = true
      ctx.drawImage(_camVideo, 0, 0, width, height)
      cap.toBlob((blob) => {
        entry.pending = false
        if (!blob || !_camPanels.has(compId)) return
        blob.arrayBuffer().then((buf) => {
          if (!_camPanels.has(compId)) return
          sendBinary(compId, buf) // up to Python as BIN_INPUT (copies buf internally)
          const handler = liveHandlers.get(compId)
          if (handler) handler(buf) // down into the iframe â€” transfers buf, so call last
        })
      }, 'image/jpeg', quality)
    }
    entry.rafId = requestAnimationFrame(capture)
  } catch (err) {
    console.warn('[danvas] camera unavailable for panel', compId, 'â€”', err.message)
  }
}

function stopCameraCapture(compId) {
  const entry = _camPanels.get(compId)
  if (!entry) return
  if (entry.rafId != null) cancelAnimationFrame(entry.rafId)
  _camPanels.delete(compId)
  if (_camPanels.size === 0 && _camStream) {
    _camStream.getTracks().forEach((t) => t.stop())
    _camStream = null
    _camVideo = null
  }
}

// --- parent-side microphone capture (Custom panels' canvas.requestMicrophone) -
// getUserMedia({audio}) is blocked inside sandboxed iframes for the same null-
// origin reason as camera. The parent captures mic audio via ScriptProcessorNode
// (fires on the main thread â€” no cross-thread postMessage hop needed), converts
// float32 to int16 PCM, and relays each chunk to Python (BIN_INPUT) and into
// the iframe (liveHandlers â†’ canvas.onPush). A JSON mic_start event is sent
// first so Python knows sampleRate / channels before audio data arrives.
// Each panel gets its own AudioContext + MediaStream (no shared stream here â€”
// multiple mic panels are uncommon, and sharing an AudioContext across panels
// with different buffer sizes would complicate the graph).

const _micPanels = new Map() // compId -> { stream, ctx, source, processor, silencer }

async function startMicCapture(compId, opts) {
  if (_micPanels.has(compId)) return
  // bufferSize must be a power of 2: 256 â€¦ 16384. 4096 â‰ˆ 85â€“93 ms per chunk.
  const bufferSize = 4096

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
    const ctx = new AudioContext()
    await ctx.resume() // ensure context is running (autoplay policy)

    // Tell Python the stream parameters before the first audio chunk arrives.
    sendInput(compId, { event: 'mic_start', sampleRate: ctx.sampleRate, channels: 1 })

    const source = ctx.createMediaStreamSource(stream)
    const processor = ctx.createScriptProcessor(bufferSize, 1, 1)

    processor.onaudioprocess = (e) => {
      if (!_micPanels.has(compId)) return
      const float32 = e.inputBuffer.getChannelData(0)
      // Convert float32 [-1, 1] â†’ int16 (half the wire size; same format
      // AudioFeed uses on the downward path).
      const int16 = new Int16Array(float32.length)
      for (let i = 0; i < float32.length; i++) {
        int16[i] = Math.max(-32768, Math.min(32767, float32[i] * 32767))
      }
      sendBinary(compId, int16.buffer) // up to Python as BIN_INPUT (copies internally)
      const handler = liveHandlers.get(compId)
      if (handler) handler(int16.buffer) // down into iframe â€” transfers buf, call last
    }

    // ScriptProcessorNode must be connected to destination to fire; a zero-gain
    // node in between keeps the mic audio from playing through speakers.
    const silencer = ctx.createGain()
    silencer.gain.value = 0
    source.connect(processor)
    processor.connect(silencer)
    silencer.connect(ctx.destination)

    _micPanels.set(compId, { stream, ctx, source, processor, silencer })
  } catch (err) {
    console.warn('[danvas] microphone unavailable for panel', compId, 'â€”', err.message)
  }
}

function stopMicCapture(compId) {
  const entry = _micPanels.get(compId)
  if (!entry) return
  try {
    entry.source.disconnect()
    entry.processor.disconnect()
    entry.silencer.disconnect()
    entry.ctx.close()
  } catch { /* ignore if already torn down */ }
  entry.stream.getTracks().forEach((t) => t.stop())
  _micPanels.delete(compId)
}

// Browser -> Python: user input from a component (slider move, etc.).
export function sendInput(id, payload) {
  sendRaw({ type: 'input', id, payload })
}

export function sendPanelError(id, message) {
  sendRaw({ type: 'panel_error', id, message: String(message) })
}

// Send a raw binary frame to Python: ``[BIN_INPUT][idLen][id bytes][payload]``.
// Used by canvas.sendBinary() in Custom iframes and React panels.
export function sendBinary(compId, buffer) {
  if (!ws) return
  const id = new TextEncoder().encode(compId)
  const frame = new Uint8Array(2 + id.length + buffer.byteLength)
  frame[0] = BIN_INPUT
  frame[1] = id.length
  frame.set(id, 2)
  frame.set(new Uint8Array(buffer), 2 + id.length)
  ws.send(frame.buffer)
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

// --- built-in graveyard panel -----------------------------------------------
// The server advertises (in `welcome`) whether this canvas shows the Graveyard
// button. When a user deletes a managed shape, Python keeps the component and
// broadcasts a `graveyard_update` with the current list of deleted panel names.
// The button opens a floating overlay; clicking Restore sends `{type:'restore'}`
// to Python, which re-registers the shape without restarting the script.
let uiGraveyard = { enabled: false, open: false, items: [] }
const uiGraveyardListeners = new Set()

function emitUiGraveyard() {
  for (const cb of uiGraveyardListeners) cb(uiGraveyard)
}
function setGraveyardEnabled(enabled) {
  uiGraveyard = { ...uiGraveyard, enabled }
  emitUiGraveyard()
}
function setGraveyardItems(items) {
  uiGraveyard = { ...uiGraveyard, items }
  emitUiGraveyard()
}

export function subscribeGraveyard(cb) {
  uiGraveyardListeners.add(cb)
  cb(uiGraveyard)
  return () => uiGraveyardListeners.delete(cb)
}

export function toggleGraveyard() {
  uiGraveyard = { ...uiGraveyard, open: !uiGraveyard.open }
  emitUiGraveyard()
}

export function sendRestore(id) {
  sendRaw({ type: 'restore', id })
}

// Whether this canvas is password-protected (welcome.auth). When true the app
// shows a sign-out button that navigates to /__logout__ (the server clears the
// session cookie and the password page returns). Shown regardless of a `ui:false`
// kiosk view â€” signing out is an auth escape hatch, not app chrome â€” so even a
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
// delta carried x/y/zoom â€” so toggling, say, `ui` or `grid` live never disturbs
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

  // Navigation mode (scroll_y / scroll_x / free): constrain the pan axis and
  // lock zoom.  Applied after the zoom-step/lock options above so scroll-mode's
  // zoomSteps: [zoom] wins over any min/max zoom setting.
  if (v.navigation) {
    applyCameraMode(v.navigation.mode, v.navigation.zoom ?? 1)
  }
}

// --- initial auto-fit --------------------------------------------------------
// When no explicit camera (x/y/zoom) is configured, frame every panel this
// viewer can see, centred, on first load â€” so the canvas never opens on empty
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
  // An explicit camera wins â€” respect serve(view={x/y/zoom}) / set_view.
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
  // past 100% â€” a single small panel should sit centred at its natural size, not
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
// the new geometry to Python â€” same read-back path as a user resize, so
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
  if (!props.h && !props.w) return // both settled â€” don't ping-pong
  applyRemote(() =>
    editor.updateShape({ id: shapeId, type: shape.type, props })
  )
  // Report only the size: a fit never moves the panel, and echoing x/y would
  // pin an auto-arranged panel (x=None in Python) to a number â€” which then makes
  // it skip the placement flow on the next viewer and collide with others.
  sendRaw(report)
  // The panel's footprint changed â€” re-pack the auto-flow around its real size.
  if (flowItems.has(shapeId)) scheduleRelayout()
  settleArmedReflows(fit.id)    // re-pack a column.refit() group waiting on it
  autoRepackForPanel(fit.id)    // re-pack any Container tree this panel is in
}

// Content-fit for native panels (the React `h="auto"` / `w="auto"` path). The
// native twin of fitFromIframe: where that reads an iframe's document size over
// postMessage, here the panel is a native React subtree, so ReactHost measures
// its content directly and calls this with `fit = { h?, w? }` and the host
// element that fills the card's body. Overhead (card header/padding) is
// `shape.<axis> - hostEl.offset<Axis>`, in layout px â€” the same read-back path
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
  if (props.h === undefined && props.w === undefined) return // both settled â€” don't ping-pong
  applyRemote(() =>
    editor.updateShape({ id: shapeId, type: shape.type, props })
  )
  // Size only â€” see fitFromIframe: echoing x/y would pin an auto-arranged panel
  // and break the placement flow for the next viewer.
  sendRaw(report)
  // The panel grew/shrank â€” re-pack the auto-flow around its real size.
  if (flowItems.has(shapeId)) scheduleRelayout()
  settleArmedReflows(componentId)    // re-pack a column.refit() group waiting on it
  autoRepackForPanel(componentId)    // re-pack any Container tree this panel is in
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
    if (d.__danvas_wheel) {
      // A Ctrl/Cmd+wheel inside an iframe panel: zoom the canvas, not the browser.
      zoomFromIframe(e.source, d.__danvas_wheel)
    } else if (d.__danvas_fit) {
      // An h="auto" panel reporting its content height.
      fitFromIframe(e.source, d.__danvas_fit)
    } else if (d.__danvas) {
      sendInput(d.__danvas, d.data)
    } else if (d.__danvas_binary && d.data instanceof ArrayBuffer) {
      // canvas.sendBinary(buf) from a Custom iframe: forward as a binary WS frame.
      sendBinary(d.__danvas_binary, d.data)
    } else if (d.__danvas_camera) {
      // canvas.requestCamera / releaseCamera from a Custom iframe.
      if (d.action === 'start') startCameraCapture(d.__danvas_camera, d.opts || {})
      else if (d.action === 'stop') stopCameraCapture(d.__danvas_camera)
    } else if (d.__danvas_mic) {
      // canvas.requestMicrophone / releaseMicrophone from a Custom iframe.
      if (d.action === 'start') startMicCapture(d.__danvas_mic, d.opts || {})
      else if (d.action === 'stop') stopMicCapture(d.__danvas_mic)
    }
  })
}
