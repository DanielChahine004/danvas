// The unified pointer + keyboard interaction handler. Routes by the active tool:
//   select  — click/marquee select (panels via DOM, drawings via geometry),
//             multi-move, delete, undo/redo
//   draw tools (rectangle/ellipse/line/arrow/draw/text/note) — create a shape
//   hand    — handled by the camera (pan); ignored here
// Drawing creation makes local DrawingRecords on the same layer Python shapes
// use; each gesture is one undo step (begin/endGroup). Sync to Python lands in
// the draw-sync stage.
import { signal } from 'alien-signals'
import { store } from './store'
import { editor, screenToPage } from './editor'
import { navMode } from './camera'
import { hitTestDrawing, hitTestErase, hitTestArrow, hitTestLabel, labelParamAt, recordsInRect, recordBBox, bindTargetAt } from './hittest'
import { copyRecordsToClipboard } from './export'
import { gesturing } from './gesture'
import type { Box } from './hittest'
import type { DrawingRecord, DrawStyle, Id, PanelRecord, Tool, WriteSignal } from './types'

// Live marquee rect (page coords) for the overlay to draw. null when idle.
export const marquee = signal<Box | null>(null) as WriteSignal<Box | null>

// Recent eraser path (page coords) for the ephemeral trail the overlay paints.
// Empty when not erasing.
export const eraseTrail = signal<{ x: number; y: number }[]>([]) as WriteSignal<{ x: number; y: number }[]>

// Alignment guides (page coords) shown while a move snaps. Empty when idle.
export interface SnapGuide {
  axis: 'x' | 'y'
  coord: number
}
export const snapGuides = signal<SnapGuide[]>([]) as WriteSignal<SnapGuide[]>

// Id of the shape an arrow endpoint is hovering and would bind to — the overlay
// outlines it (whiteboard-style) while drawing/dragging an arrow end. null when none.
export const bindHighlight = signal<Id | null>(null) as WriteSignal<Id | null>

// Page-space dot showing exactly where an arrow end will attach on the hovered
// panel (snaps to centre when near). null when not connecting.
export const bindAnchorDot = signal<{ x: number; y: number } | null>(null) as WriteSignal<{ x: number; y: number } | null>

// Ids the eraser has marked this stroke (dimmed in the layer; removed on release).
export const erasingIds = signal<Set<Id>>(new Set()) as WriteSignal<Set<Id>>

// True briefly while a STYLE-PANEL edit (colour/opacity/…) is applied, so the user
// sees the result without the selection box in the way. Stays true 0.5s after the
// last edit. Direct manipulations (move/resize/rotate/arrow reshape) keep their
// highlights. Any canvas press clears it (clearInteracting) so the box returns and
// the shape stays selectable.
export const interacting = signal<boolean>(false) as WriteSignal<boolean>
let _interactTimer: any = null
export function bumpInteracting(): void {
  if (!interacting()) interacting(true)
  if (_interactTimer) clearTimeout(_interactTimer)
  _interactTimer = setTimeout(() => interacting(false), 500)
}
export function clearInteracting(): void {
  if (_interactTimer) {
    clearTimeout(_interactTimer)
    _interactTimer = null
  }
  if (interacting()) interacting(false)
}

// Whether move-snapping to other records' edges/centres is on (Settings toggle).
const SNAP_KEY = 'pc_snap'
export const snapEnabled = signal<boolean>(typeof localStorage !== 'undefined' ? localStorage.getItem(SNAP_KEY) !== '0' : true) as WriteSignal<boolean>
export function setSnapEnabled(on: boolean): void {
  snapEnabled(on)
  try {
    localStorage.setItem(SNAP_KEY, on ? '1' : '0')
  } catch {
    /* private mode */
  }
}

// Canonical connection points (normalised): centre + the four edge midpoints. An
// arrow end SNAPS to the nearest of these within CONNECT_SNAP_SCREEN px — the same
// "lock" the centre already had, now extended to N/S/E/W — so connectors latch to
// clean anchors. Drag elsewhere on the shape to attach freely along the perimeter.
const CONNECT_SNAP_SCREEN = 12
const EDGE_ANCHORS: { x: number; y: number }[] = [
  { x: 0.5, y: 0 }, // top middle
  { x: 1, y: 0.5 }, // right middle
  { x: 0.5, y: 1 }, // bottom middle
  { x: 0, y: 0.5 }, // left middle
]

// World-space point of a normalised anchor on a (possibly rotated) box.
function anchorToPage(bb: Box, rot: number, nx: number, ny: number): { x: number; y: number } {
  const cx = bb.x + bb.w / 2
  const cy = bb.y + bb.h / 2
  const ox = nx * bb.w - bb.w / 2
  const oy = ny * bb.h - bb.h / 2
  const cc = Math.cos(rot)
  const ss = Math.sin(rot)
  return { x: cx + ox * cc - oy * ss, y: cy + ox * ss + oy * cc }
}

// Page-space dots for a shape's visible connection handles (the four edge
// midpoints), for the overlay to draw while an arrow is being connected/hovered.
export function connectorPoints(id: Id): { x: number; y: number }[] {
  const rec = store.peek(id) as any
  const bb = recordBBox(rec)
  if (!bb || bb.w <= 0 || bb.h <= 0) return []
  const rot = rec?.rotation || 0
  return EDGE_ANCHORS.map((a) => anchorToPage(bb, rot, a.x, a.y))
}

// Where an arrow end attaches on a target shape: a normalised anchor + the page
// dot. Snaps to the nearest connection point (centre → anchor undefined for a clean
// centre-clip; an edge midpoint → its anchor); otherwise a free perimeter anchor.
export function bindAnchorAt(pt: { x: number; y: number }, id: Id): { anchor: { x: number; y: number } | undefined; dot: { x: number; y: number } } | null {
  const rec = store.peek(id) as any
  const bb = recordBBox(rec)
  if (!bb || bb.w <= 0 || bb.h <= 0) return null
  const rot = rec?.rotation || 0
  const z = store.camera().z
  let best: { anchor: { x: number; y: number } | undefined; dot: { x: number; y: number } } | null = null
  let bestD = Infinity
  const consider = (anchor: { x: number; y: number } | undefined, dot: { x: number; y: number }) => {
    const d = Math.hypot(dot.x - pt.x, dot.y - pt.y) * z // screen px
    if (d < bestD) {
      bestD = d
      best = { anchor, dot }
    }
  }
  consider(undefined, anchorToPage(bb, rot, 0.5, 0.5)) // centre → clean centre-clip
  for (const a of EDGE_ANCHORS) consider(a, anchorToPage(bb, rot, a.x, a.y))
  if (best && bestD <= CONNECT_SNAP_SCREEN) return best
  // No handle nearby — attach freely at the cursor's perimeter-relative anchor.
  const cx = bb.x + bb.w / 2
  const cy = bb.y + bb.h / 2
  const c = Math.cos(-rot)
  const s = Math.sin(-rot)
  const dx = pt.x - cx
  const dy = pt.y - cy
  const lx = dx * c - dy * s
  const ly = dx * s + dy * c
  const nx = Math.max(0, Math.min(1, (lx + bb.w / 2) / bb.w))
  const ny = Math.max(0, Math.min(1, (ly + bb.h / 2) / bb.h))
  return { anchor: { x: nx, y: ny }, dot: anchorToPage(bb, rot, nx, ny) }
}

const DRAW_TOOLS = new Set<Tool>(['rectangle', 'ellipse', 'line', 'arrow', 'draw', 'text', 'note'])
// Eraser / click-select reach in screen pixels (converted to page units per gesture).
const ERASE_SCREEN_R = 11
const SELECT_SCREEN_R = 6
// connector arrows are thin and unmovable — give them a fat click target so
// they're easy to select / double-click-to-edit.
const ARROW_SCREEN_R = 14

function nid(): Id {
  return 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7)
}

function setSelection(ids: Id[]): void {
  store.transact('local', () => store.instance({ ...store.instance(), selectedIds: ids }))
}

function setEditing(id: Id): void {
  store.transact('local', () => store.instance({ ...store.instance(), tool: 'select', selectedIds: [id], editingId: id }))
}

// Expand a set of ids to include every member of any group they belong to, so a
// grouped drawing selects/moves/deletes as a unit. (props.groupId on drawings.)
function expandGroups(ids: Id[]): Id[] {
  const groups = new Set<string>()
  for (const id of ids) {
    const g = (store.peek(id) as any)?.props?.groupId
    if (g) groups.add(g)
  }
  if (!groups.size) return ids
  const out = new Set(ids)
  for (const gid of store.ids()) {
    const g = (store.peek(gid) as any)?.props?.groupId
    if (g && groups.has(g)) out.add(gid)
  }
  return [...out]
}

// Snap the moving selection's edges/centres to other records' edges/centres,
// returning the adjusted delta and publishing the guide lines. Hold Alt to skip.
const SNAP_SCREEN = 6
function applySnap(selBox: Box, dx: number, dy: number, movingIds: Id[], disabled: boolean): { dx: number; dy: number } {
  if (disabled || !snapEnabled()) {
    snapGuides([])
    return { dx, dy }
  }
  const z = store.camera().z
  const TH = SNAP_SCREEN / z
  const mX = [selBox.x + dx, selBox.x + selBox.w / 2 + dx, selBox.x + selBox.w + dx]
  const mY = [selBox.y + dy, selBox.y + selBox.h / 2 + dy, selBox.y + selBox.h + dy]
  const moving = new Set(movingIds)
  let bestX: { d: number; coord: number } | null = null
  let bestY: { d: number; coord: number } | null = null
  for (const id of store.ids()) {
    if (moving.has(id)) continue
    const bb = recordBBox(store.peek(id))
    if (!bb) continue
    const cX = [bb.x, bb.x + bb.w / 2, bb.x + bb.w]
    const cY = [bb.y, bb.y + bb.h / 2, bb.y + bb.h]
    for (const m of mX) for (const c of cX) {
      const d = c - m
      if (Math.abs(d) <= TH && (!bestX || Math.abs(d) < Math.abs(bestX.d))) bestX = { d, coord: c }
    }
    for (const m of mY) for (const c of cY) {
      const d = c - m
      if (Math.abs(d) <= TH && (!bestY || Math.abs(d) < Math.abs(bestY.d))) bestY = { d, coord: c }
    }
  }
  const guides: SnapGuide[] = []
  if (bestX) {
    dx += bestX.d
    guides.push({ axis: 'x', coord: bestX.coord })
  }
  if (bestY) {
    dy += bestY.d
    guides.push({ axis: 'y', coord: bestY.coord })
  }
  snapGuides(guides)
  return { dx, dy }
}

function newDrawing(shapeType: DrawingRecord['shapeType'], x: number, y: number, props: any): DrawingRecord {
  const opacity = store.instance().style.opacity ?? 1
  return { typeName: 'drawing', id: nid(), shapeType, x, y, rotation: 0, opacity, index: '', props }
}

// --- image upload (paste / drag-drop) ----------------------------------------
const IMG_MAX_SRC = 1600 // cap stored resolution so the synced data URL stays sane
const IMG_MAX_DISPLAY = 480 // initial on-canvas size of the longest side

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = reject
    r.readAsDataURL(file)
  })
}
function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = reject
    img.src = src
  })
}
// Downscale very large images so the stored (and wire-synced) data URL stays
// reasonable; small images pass through untouched. PNG keeps transparency.
function maybeDownscale(img: HTMLImageElement, dataUrl: string, type: string): string {
  const max = Math.max(img.naturalWidth, img.naturalHeight)
  if (max <= IMG_MAX_SRC) return dataUrl
  const s = IMG_MAX_SRC / max
  const c = document.createElement('canvas')
  c.width = Math.round(img.naturalWidth * s)
  c.height = Math.round(img.naturalHeight * s)
  const ctx = c.getContext('2d')
  if (!ctx) return dataUrl
  ctx.drawImage(img, 0, 0, c.width, c.height)
  return c.toDataURL(type === 'image/png' ? 'image/png' : 'image/jpeg', 0.85)
}

// Add an image file to the canvas as an `image` drawing (syncs + persists like
// any other drawing). Placed centred at `atPage` (default: viewport centre).
export async function addImageFromFile(file: File, atPage?: { x: number; y: number }): Promise<void> {
  if (!file.type.startsWith('image/')) return
  const raw = await readAsDataUrl(file)
  const img = await loadImage(raw)
  const src = maybeDownscale(img, raw, file.type)
  const ratio = img.naturalHeight / img.naturalWidth || 1
  const dw = Math.min(IMG_MAX_DISPLAY, img.naturalWidth || IMG_MAX_DISPLAY)
  const dh = Math.round(dw * ratio)
  const center = atPage || editor.getViewportPageBounds().center
  const rec = newDrawing('image', center.x - dw / 2, center.y - dh / 2, { src, w: dw, h: dh })
  store.beginGroup()
  store.transact('local', () => store.put(rec))
  store.endGroup()
  store.transact('local', () => store.instance({ ...store.instance(), tool: 'select', selectedIds: [rec.id] }))
}

type Drag =
  | { kind: 'move'; ids: Id[]; sx: number; sy: number; orig: Map<Id, { x: number; y: number }>; selBox: Box }
  | { kind: 'marquee'; sx: number; sy: number; base: Id[] }
  | { kind: 'geo'; id: Id; sx: number; sy: number }
  | { kind: 'line'; id: Id; sx: number; sy: number }
  | { kind: 'draw'; id: Id; sx: number; sy: number; pts: { x: number; y: number }[] }
  | { kind: 'label'; id: Id }
  | { kind: 'erase' }
  | null

export function attachInteraction(container: HTMLElement): () => void {
  let drag: Drag = null

  // Abort an in-progress single-pointer gesture (when a 2nd finger starts a pinch).
  const abortDrag = () => {
    if (!drag) return
    if (drag.kind === 'geo' || drag.kind === 'line' || drag.kind === 'draw') {
      const id = drag.id
      store.endGroup()
      const r = store.peek(id) as any
      if (r && (r.props.w || 0) < 8 && (r.props.h || 0) < 8) store.transact('local', () => store.remove(id))
    } else if (drag.kind === 'move') {
      store.endGroup()
    } else if (drag.kind === 'erase') {
      erasingIds(new Set()) // abandon marks, delete nothing
    } else if (drag.kind === 'marquee') {
      marquee(null)
    }
    eraseTrail([])
    container.classList.remove('pc-gesturing')
    drag = null
  }

  const onPointerDown = (e: PointerEvent) => {
    if (e.button !== 0) return
    if (gesturing()) return // a two-finger pinch owns the gesture
    clearInteracting() // a fresh canvas interaction brings the selection box back
    const tool = store.instance().tool
    // In a scroll/document mode a touch is either scrolling the page (camera) or a tap
    // on panel content — never a canvas marquee/panel-move. Bail so the press reaches
    // the content (the deferred scroll in input.ts handles drag-to-scroll). Mouse and
    // the draw tools are unaffected.
    if (e.pointerType === 'touch' && tool === 'select' && navMode() !== 'free') return
    // Capture the pointer for gestures we own (marquee/move/draw/erase) so a Custom
    // panel's iframe under the cursor can't swallow the move/up events mid-drag —
    // which froze a marquee the moment it crossed an iframe. Released automatically
    // on pointerup. The hand tool's pan is the camera's, so leave it alone.
    if (tool !== 'hand') {
      try {
        container.setPointerCapture(e.pointerId)
      } catch {
        /* capture unsupported / pointer already gone */
      }
      container.classList.add('pc-gesturing') // iframes click-through for the gesture
    }
    const pt = screenToPage({ x: e.clientX, y: e.clientY })
    const style = store.instance().style

    // --- draw tools: create a shape ---
    if (DRAW_TOOLS.has(tool)) {
      e.preventDefault()
      store.beginGroup()
      if (tool === 'text' || tool === 'note') {
        const isNote = tool === 'note'
        const rec = newDrawing(isNote ? 'note' : 'text', pt.x, pt.y, {
          text: '',
          color: style.color,
          size: style.size,
          ...(isNote ? { w: 160, h: 160, fill: 'solid' } : { w: 160, h: 36 }),
        })
        store.transact('local', () => store.put(rec))
        store.endGroup()
        // edit it immediately; drop back to select
        store.transact('local', () => store.instance({ ...store.instance(), tool: 'select', selectedIds: [rec.id], editingId: rec.id }))
        return
      }
      if (tool === 'draw') {
        const rec = newDrawing('draw', pt.x, pt.y, { points: [{ x: 0, y: 0 }], color: style.color, size: style.size, dash: style.dash })
        store.transact('local', () => store.put(rec))
        drag = { kind: 'draw', id: rec.id, sx: pt.x, sy: pt.y, pts: [{ x: 0, y: 0 }] }
        return
      }
      if (tool === 'line' || tool === 'arrow') {
        // bind the start end if it lands on a shape/panel (arrow sticks to it) +
        // remember WHERE on it (anchor; undefined = centre).
        const bindStart = bindTargetAt(pt) || undefined
        const sa = bindStart ? bindAnchorAt(pt, bindStart) : null
        const rec = newDrawing('line', pt.x, pt.y, {
          points: { a1: { id: 'a1', index: 'a1', x: 0, y: 0 }, a2: { id: 'a2', index: 'a2', x: 0, y: 0 } },
          color: style.color,
          size: style.size,
          dash: style.dash,
          arrow: tool === 'arrow',
          arrowKind: (style as any).arrowKind || 'straight',
          bindStart,
          bindStartAnchor: sa?.anchor,
          w: 0,
          h: 0,
        })
        store.transact('local', () => store.put(rec))
        drag = { kind: 'line', id: rec.id, sx: pt.x, sy: pt.y }
        return
      }
      // rectangle / ellipse
      const rec = newDrawing('geo', pt.x, pt.y, {
        geo: tool === 'ellipse' ? 'ellipse' : 'rectangle',
        w: 1,
        h: 1,
        color: style.color,
        fill: style.fill,
        size: style.size,
        dash: style.dash,
      })
      store.transact('local', () => store.put(rec))
      drag = { kind: 'geo', id: rec.id, sx: pt.x, sy: pt.y }
      return
    }

    if (tool === 'eraser') {
      e.preventDefault()
      // Mark hits (dimmed in the layer) and only delete them on release, so the
      // user sees what will go and can change their mind by not lifting.
      const r = ERASE_SCREEN_R / store.camera().z
      const hit = hitTestErase(pt, r)
      const marked = new Set(erasingIds())
      if (hit) marked.add(hit)
      erasingIds(marked)
      eraseTrail([{ x: pt.x, y: pt.y }])
      drag = { kind: 'erase' }
      return
    }

    if (tool !== 'select') return // hand tool etc. — camera handles it

    // --- select tool ---
    const target = e.target as HTMLElement
    const panelEl = target.closest('[data-pc-panel-id]') as HTMLElement | null
    const inst = store.instance()
    const additive = e.shiftKey

    if (panelEl) {
      const id = panelEl.getAttribute('data-pc-panel-id')!
      const rec = store.peek(id) as PanelRecord | undefined
      if (!rec || rec.isLocked || rec.meta?.noGrab) return
      selectThenMaybeMove(id, rec.meta?.lockMove ? false : true, additive, pt, e)
      return
    }

    // A line/arrow's CAPTION is directly grabbable: pressing the text starts a
    // label drag (moving it along the line) WITHOUT first selecting the arrow —
    // one gesture. A plain press just selects; a double-click still edits.
    const labelHit = !additive ? hitTestLabel(pt) : null
    if (labelHit) {
      if (!store.instance().selectedIds.includes(labelHit)) setSelection([labelHit])
      store.beginGroup()
      drag = { kind: 'label', id: labelHit }
      return
    }

    const hit = hitTestDrawing(pt, SELECT_SCREEN_R / store.camera().z)
    if (hit) {
      selectThenMaybeMove(hit, true, additive, pt, e)
      return
    }

    // Connector arrows (canvas.connect) reroute off their endpoints, so they're
    // not movable — but they should be clickable to select + delete.
    const arrowHit = hitTestArrow(pt, ARROW_SCREEN_R / store.camera().z)
    if (arrowHit) {
      selectThenMaybeMove(arrowHit, false, additive, pt, e)
      return
    }

    // empty canvas — start a marquee (clears selection unless additive)
    if (!additive && inst.selectedIds.length) setSelection([])
    drag = { kind: 'marquee', sx: pt.x, sy: pt.y, base: additive ? inst.selectedIds.slice() : [] }
    marquee({ x: pt.x, y: pt.y, w: 0, h: 0 })
  }

  // Select `id` (and its group, if any), then (if movable) begin moving the
  // whole selection.
  function selectThenMaybeMove(id: Id, movable: boolean, additive: boolean, pt: { x: number; y: number }, _e: PointerEvent) {
    const inst = store.instance()
    let sel = inst.selectedIds
    const grp = expandGroups([id])
    if (additive) {
      sel = sel.includes(id) ? sel.filter((x) => !grp.includes(x)) : Array.from(new Set([...sel, ...grp]))
      setSelection(sel)
      return // shift-click toggles; don't start a move
    }
    if (!sel.includes(id)) {
      sel = grp
      setSelection(sel)
    }
    if (!movable) return
    const orig = new Map<Id, { x: number; y: number }>()
    let bx = Infinity,
      by = Infinity,
      bX = -Infinity,
      bY = -Infinity
    for (const sid of sel) {
      const r = store.peek(sid) as any
      if (r && typeof r.x === 'number') orig.set(sid, { x: r.x, y: r.y })
      const bb = recordBBox(store.peek(sid))
      if (bb) {
        bx = Math.min(bx, bb.x)
        by = Math.min(by, bb.y)
        bX = Math.max(bX, bb.x + bb.w)
        bY = Math.max(bY, bb.y + bb.h)
      }
    }
    store.beginGroup()
    drag = { kind: 'move', ids: [...orig.keys()], sx: pt.x, sy: pt.y, orig, selBox: { x: bx, y: by, w: bX - bx, h: bY - by } }
  }

  const onPointerMove = (e: PointerEvent) => {
    if (!drag) {
      // Arrow/line tool idle: preview the connection handles on the shape under the
      // cursor (so you can see where an arrow will start/attach before pressing).
      const t = store.instance().tool
      if (t === 'arrow' || t === 'line') {
        bindHighlight(bindTargetAt(screenToPage({ x: e.clientX, y: e.clientY })) || null)
      } else if (bindHighlight()) {
        bindHighlight(null)
      }
      return
    }
    if (gesturing()) {
      abortDrag()
      return
    }
    const pt = screenToPage({ x: e.clientX, y: e.clientY })
    if (drag.kind === 'move') {
      const d = drag
      const snapped = applySnap(d.selBox, pt.x - drag.sx, pt.y - drag.sy, d.ids, e.altKey)
      store.transact('local', () => {
        for (const id of d.ids) {
          const o = d.orig.get(id)!
          store.patch(id, { x: o.x + snapped.dx, y: o.y + snapped.dy })
        }
      })
    } else if (drag.kind === 'marquee') {
      const x = Math.min(drag.sx, pt.x)
      const y = Math.min(drag.sy, pt.y)
      const w = Math.abs(pt.x - drag.sx)
      const h = Math.abs(pt.y - drag.sy)
      marquee({ x, y, w, h })
      // A click (or micro-drag) is NOT a marquee — only start selecting once it's
      // dragged a few px. Without this, clicking inside a hollow shape would
      // bbox-select it via a zero-size marquee instead of passing through.
      const TH = 4 / store.camera().z
      if (w < TH && h < TH) return
      const inside = recordsInRect({ x, y, w, h })
      const merged = drag.base.length ? Array.from(new Set([...drag.base, ...inside])) : inside
      setSelection(expandGroups(merged))
    } else if (drag.kind === 'geo') {
      const x = Math.min(drag.sx, pt.x)
      const y = Math.min(drag.sy, pt.y)
      const w = Math.max(1, Math.abs(pt.x - drag.sx))
      const h = Math.max(1, Math.abs(pt.y - drag.sy))
      const id = drag.id
      store.transact('local', () => store.patch(id, { x, y, props: { w, h } }))
    } else if (drag.kind === 'line') {
      const id = drag.id
      const ax = pt.x - drag.sx
      const ay = pt.y - drag.sy
      store.transact('local', () =>
        store.patch(id, {
          props: {
            points: { a1: { id: 'a1', index: 'a1', x: 0, y: 0 }, a2: { id: 'a2', index: 'a2', x: ax, y: ay } },
            w: Math.abs(ax),
            h: Math.abs(ay),
          },
        }),
      )
      // highlight the shape the end would bind to + show the attach dot
      const tgt = bindTargetAt(pt, id)
      bindHighlight(tgt || null)
      bindAnchorDot(tgt ? bindAnchorAt(pt, tgt)?.dot || null : null)
    } else if (drag.kind === 'draw') {
      const lx = pt.x - drag.sx
      const ly = pt.y - drag.sy
      // Decimate: skip a point within ~2 screen px of the last kept one. A slow
      // drag otherwise pushes a point per pointermove (up to 120Hz) and re-renders
      // the whole perfect-freehand path over the growing array each time — O(n^2)
      // over the stroke's life, and the full list is synced to Python + persisted.
      // The threshold is in page units (screen px ÷ zoom) so fidelity is constant
      // on screen at any zoom.
      const last = drag.pts[drag.pts.length - 1]
      const minD = 2 / store.camera().z
      if (last && Math.hypot(lx - last.x, ly - last.y) < minD) return
      drag.pts.push({ x: lx, y: ly })
      const id = drag.id
      const pts = drag.pts
      store.transact('local', () => store.patch(id, { props: { points: pts.slice() } }))
    } else if (drag.kind === 'label') {
      // move the caption along its line/arrow (pointer projected onto the chord)
      const id = drag.id
      const t = labelParamAt(store.peek(id), pt)
      if (t != null) store.transact('local', () => store.patch(id, { props: { labelPosition: t } }))
    } else if (drag.kind === 'erase') {
      const r = ERASE_SCREEN_R / store.camera().z
      const hit = hitTestErase(pt, r)
      if (hit && !erasingIds().has(hit)) {
        const marked = new Set(erasingIds())
        marked.add(hit)
        erasingIds(marked)
      }
      const trail = eraseTrail()
      const next = [...trail, { x: pt.x, y: pt.y }]
      eraseTrail(next.length > 18 ? next.slice(next.length - 18) : next)
    }
  }

  const onPointerUp = () => {
    container.classList.remove('pc-gesturing') // end of gesture — iframes interactive again
    if (!drag) return
    if (drag.kind === 'move') {
      store.endGroup()
      snapGuides([])
    } else if (drag.kind === 'erase') {
      // commit: remove everything marked this stroke as one undo step
      const ids = [...erasingIds()]
      if (ids.length) {
        store.beginGroup()
        store.transact('local', () => {
          for (const id of ids) if (store.has(id)) store.remove(id)
        })
        store.endGroup()
      }
      erasingIds(new Set())
      eraseTrail([])
    } else if (drag.kind === 'marquee') {
      marquee(null)
    } else if (drag.kind === 'label') {
      store.endGroup() // close the (possibly empty → discarded) label-move group
    } else if (drag.kind === 'geo' || drag.kind === 'line' || drag.kind === 'draw') {
      const id = drag.id
      bindHighlight(null)
      bindAnchorDot(null)
      // bind the arrow's END to whatever shape sits under it (reroutes on move) +
      // store the precise attach anchor.
      if (drag.kind === 'line') {
        const r = store.peek(id) as any
        const a2 = r?.props?.points?.a2
        if (a2) {
          const endPt = { x: r.x + a2.x, y: r.y + a2.y }
          const startBind = r?.props?.bindStart
          const tgt = bindTargetAt(endPt, id)
          if (tgt && tgt !== startBind) {
            const ea = bindAnchorAt(endPt, tgt)
            store.transact('local', () => store.patch(id, { props: { bindEnd: tgt, bindEndAnchor: ea?.anchor } }))
          } else if (tgt && tgt === startBind) {
            // Both ends landed on the same shape (e.g. a line drawn across one
            // panel). Binding both to it would collapse the line onto that shape's
            // anchor and it'd vanish — so drop the start binding and keep it a free
            // annotation line on top. (A true connector joins two *different* shapes.)
            store.transact('local', () => store.patch(id, { props: { bindStart: undefined, bindStartAnchor: undefined } }))
          }
        }
        // Each end keeps the connection point the user snapped to (centre → undefined
        // anchor for a clean centre-clip; an edge midpoint → that anchor), so a
        // top-to-bottom (or any N/S/E/W) connection sticks instead of collapsing to
        // centre-to-centre.
      }
      // finalize draw bbox; drop a too-tiny shape (an accidental click)
      if (drag.kind === 'draw') {
        const pts = drag.pts
        let minx = Infinity,
          miny = Infinity,
          maxx = -Infinity,
          maxy = -Infinity
        for (const p of pts) {
          minx = Math.min(minx, p.x)
          miny = Math.min(miny, p.y)
          maxx = Math.max(maxx, p.x)
          maxy = Math.max(maxy, p.y)
        }
        store.transact('local', () => store.patch(id, { props: { w: Math.max(1, maxx - minx), h: Math.max(1, maxy - miny) } }))
      }
      const rec = store.peek(id) as any
      // a line genuinely connecting two different shapes is kept even if short
      const connects = rec && rec.props.bindEnd && rec.props.bindEnd !== rec.props.bindStart
      const tiny = rec && (rec.props.w || 0) < 3 && (rec.props.h || 0) < 3 && !connects
      store.endGroup()
      if (tiny) {
        store.transact('local', () => store.remove(id))
      } else {
        // keep the pen tool for repeated strokes; other tools drop to select. The
        // new shape is NOT auto-selected (no highlight) — select it to edit it.
        const keepTool = store.instance().tool === 'draw'
        store.transact('local', () =>
          store.instance({ ...store.instance(), selectedIds: [], tool: keepTool ? 'draw' : 'select' }),
        )
      }
    }
    drag = null
  }

  const onKeyDown = (e: KeyboardEvent) => {
    const t = e.target as HTMLElement | null
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
    if (store.instance().editingId) return // editing inline — let the textarea own keys
    const mod = e.ctrlKey || e.metaKey
    if (mod && (e.key === 'z' || e.key === 'Z')) {
      e.preventDefault()
      if (e.shiftKey) store.redo()
      else store.undo()
      return
    }
    if (mod && (e.key === 'y' || e.key === 'Y')) {
      e.preventDefault()
      store.redo()
      return
    }
    // Ctrl/Cmd+C copies the selection to the clipboard as a PNG.
    if (mod && (e.key === 'c' || e.key === 'C')) {
      const ids = store.instance().selectedIds
      if (ids.length) {
        e.preventDefault()
        void copyRecordsToClipboard(ids)
      }
      return
    }
    // Ctrl/Cmd+G group, +Shift ungroup.
    if (mod && (e.key === 'g' || e.key === 'G')) {
      e.preventDefault()
      if (e.shiftKey) ungroupSelection()
      else groupSelection()
      return
    }
    if (e.key === 'Escape') {
      store.transact('local', () => store.instance({ ...store.instance(), tool: 'select', selectedIds: [], editingId: null }))
      return
    }
    // single-key tool shortcuts (whiteboard-style)
    if (!mod) {
      const map: Record<string, Tool> = { v: 'select', h: 'hand', d: 'draw', p: 'draw', r: 'rectangle', o: 'ellipse', l: 'line', a: 'arrow', t: 'text', n: 'note', e: 'eraser' }
      const tool = map[e.key.toLowerCase()]
      if (tool) {
        store.transact('local', () => store.instance({ ...store.instance(), tool }))
        return
      }
    }
    if (e.key !== 'Delete' && e.key !== 'Backspace') return
    const sel = store.instance().selectedIds
    if (!sel.length) return
    const deletable = sel.filter((id) => {
      const r = store.peek(id) as any
      return r && !(r.typeName === 'panel' && r.isLocked)
    })
    if (!deletable.length) return
    e.preventDefault()
    store.transact('local', () => {
      for (const id of deletable) store.remove(id)
      store.instance({ ...store.instance(), selectedIds: [] })
    })
  }

  // Double-click: edit an existing text/note/geo/line label, or drop a new text
  // shape on empty canvas (whiteboard-style).
  const onDblClick = (e: MouseEvent) => {
    const tool = store.instance().tool
    if (tool !== 'select' && tool !== 'text') return
    const target = e.target as HTMLElement
    if (target.closest('[data-pc-panel-id]')) return // let panels keep their dblclick
    const pt = screenToPage({ x: e.clientX, y: e.clientY })
    const hit = hitTestDrawing(pt, SELECT_SCREEN_R / store.camera().z)
    if (hit) {
      const r = store.peek(hit) as any
      if (r && r.typeName === 'drawing' && ['text', 'note', 'geo', 'line'].includes(r.shapeType)) {
        e.preventDefault()
        setEditing(hit)
      }
      return
    }
    // a connector arrow (panel-to-panel) — edit its label too
    const arrowHit = hitTestArrow(pt, ARROW_SCREEN_R / store.camera().z)
    if (arrowHit) {
      e.preventDefault()
      setEditing(arrowHit)
      return
    }
    // anywhere on a line/arrow CAPTION (its full box, matching the drag target)
    const labelHit = hitTestLabel(pt)
    if (labelHit) {
      e.preventDefault()
      setEditing(labelHit)
      return
    }
    // empty space: create + edit a fresh text shape
    e.preventDefault()
    const style = store.instance().style
    const rec = newDrawing('text', pt.x, pt.y, { text: '', color: style.color, size: style.size, w: 160, h: 36 })
    store.beginGroup()
    store.transact('local', () => store.put(rec))
    store.endGroup()
    setEditing(rec.id)
  }

  // Paste an image from the clipboard (screenshots included) -> viewport centre.
  const onPaste = (e: ClipboardEvent) => {
    if (store.instance().editingId) return
    const t = e.target as HTMLElement | null
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
    const items = e.clipboardData?.items
    if (!items) return
    for (const it of Array.from(items)) {
      if (it.type.startsWith('image/')) {
        const file = it.getAsFile()
        if (file) {
          e.preventDefault()
          void addImageFromFile(file)
        }
        return
      }
    }
  }
  // Drag an image file onto the canvas -> dropped at the cursor.
  const onDragOver = (e: DragEvent) => {
    if (e.dataTransfer && Array.from(e.dataTransfer.items || []).some((i) => i.type.startsWith('image/'))) e.preventDefault()
  }
  const onDrop = (e: DragEvent) => {
    const files = e.dataTransfer?.files
    const imgs = files ? Array.from(files).filter((f) => f.type.startsWith('image/')) : []
    if (!imgs.length) return
    e.preventDefault()
    const base = screenToPage({ x: e.clientX, y: e.clientY })
    imgs.forEach((f, i) => void addImageFromFile(f, { x: base.x + i * 16, y: base.y + i * 16 }))
  }

  container.addEventListener('pointerdown', onPointerDown)
  container.addEventListener('dblclick', onDblClick)
  container.addEventListener('dragover', onDragOver)
  container.addEventListener('drop', onDrop)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('paste', onPaste)
  return () => {
    container.removeEventListener('pointerdown', onPointerDown)
    container.removeEventListener('dblclick', onDblClick)
    container.removeEventListener('dragover', onDragOver)
    container.removeEventListener('drop', onDrop)
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', onPointerUp)
    window.removeEventListener('keydown', onKeyDown)
    window.removeEventListener('paste', onPaste)
  }
}

// z-order arrange for the current selection.
export function arrangeSelection(op: 'front' | 'back' | 'forward' | 'backward'): void {
  const sel = store.instance().selectedIds
  for (const id of sel) store.reorder(id, op)
}

// Group the selected DRAWINGS (a shared props.groupId; panels are Python-owned and
// not grouped). Ungroup clears it. One undo step each.
export function groupSelection(): void {
  const sel = store.instance().selectedIds.filter((id) => store.peek(id)?.typeName === 'drawing')
  if (sel.length < 2) return
  const gid = 'g' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
  store.beginGroup()
  store.transact('local', () => {
    for (const id of sel) store.patch(id, { props: { groupId: gid } })
  })
  store.endGroup()
}
export function ungroupSelection(): void {
  const sel = store.instance().selectedIds
  store.beginGroup()
  store.transact('local', () => {
    for (const id of sel) {
      if ((store.peek(id) as any)?.props?.groupId) store.patch(id, { props: { groupId: undefined } })
    }
  })
  store.endGroup()
}
export function selectionHasGroup(): boolean {
  return store.instance().selectedIds.some((id) => !!(store.peek(id) as any)?.props?.groupId)
}

// Align the selected records along an axis: edges to the group's min/max, or
// centres to the group centre. Works across panels + drawings (each moves by
// its bbox, so point-based shapes shift correctly). One undo step.
export function alignSelection(axis: 'x' | 'y', mode: 'start' | 'center' | 'end'): void {
  const sel = store.instance().selectedIds
  const items = sel
    .map((id) => ({ id, b: recordBBox(store.peek(id)), rec: store.peek(id) as any }))
    .filter((o) => o.b && o.rec && typeof o.rec.x === 'number') as { id: Id; b: Box; rec: any }[]
  if (items.length < 2) return
  const lo = Math.min(...items.map((o) => (axis === 'x' ? o.b.x : o.b.y)))
  const hi = Math.max(...items.map((o) => (axis === 'x' ? o.b.x + o.b.w : o.b.y + o.b.h)))
  const mid = (lo + hi) / 2
  store.beginGroup()
  store.transact('local', () => {
    for (const { id, b, rec } of items) {
      const size = axis === 'x' ? b.w : b.h
      const cur = axis === 'x' ? b.x : b.y
      const targetStart = mode === 'start' ? lo : mode === 'end' ? hi - size : mid - size / 2
      const shift = targetStart - cur
      if (Math.abs(shift) < 0.01) continue
      const pos = (axis === 'x' ? rec.x : rec.y) + shift
      store.patch(id, axis === 'x' ? { x: pos } : { y: pos })
    }
  })
  store.endGroup()
}

// Duplicate the selected drawings (panels are Python-owned — not duplicated).
export function duplicateSelection(): void {
  const sel = store.instance().selectedIds
  const newIds: Id[] = []
  store.transact('local', () => {
    for (const id of sel) {
      const r = store.peek(id) as any
      if (!r || r.typeName !== 'drawing') continue
      const copy: DrawingRecord = { ...r, id: nid(), x: r.x + 16, y: r.y + 16, props: { ...r.props } }
      store.put(copy)
      newIds.push(copy.id)
    }
    if (newIds.length) store.instance({ ...store.instance(), selectedIds: newIds })
  })
}
