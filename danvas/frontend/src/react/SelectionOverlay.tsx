// Selection UI in screen space (constant size at any zoom): a marquee rect while
// drag-selecting, an outline per selected record (panel or drawing), and resize
// handles when exactly one resizable shape is selected. Handles patch the record;
// for panels the autoH/autoW axis is locked (content-driven).
import { store } from '../engine/store'
import { pageToScreen, screenToPage } from '../engine/editor'
import { marquee, eraseTrail, snapGuides, bindHighlight, bindAnchorDot, bindAnchorAt, connectorPoints, interacting } from '../engine/interaction'
import { recordBBox, bindTargetAt } from '../engine/hittest'
import { linePathD, bendFromApex, clipConnector, clipArrow, polyPointAt, nearestPolyParam, elbowSegments, collapseElbowCoords, elbowDefaultAxis, elbowRoutePts, type ArrowKind } from '../engine/lineGeo'
import { measuredTextSize } from './measure'
import { freehandStrokePath } from './freehand'
import { useValue } from './EngineContext'
import { useRef, useEffect, useLayoutEffect } from 'preact/hooks'
import { effect } from 'alien-signals'

const MIN = 12
const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const
type Handle = (typeof HANDLES)[number]
const accent = 'var(--pc-accent, #3b82f6)'
const STROKE: Record<string, number> = { s: 2, m: 3.5, l: 6, xl: 9 }
// Rendered stroke width (page units) of a line/arrow, matching DrawingLayer's
// strokeW: user lines default to 'm'; connector arrows with no size fall back to 2.5.
const lineStrokeW = (rec: any) => (rec.props.size ? STROKE[rec.props.size as string] || 3.5 : rec.typeName === 'arrow' ? 2.5 : 3.5)
// Slightly bigger handle hit-targets on touch devices (coarse pointers).
const COARSE = typeof matchMedia !== 'undefined' && matchMedia('(pointer: coarse)').matches
const DOT = COARSE ? 8 : 6 // endpoint / circle handle radius
const BOX_HANDLE = COARSE ? 12 : 9 // resize-handle square size
const ROTATE_ZONE = COARSE ? 30 : 22 // invisible corner rotation hit-area
const FONT_PX: Record<string, number> = { s: 14, m: 20, l: 28, xl: 40 } // text px per size
// tldraw's rotate cursor: a curved arc with an arrowhead (white halo over a black
// glyph so it reads on any background). The path + drop-shadow wrapper are taken
// verbatim from tldraw's useCursor (ROTATE_CORNER_SVG / getCursorCss); we rotate it
// per corner via a `tr` offset PLUS the shape's own rotation, so the arrow always
// points tangent to the rotation — exactly how tldraw orients it.
// White body with a black halo (tldraw's dark-mode variant): fills swapped vs the
// default so the glyph reads with a light body. Path 1 = the arrow body, path 2 =
// the surrounding outline ring.
const ROTATE_CORNER_SVG =
  '<path d="M22.4789 9.45728L25.9935 12.9942L22.4789 16.5283V14.1032C18.126 14.1502 14.6071 17.6737 14.5675 22.0283H17.05L13.513 25.543L9.97889 22.0283H12.5674C12.6071 16.5691 17.0214 12.1503 22.4789 12.1031L22.4789 9.45728Z" fill="white"/>' +
  '<path fill-rule="evenodd" clip-rule="evenodd" d="M21.4789 7.03223L27.4035 12.9945L21.4789 18.9521V15.1868C18.4798 15.6549 16.1113 18.0273 15.649 21.0284H19.475L13.5128 26.953L7.55519 21.0284H11.6189C12.1243 15.8155 16.2679 11.6677 21.4789 11.1559L21.4789 7.03223ZM22.4789 12.1031C17.0214 12.1503 12.6071 16.5691 12.5674 22.0284H9.97889L13.513 25.543L17.05 22.0284H14.5675C14.5705 21.6896 14.5947 21.3558 14.6386 21.0284C15.1157 17.4741 17.9266 14.6592 21.4789 14.1761C21.8063 14.1316 22.1401 14.1069 22.4789 14.1032V16.5284L25.9935 12.9942L22.4789 9.45729L22.4789 12.1031Z" fill="black"/>'
// Per-corner base rotation (degrees), clockwise from the top-left, matching
// tldraw's nwse/nesw/senw/swne-rotate entries.
const ROTATE_TR: Record<'nw' | 'ne' | 'se' | 'sw', number> = { nw: 0, ne: 90, se: 180, sw: 270 }
// Build the CSS cursor value for the rotate handle at base offset `tr`, with the
// shape rotated `r` degrees. Mirrors tldraw's getCursorCss (incl. the angle-tracking
// drop shadow); hot-spot centred at 16,16.
function rotateCursorCss(r: number, tr: number): string {
  const a = (-tr - r) * (Math.PI / 180)
  const dx = Math.cos(a) - Math.sin(a)
  const dy = Math.sin(a) + Math.cos(a)
  return (
    "url(\"data:image/svg+xml,<svg height='32' width='32' viewBox='0 0 32 32' xmlns='http://www.w3.org/2000/svg' style='color: black;'>" +
    "<defs><filter id='shadow' y='-40%' x='-40%' width='180px' height='180%' color-interpolation-filters='sRGB'>" +
    `<feDropShadow dx='${dx.toFixed(3)}' dy='${dy.toFixed(3)}' stdDeviation='1.2' flood-opacity='.5'/></filter></defs>` +
    `<g fill='none' transform='rotate(${r + tr} 16 16)' filter='url(%23shadow)'>` +
    ROTATE_CORNER_SVG.replace(/"/g, "'") +
    "</g></svg>\") 16 16, pointer"
  )
}
const fullSvg: any = { position: 'absolute', left: 0, top: 0, width: '100%', height: '100%', overflow: 'visible', pointerEvents: 'none' }

// Screen-space box covering a line/arrow's rendered caption, so the text itself is
// a drag target (move the label) + double-click target (edit) — no hunting for a
// tiny handle. Centred on the label point `lp`; sized from the text + font.
function labelBox(text: string, size: string | undefined, z: number, lp: { x: number; y: number }) {
  const lines = String(text || '').split('\n')
  const maxLen = Math.max(1, ...lines.map((l) => l.length))
  const fpx = (FONT_PX[size || 'm'] ?? 20) * z
  const w = Math.max(30, maxLen * fpx * 0.62)
  const h = Math.max(fpx * 1.25, lines.length * fpx * 1.3)
  return { x: lp.x - w / 2, y: lp.y - h / 2, w, h }
}
// Bend-handle position: at the apex normally, but nudged a fixed screen distance
// perpendicular to the chord when a caption sits there — so the handle stays
// grabbable (rendered on top) WITHOUT covering the text, leaving the caption free
// to be dragged.
function bendHandlePos(apex: { x: number; y: number }, a: { x: number; y: number }, b: { x: number; y: number }, bend: number, hasLabel: boolean) {
  if (!hasLabel) return apex
  const dx = b.x - a.x
  const dy = b.y - a.y
  const ln = Math.hypot(dx, dy) || 1
  const sign = bend < 0 ? -1 : 1
  const off = COARSE ? 26 : 20
  return { x: apex.x + (-dy / ln) * off * sign, y: apex.y + (dx / ln) * off * sign }
}
// Multi-bend orthogonal route handles for an elbow arrow (line OR connector). One
// handle per draggable segment: drag perpendicular to MOVE that bend; DOUBLE-CLICK a
// segment to ADD a bend (a jog) there; on release, tiny segments COLLAPSE away. The
// route is stored as props.elbowCoords (+ elbowAxis) and stays orthogonal.
function ElbowHandles({ rec, cA, cB }: { rec: any; cA: { x: number; y: number }; cB: { x: number; y: number } }) {
  const splitOf = (r: any) => (typeof r.props.elbowSplit === 'number' ? r.props.elbowSplit : 0.5)
  const { segs } = elbowSegments(cA, cB, splitOf(rec), rec.props.elbowCoords, rec.props.elbowAxis)
  const drag = (onMove: (pt: { x: number; y: number }) => void, onEnd?: () => void) => (e: PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    store.beginGroup()
    const move = (ev: PointerEvent) => onMove(screenToPage({ x: ev.clientX, y: ev.clientY }))
    const up = () => {
      store.endGroup()
      onEnd?.()
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  const moveSeg = (coordIndex: number, orient: 'h' | 'v') =>
    drag(
      (pt) => {
        const live = clipArrow(store.peek(rec.id))
        const cur = store.peek(rec.id) as any
        if (!live) return
        const seg = elbowSegments(live.cA, live.cB, splitOf(cur), cur.props.elbowCoords, cur.props.elbowAxis)
        const cs = seg.coords.slice()
        cs[coordIndex] = orient === 'h' ? pt.y : pt.x
        store.transact('local', () => store.patch(rec.id, { props: { elbowCoords: cs, elbowAxis: seg.axis } }))
      },
      () => {
        const live = clipArrow(store.peek(rec.id))
        const cur = store.peek(rec.id) as any
        if (!live || !cur.props.elbowCoords) return
        const min = 22 / store.camera().z
        const collapsed = collapseElbowCoords(live.cA, live.cB, splitOf(cur), cur.props.elbowCoords, cur.props.elbowAxis || elbowDefaultAxis(live.cA, live.cB), min)
        store.transact('local', () => store.patch(rec.id, { props: { elbowCoords: collapsed } }))
      },
    )
  // Double-click a segment to ADD a bend: split it with a small perpendicular jog
  // (offset so the new bend is visible & grabbable, not a zero-area degenerate one).
  const addBend = (coordIndex: number, orient: 'h' | 'v') => (e: any) => {
    e.preventDefault()
    e.stopPropagation()
    const live = clipArrow(store.peek(rec.id))
    const cur = store.peek(rec.id) as any
    if (!live) return
    const seg = elbowSegments(live.cA, live.cB, splitOf(cur), cur.props.elbowCoords, cur.props.elbowAxis)
    const pt = screenToPage({ x: e.clientX, y: e.clientY })
    const off = 40 / store.camera().z
    const ncs = seg.coords.slice()
    // first new coord = the turn at the click point (along the segment), second =
    // the perpendicular hop (offset away so the jog has real length)
    ncs.splice(coordIndex + 1, 0, ...(orient === 'v' ? [pt.y, pt.x + off] : [pt.x, pt.y + off]))
    store.transact('local', () => store.patch(rec.id, { props: { elbowCoords: ncs, elbowAxis: seg.axis } }))
  }
  return (
    <>
      {segs
        .filter((s) => s.coordIndex >= 0 && s.len > 1)
        .map((s, k) => {
          const m = pageToScreen(s.mid)
          return (
            <rect
              key={k}
              x={m.x - (COARSE ? 8 : 5)}
              y={m.y - (COARSE ? 8 : 5)}
              width={COARSE ? 16 : 10}
              height={COARSE ? 16 : 10}
              rx={2}
              fill={accent}
              stroke="#fff"
              strokeWidth={1.5}
              style={{ pointerEvents: 'all', cursor: s.orient === 'h' ? 'ns-resize' : 'ew-resize' }}
              onPointerDown={moveSeg(s.coordIndex, s.orient) as any}
              onDblClick={addBend(s.coordIndex, s.orient) as any}
            />
          )
        })}
    </>
  )
}

// Open the inline editor on a line/arrow label.
const editLabelOf = (id: string) => store.transact('local', () => store.instance({ ...store.instance(), selectedIds: [id], editingId: id }))
// Reset a line/arrow to a straight, un-bent path (double-click its bend handle).
const straightenOf = (id: string) => (e: any) => {
  e.preventDefault()
  e.stopPropagation()
  store.transact('local', () => store.patch(id, { props: { bend: 0, arrowKind: 'straight' } }))
}

// SVG path for the faint centre-to-centre selection skeleton. For an elbow it walks
// the full rectilinear route (cA→cB in PAGE space, honouring elbowCoords/axis) and
// maps each corner to screen, so the dotted guide overlays the right-angle arrow it
// belongs to. Other kinds fall back to linePathD on the screen-space anchors.
function elbowSkeletonD(kind: ArrowKind, cA: { x: number; y: number }, cB: { x: number; y: number }, split: number, coords: number[] | undefined, axis: 'h' | 'v' | undefined, cAs: { x: number; y: number }, cBs: { x: number; y: number }, bz: number): string {
  if (kind !== 'elbow') return linePathD(cAs, cBs, bz, kind, split)
  const pts = elbowRoutePts(cA, cB, split, coords, axis).map(pageToScreen)
  return 'M ' + pts.map((p) => `${p.x},${p.y}`).join(' L ')
}

export function SelectionOverlay() {
  const selectedIds = useValue('sel-ids', () => store.instance().selectedIds, [])
  const mq = useValue('sel-marquee', () => marquee(), [])
  // Hide the boxes/handles while a selected entity is being modified (and ~1s
  // after) so the user can see their change unobstructed.
  const busy = useValue('sel-busy', () => interacting(), [])
  // Subscribe to ZOOM only (not pan). A pan keeps every handle's screen offset a
  // constant translate (screen = (page+cam)·z), so it's applied imperatively to
  // boxLayer below — re-rendering the boxes on every pan frame is what we avoid.
  // A zoom changes the page→screen scale non-uniformly, so it must re-render.
  useValue('sel-z', () => store.camera().z, [])
  const single = selectedIds.length === 1 ? selectedIds[0] : null

  // The pan-follow layer for the (idle-selection) boxes + badge. Its children are
  // positioned in screen space at the camera captured on the last render (baseRef);
  // an effect translates the whole layer by the live pan delta so it tracks a pan
  // without a React render. Reset to identity after each render (base just moved).
  const boxLayerRef = useRef<HTMLDivElement>(null)
  const baseRef = useRef(store.camera())
  useLayoutEffect(() => {
    baseRef.current = store.camera()
    if (boxLayerRef.current) boxLayerRef.current.style.transform = ''
  })
  useEffect(() => {
    const stop = effect(() => {
      const c = store.camera() // tracked
      const el = boxLayerRef.current
      if (!el) return
      const base = baseRef.current
      // Pure pan (same zoom) → exact translate. On zoom the sel-z subscription
      // re-renders with fresh screen coords, so just drop to identity here.
      el.style.transform = c.z === base.z ? `translate(${(c.x - base.x) * c.z}px, ${(c.y - base.y) * c.z}px)` : ''
    })
    return stop
  }, [])

  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 250 }}>
      {mq && <Marquee />}
      <div ref={boxLayerRef} style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
        {!busy &&
          selectedIds.map((id) => (
            <SelectionBox key={id} id={id} single={id === single} />
          ))}
        {/* size badge — NOT gated by `busy`, so it stays (and updates) while resizing */}
        {single && <DimensionBadge id={single} />}
      </div>
      <SnapGuides />
      <BindHighlight />
      <ConnectorHandles />
      <BindAnchorDot />
      <EraserTrail />
    </div>
  )
}

// A small size badge (W × H in page units) below a selected panel / box-shape.
// Rendered ungated so it stays visible AND updates live while the shape is resized
// (whiteboard-style). Lines/arrows/ink have no box, so they're skipped.
function DimensionBadge({ id }: { id: string }) {
  const rec = useValue('dim:' + id, () => store.get(id), [id])
  // Camera read untracked — parent re-renders on zoom and pan-translates the layer.
  if (!rec) return null
  const r = rec as any
  const isBox = r.typeName === 'panel' || (r.typeName === 'drawing' && ['geo', 'note', 'text', 'frame', 'image'].includes(r.shapeType))
  if (!isBox) return null
  const b = recordBBox(r)
  if (!b) return null
  const z = store.camera().z
  const rot = r.rotation || 0
  // Position the badge under the ROTATED shape's lowest point but keep it HORIZONTAL
  // (it must not rotate with the shape): take the shape centre's screen x and the
  // max screen y across the rotated corners.
  const cx = b.x + b.w / 2
  const cy = b.y + b.h / 2
  const cos = Math.cos(rot)
  const sin = Math.sin(rot)
  let maxY = -Infinity
  for (const [px, py] of [[b.x, b.y], [b.x + b.w, b.y], [b.x + b.w, b.y + b.h], [b.x, b.y + b.h]] as const) {
    const s = pageToScreen({ x: cx + (px - cx) * cos - (py - cy) * sin, y: cy + (px - cx) * sin + (py - cy) * cos })
    if (s.y > maxY) maxY = s.y
  }
  const center = pageToScreen({ x: cx, y: cy })
  return (
    <div
      style={{
        position: 'absolute',
        left: center.x,
        top: maxY + 8,
        transform: 'translateX(-50%)',
        padding: '2px 7px',
        borderRadius: 6,
        background: 'var(--ui-accent, #2563eb)',
        color: '#fff',
        fontSize: 11,
        fontWeight: 600,
        fontFamily: 'system-ui, sans-serif',
        whiteSpace: 'nowrap',
        lineHeight: 1.45,
        boxShadow: '0 1px 4px rgba(0,0,0,0.3)',
        pointerEvents: 'none',
      }}
    >
      {Math.round(b.w)} × {Math.round(b.h)}
    </div>
  )
}

// The connection handles (top/right/bottom/left midpoints) on the shape an arrow is
// about to bind to — shown while drawing/dragging an arrow end or hovering a shape
// with the arrow/line tool. An end snaps to the nearest of these (or the centre);
// the snapped one is then marked by the brighter BindAnchorDot drawn on top.
function ConnectorHandles() {
  const id = useValue('connect-hl', () => bindHighlight(), [])
  useValue('connect-cam', () => store.camera(), [])
  if (!id) return null
  const pts = connectorPoints(id)
  if (!pts.length) return null
  return (
    <svg style={fullSvg}>
      {pts.map((p, i) => {
        const s = pageToScreen(p)
        return <circle key={i} cx={s.x} cy={s.y} r={DOT - 1} fill="#fff" stroke={accent} strokeWidth={1.5} />
      })}
    </svg>
  )
}

// Faint dot showing exactly where an arrow end will attach on a panel/shape.
function BindAnchorDot() {
  const dot = useValue('bind-dot', () => bindAnchorDot(), [])
  useValue('bind-dot-cam', () => store.camera(), [])
  if (!dot) return null
  const s = pageToScreen(dot)
  return (
    <svg style={fullSvg}>
      <circle cx={s.x} cy={s.y} r={5} fill={accent} fillOpacity={0.45} stroke="#fff" strokeOpacity={0.7} strokeWidth={1.5} />
    </svg>
  )
}

// Outlines the shape an arrow endpoint is about to bind to (whiteboard-style), driven
// by the bindHighlight signal set while drawing/dragging an arrow end.
function BindHighlight() {
  const id = useValue('bind-hl', () => bindHighlight(), [])
  useValue('bind-cam', () => store.camera(), [])
  if (!id) return null
  const r = store.peek(id) as any
  const b = recordBBox(r)
  if (!b) return null
  const tl = pageToScreen({ x: b.x, y: b.y })
  const z = store.camera().z
  // follow the shape's rotation (recordBBox is the upright local box; the shape is
  // drawn rotated about its centre) so the highlight matches the actual rotated shape
  const rot = r?.rotation || 0
  return (
    <div
      style={{
        position: 'absolute',
        left: tl.x - 2,
        top: tl.y - 2,
        width: b.w * z + 4,
        height: b.h * z + 4,
        border: `2px solid ${accent}`,
        borderRadius: 8,
        boxShadow: '0 0 0 3px rgba(59,130,246,0.22)',
        pointerEvents: 'none',
        transform: rot ? `rotate(${rot}rad)` : undefined,
        transformOrigin: 'center',
      }}
    />
  )
}

// Alignment guides shown while a move snaps (rose dashed lines across the viewport).
function SnapGuides() {
  const guides = useValue('snap', () => snapGuides(), [])
  useValue('snap-cam', () => store.camera(), [])
  if (!guides.length) return null
  return (
    <svg style={fullSvg}>
      {guides.map((g, i) => {
        if (g.axis === 'x') {
          const sx = pageToScreen({ x: g.coord, y: 0 }).x
          return <line key={i} x1={sx} y1={0} x2={sx} y2={window.innerHeight} stroke="#f43f5e" strokeWidth={1} strokeDasharray="4 4" />
        }
        const sy = pageToScreen({ x: 0, y: g.coord }).y
        return <line key={i} x1={0} y1={sy} x2={window.innerWidth} y2={sy} stroke="#f43f5e" strokeWidth={1} strokeDasharray="4 4" />
      })}
    </svg>
  )
}

function Marquee() {
  const cam = useValue('mq-cam', () => store.camera(), [])
  const m = useValue('mq', () => marquee(), [])
  if (!m) return null
  const tl = pageToScreen({ x: m.x, y: m.y })
  return (
    <div
      style={{
        position: 'absolute',
        left: tl.x,
        top: tl.y,
        width: m.w * cam.z,
        height: m.h * cam.z,
        border: `1px solid ${accent}`,
        background: 'rgba(59,130,246,0.08)',
        borderRadius: 2,
      }}
    />
  )
}

function SelectionBox({ id, single }: { id: string; single: boolean }) {
  // Camera read UNTRACKED (no pan re-render): the parent re-renders us on zoom
  // (sel-z) and pan-translates our layer, so subscribing here would be redundant.
  const cam = store.camera()
  const rec = useValue('sel-rec:' + id, () => store.get(id), [id])
  // While this shape is being text-edited, show only the caret (TextEditor) — no
  // box/handles. The box returns when it's merely selected.
  const editing = useValue('sel-edit:' + id, () => store.instance().editingId === id, [id])
  if (!rec || editing) return null
  const r = rec as any
  // Freehand ink hugs the stroke outline rather than a bounding rectangle.
  if (r.typeName === 'drawing' && (r.shapeType === 'draw' || r.shapeType === 'highlight')) {
    return <FreehandOutline rec={r} z={cam.z} />
  }
  // Lines/arrows show draggable endpoint handles instead of a box.
  if (r.typeName === 'drawing' && r.shapeType === 'line') {
    return <LineSelection rec={r} z={cam.z} single={single} />
  }
  // Connector arrows (canvas.connect) get the same handles, routed off the panels
  // they bind to — so they're as controllable as a user-drawn arrow.
  if (r.typeName === 'arrow') {
    return <ArrowSelection rec={r} z={cam.z} single={single} />
  }
  const b = recordBBox(rec)
  if (!b) return null
  const tl = pageToScreen({ x: b.x, y: b.y })
  const w = b.w * cam.z
  const h = b.h * cam.z
  // When the shape is small on screen (zoomed out / tiny), drop the edge handles
  // and keep only the corners so the handles don't swamp the shape.
  const cornersOnly = w < 46 || h < 46

  const isPanel = (rec as any).typeName === 'panel'
  const drawType = (rec as any).typeName === 'drawing' ? (rec as any).shapeType : null
  // resizable when single-selected and the shape has a real box (not line/draw).
  const resizable =
    single && !(rec as any).meta?.lockResize && (isPanel || ['geo', 'note', 'text', 'frame', 'image'].includes(drawType))
  // Content-fitting panels (h="auto" / w="auto") still get full resize handles: a
  // manual drag of the auto axis PINS it (autoH/autoW → false in onMove) so the
  // user's size wins over the content fit, like resizing any other shape. An axis
  // that's auto but NOT being dragged is left to fitNative (writing it here would
  // fight the async content measure and flicker the box).
  const autoH = isPanel && !!(rec as any).props.autoH
  const autoW = isPanel && !!(rec as any).props.autoW

  // A text drawing scales its FONT when resized vertically (whiteboard-style) instead
  // of just stretching the box; horizontal-only drags change the wrap width.
  const isText = drawType === 'text'
  const origFont = isText ? (r.props.fontSize ?? FONT_PX[r.props.size as string] ?? 20) : 0

  const onHandleDown = (handle: Handle) => (e: PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    store.beginGroup()
    const start = { x: e.clientX, y: e.clientY }
    const orig = { x: (rec as any).x, y: (rec as any).y, w: b.w, h: b.h }
    const dragsW = handle.includes('e') || handle.includes('w')
    const dragsH = handle.includes('n') || handle.includes('s')
    const onMove = (ev: PointerEvent) => {
      const z = store.camera().z
      const dx = (ev.clientX - start.x) / z
      const dy = (ev.clientY - start.y) / z
      let nx = orig.x,
        ny = orig.y,
        nw = orig.w,
        nh = orig.h
      if (handle.includes('e')) nw = orig.w + dx
      if (handle.includes('w')) nw = orig.w - dx
      if (handle.includes('s')) nh = orig.h + dy
      if (handle.includes('n')) nh = orig.h - dy
      nw = Math.max(MIN, nw)
      nh = Math.max(MIN, nh)
      if (handle.includes('w')) nx = orig.x + (orig.w - nw)
      if (handle.includes('n')) ny = orig.y + (orig.h - nh)
      // Text box resize: a SIDE handle (left/right) sets the wrap WIDTH and the text
      // reflows — font unchanged; the height re-measures to fit the wrapped lines. A
      // CORNER scales the whole text uniformly (font + box). Top/bottom handles are
      // hidden for text (height is content-driven).
      if (isText) {
        const hasW = handle.includes('e') || handle.includes('w')
        const hasH = handle.includes('n') || handle.includes('s')
        if (hasW && !hasH) {
          const fw = Math.max(MIN, nw)
          const fx = handle.includes('w') ? orig.x + (orig.w - fw) : orig.x
          const mh = measuredTextSize((r as any).props.text || '', origFont, fw).h
          store.transact('local', () => store.patch(id, { x: fx, props: { w: fw, h: Math.max(origFont * 1.3, mh), autoSize: false } as any }))
          return
        }
        const sx = orig.w ? nw / orig.w : 1
        const sy = orig.h ? nh / orig.h : 1
        const scale = Math.max(0.2, hasW && hasH ? Math.sqrt(sx * sy) : sy)
        const fw = Math.max(MIN, orig.w * scale)
        const fh = Math.max(MIN, orig.h * scale)
        const fx = handle.includes('w') ? orig.x + (orig.w - fw) : orig.x
        const fy = handle.includes('n') ? orig.y + (orig.h - fh) : orig.y
        const nf = Math.max(6, Math.round(origFont * scale))
        store.transact('local', () => store.patch(id, { x: fx, y: fy, props: { w: fw, h: fh, fontSize: nf } }))
        return
      }
      // Write each axis only when the gesture drags it OR it isn't content-fit. A
      // content-fit axis that isn't being dragged stays owned by fitNative — writing
      // it here every frame would fight the async measure and flicker the box. A
      // content-fit axis that IS dragged gets pinned (auto → false), so the manual
      // size sticks (synced to Python via the geometry read-back) instead of the
      // content fit snapping it back.
      const patch: any = { props: {} }
      if (dragsW || !autoW) {
        patch.props.w = nw
        if (handle.includes('w')) patch.x = nx
        if (dragsW && autoW) patch.props.autoW = false
      }
      if (dragsH || !autoH) {
        patch.props.h = nh
        if (handle.includes('n')) patch.y = ny
        if (dragsH && autoH) patch.props.autoH = false
      }
      store.transact('local', () => store.patch(id, patch))
    }
    const onUp = () => {
      store.endGroup()
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  // Every resizable shape (panels included, even content-fit ones) offers all eight
  // handles; dragging the auto axis pins it (see onMove). Text is the only special
  // case, trimmed in the handle filter below.

  // Rotate around the shape centre; hold Shift to snap to 15°. The selection box
  // itself is rotated to match, so the box + handles track the rotated shape.
  const rotation = (rec as any).rotation || 0
  const rotationDeg = (rotation * 180) / Math.PI
  const startRotate = (e: PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    store.beginGroup()
    const center = pageToScreen({ x: b.x + b.w / 2, y: b.y + b.h / 2 })
    const a0 = Math.atan2(e.clientY - center.y, e.clientX - center.x)
    const r0 = (store.peek(id) as any).rotation || 0
    const onMove = (ev: PointerEvent) => {
      const a = Math.atan2(ev.clientY - center.y, ev.clientX - center.x)
      let nr = r0 + (a - a0)
      if (ev.shiftKey) nr = Math.round(nr / (Math.PI / 12)) * (Math.PI / 12)
      store.transact('local', () => store.patch(id, { rotation: nr }))
    }
    const onUp = () => {
      store.endGroup()
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  return (
    <div
      data-pc-selbox={id}
      style={{ position: 'absolute', left: tl.x, top: tl.y, width: w, height: h, pointerEvents: 'none', transform: rotation ? `rotate(${rotation}rad)` : undefined, transformOrigin: 'center' }}
    >
      <div style={{ position: 'absolute', inset: 0, border: `1.5px solid ${accent}`, borderRadius: 6, boxSizing: 'border-box' }} />
      {/* whiteboard-style rotation: invisible zones just OUTSIDE each corner; hover
          shows a curved double-arrow cursor, drag rotates. Rendered before the
          resize handles so the (smaller) corner handles sit on top for resizing. */}
      {resizable &&
        (['nw', 'ne', 'se', 'sw'] as const).map((c) => {
          // Sit the rotation zone just OUTSIDE the corner (flush), so it no longer
          // pokes into the box interior where the resize handle's grab area lives —
          // rotation is grabbed from the ring beyond the corner, resize from on it.
          const z = ROTATE_ZONE
          const pos: any = { position: 'absolute', width: z, height: z, cursor: rotateCursorCss(rotationDeg, ROTATE_TR[c]), pointerEvents: 'all' }
          if (c === 'nw') Object.assign(pos, { left: -z, top: -z })
          if (c === 'ne') Object.assign(pos, { right: -z, top: -z })
          if (c === 'se') Object.assign(pos, { right: -z, bottom: -z })
          if (c === 'sw') Object.assign(pos, { left: -z, bottom: -z })
          return <div key={'rot-' + c} data-pc-rotate={c} onPointerDown={startRotate as any} style={pos} />
        })}
      {resizable &&
        HANDLES.filter((hd) => {
          // text: corners (scale) + left/right (wrap width) always; never top/bottom
          // (height is content-driven), and ignore cornersOnly so a short single-line
          // box still offers the side handles.
          if (isText) return hd !== 'n' && hd !== 's'
          return !cornersOnly || hd.length === 2
        }).map((hd) => (
          <div key={hd} data-pc-handle={hd} onPointerDown={onHandleDown(hd) as any} style={handleStyle(hd)}>
            <div style={handleSquareStyle} />
          </div>
        ))}
    </div>
  )
}

// A selection indicator that traces a freehand stroke's outline (with a small
// constant-screen margin) instead of a rectangle. `z` is passed only to force a
// re-render on zoom; pageToScreen reads the live camera.
function FreehandOutline({ rec, z }: { rec: any; z: number }) {
  const raw = Array.isArray(rec.props.points) ? rec.props.points : rec.props.points ? Object.values(rec.props.points) : []
  if (!raw.length) return null
  const screenPts = (raw as any[]).map((p) => {
    const s = pageToScreen({ x: rec.x + p.x, y: rec.y + p.y })
    return [s.x, s.y] as [number, number]
  })
  const size = (STROKE[rec.props.size as string] || 3.5) * 2.2 * z + 9
  const d = freehandStrokePath(screenPts, size)
  // Fill-only with evenodd so a self-crossing stroke shows just its silhouette
  // perimeter (and holes for any loops) — no internal lines where the outline
  // overlaps itself, and no stroke that would trace those crossings.
  return (
    <svg style={fullSvg}>
      <path d={d} fill={accent} fillOpacity={0.22} fillRule="evenodd" stroke="none" />
    </svg>
  )
}

// Selection UI for a line/arrow: the curved highlight, draggable endpoint handles,
// a bend handle at the apex (drag to curve), and the label handle (drag along).
function LineSelection({ rec, z, single }: { rec: any; z: number; single: boolean }) {
  // Same centre-to-centre clip as connectors so the selection matches the rendered
  // (perimeter-clipped) arrow: skeleton + endpoints at the anchors, highlight = arc.
  const clip = clipArrow(rec)
  if (!clip) return null
  const kind = clip.kind
  const entries = Object.entries(rec.props.points || {}).sort((u: any, v: any) => (u[1].index < v[1].index ? -1 : 1)) as [string, any][]
  const bend = rec.props.bend || 0
  const split = typeof rec.props.elbowSplit === 'number' ? rec.props.elbowSplit : 0.5
  const bound = !!(rec.props.bindStart || rec.props.bindEnd)
  const cAs = pageToScreen(clip.cA)
  const cBs = pageToScreen(clip.cB)
  const visS = clip.visible.map(pageToScreen)
  const bz = bend * z
  // The skeleton is the UNCLIPPED centre-to-centre guide. For an elbow it must trace
  // the SAME rectilinear route the visible arrow uses (honouring elbowCoords/axis),
  // not linePathD's simple single-bend route — otherwise the dotted guide diverges
  // from the right-angle arrow it belongs to.
  const skeletonD = elbowSkeletonD(kind, clip.cA, clip.cB, split, rec.props.elbowCoords, rec.props.elbowAxis, cAs, cBs, bz)
  const highlightD = visS.length >= 2 ? 'M ' + visS.map((p: { x: number; y: number }) => `${p.x},${p.y}`).join(' L ') : ''
  const apex = pageToScreen(clip.apex)
  const endHandles = [
    { key: entries[0]?.[0] || 'a1', s: cAs, which: 'start' as const },
    { key: entries[entries.length - 1]?.[0] || 'a2', s: cBs, which: 'end' as const },
  ]
  const lt = Math.max(0, Math.min(1, rec.props.labelPosition ?? 0.5))
  const lp = visS.length >= 2 ? polyPointAt(visS, lt) : apex

  const drag = (onMove: (pt: { x: number; y: number }) => void, onEnd?: () => void) => (e: PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    store.beginGroup()
    const move = (ev: PointerEvent) => onMove(screenToPage({ x: ev.clientX, y: ev.clientY }))
    const up = () => {
      store.endGroup()
      onEnd?.()
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  // dragging an endpoint also (un)binds it + sets the precise attach anchor
  const startEndpoint = (key: string, which: 'start' | 'end') =>
    drag(
      (pt) => {
        store.transact('local', () => {
          const cur = store.peek(rec.id) as any
          if (!cur) return
          const pts: any = { ...cur.props.points, [key]: { ...cur.props.points[key], x: pt.x - cur.x, y: pt.y - cur.y } }
          const xs = Object.values(pts).map((p: any) => p.x)
          const ys = Object.values(pts).map((p: any) => p.y)
          const tgt = bindTargetAt(pt, rec.id) || undefined
          const ai = tgt ? bindAnchorAt(pt, tgt) : null
          const bindPatch = which === 'start' ? { bindStart: tgt, bindStartAnchor: ai?.anchor } : { bindEnd: tgt, bindEndAnchor: ai?.anchor }
          store.patch(rec.id, { props: { points: pts, w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys), ...bindPatch } })
          bindHighlight(tgt || null)
          bindAnchorDot(ai?.dot || null)
        })
      },
      () => {
        bindHighlight(null)
        bindAnchorDot(null)
      },
    )
  const startBend = drag((pt) => {
    const e2 = clipArrow(store.peek(rec.id))
    if (!e2) return
    const nb = bendFromApex(e2.cA, e2.cB, pt)
    store.transact('local', () => store.patch(rec.id, { props: { bend: Math.abs(nb) < 2 ? 0 : nb } }))
  })
  const startLabel = drag((pt) => {
    const e2 = clipArrow(store.peek(rec.id))
    if (!e2 || e2.visible.length < 2) return
    store.transact('local', () => store.patch(rec.id, { props: { labelPosition: nearestPolyParam(e2.visible, pt) } }))
  })

  // The caption is a drag (move) + double-click (edit) target. The bend handle sits
  // at the apex (over the caption) and is rendered LAST so it stays grabbable THROUGH
  // the text; the rest of the caption is still draggable around it.
  const lb = single && rec.props.text && visS.length >= 2 ? labelBox(rec.props.text, rec.props.size, z, lp) : null
  return (
    <svg style={fullSvg}>
      {/* faint centre-to-centre skeleton when bound (logical endpoints), then the arc */}
      {single && bound && <path d={skeletonD} fill="none" stroke={accent} strokeWidth={1.5} strokeOpacity={0.4} strokeDasharray="2 4" />}
      {/* highlight traces the stroke at the SAME thickness as the line (scaled by zoom),
          so a thin line gets a thin outline and a thick one a thick outline. */}
      {highlightD && <path d={highlightD} fill="none" stroke={accent} strokeWidth={lineStrokeW(rec) * z} strokeOpacity={0.45} strokeLinecap="round" strokeLinejoin="round" />}
      {single && kind === 'elbow' && <ElbowHandles rec={rec} cA={clip.cA} cB={clip.cB} />}
      {single &&
        endHandles.map((h) => (
          <circle key={h.key} cx={h.s.x} cy={h.s.y} r={DOT} fill="#fff" stroke={accent} strokeWidth={1.5} style={{ pointerEvents: 'all', cursor: 'grab' }} onPointerDown={startEndpoint(h.key, h.which) as any} />
        ))}
      {lb && (
        <rect x={lb.x} y={lb.y} width={lb.w} height={lb.h} rx={4} fill="transparent" style={{ pointerEvents: 'all', cursor: 'move' }} onPointerDown={startLabel as any} onDblClick={(() => editLabelOf(rec.id)) as any} />
      )}
      {single && kind !== 'elbow' && (() => {
        const bh = bendHandlePos(apex, cAs, cBs, bend, !!rec.props.text)
        return <circle cx={bh.x} cy={bh.y} r={COARSE ? 9 : 5} fill={accent} stroke="#fff" strokeWidth={1.5} style={{ pointerEvents: 'all', cursor: 'grab' }} onPointerDown={startBend as any} onDblClick={straightenOf(rec.id) as any} />
      })()}
    </svg>
  )
}

// Selection UI for a CONNECTOR arrow (canvas.connect): the curved highlight, a
// bend/elbow handle, a label handle, and endpoint handles you can drag onto a
// different panel/shape to re-route it — i.e. control like a user-drawn arrow.
// (Edits are local: there's no wire message to push an arrow's appearance back to
// Python, so they reset on reload.)
function ArrowSelection({ rec, z, single }: { rec: any; z: number; single: boolean }) {
  const startRec = useValue('as-s:' + rec.id, () => (rec.start ? store.get(rec.start) : undefined), [rec.start])
  const endRec = useValue('as-e:' + rec.id, () => (rec.end ? store.get(rec.end) : undefined), [rec.end])
  if (!startRec || !endRec) return null
  const clip = clipConnector(startRec, endRec, rec)
  if (!clip) return null
  const kind = clip.kind
  const bend = rec.props.bend || 0
  const split = typeof rec.props.elbowSplit === 'number' ? rec.props.elbowSplit : 0.5
  // skeleton = centre-to-centre (faint guide + endpoints); highlight = clipped arc
  const cAs = pageToScreen(clip.cA)
  const cBs = pageToScreen(clip.cB)
  const visS = clip.visible.map(pageToScreen)
  const bz = bend * z
  const skeletonD = elbowSkeletonD(kind, clip.cA, clip.cB, split, rec.props.elbowCoords, rec.props.elbowAxis, cAs, cBs, bz)
  const highlightD = visS.length >= 2 ? 'M ' + visS.map((p) => `${p.x},${p.y}`).join(' L ') : ''
  const apex = pageToScreen(clip.apex)
  const lt = Math.max(0, Math.min(1, rec.props.labelPosition ?? 0.5))
  const lp = visS.length >= 2 ? polyPointAt(visS, lt) : apex
  const live = () => clipConnector(store.peek((store.peek(rec.id) as any)?.start), store.peek((store.peek(rec.id) as any)?.end), store.peek(rec.id))

  const drag = (onMove: (pt: { x: number; y: number }) => void, onEnd?: () => void) => (e: PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    store.beginGroup()
    const move = (ev: PointerEvent) => onMove(screenToPage({ x: ev.clientX, y: ev.clientY }))
    const up = () => {
      store.endGroup()
      onEnd?.()
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  // drag an endpoint (at the shape centre) onto another panel/shape to re-route it
  const startEndpoint = (which: 'start' | 'end') =>
    drag(
      (pt) => {
        const tgt = bindTargetAt(pt, rec.id)
        bindHighlight(tgt || null)
        if (tgt) store.transact('local', () => store.patch(rec.id, which === 'start' ? { start: tgt } : ({ end: tgt } as any)))
      },
      () => bindHighlight(null),
    )
  const startBend = drag((pt) => {
    const e2 = live()
    if (!e2) return
    const nb = bendFromApex(e2.cA, e2.cB, pt)
    store.transact('local', () => store.patch(rec.id, { props: { bend: Math.abs(nb) < 2 ? 0 : nb } }))
  })
  const startLabel = drag((pt) => {
    const e2 = live()
    if (!e2 || e2.visible.length < 2) return
    store.transact('local', () => store.patch(rec.id, { props: { labelPosition: nearestPolyParam(e2.visible, pt) } }))
  })

  const lb = single && rec.props.text && visS.length >= 2 ? labelBox(rec.props.text, rec.props.size, z, lp) : null
  return (
    <svg style={fullSvg}>
      {/* faint centre-to-centre skeleton (logical endpoints), then the visible arc */}
      {single && <path d={skeletonD} fill="none" stroke={accent} strokeWidth={1.5} strokeOpacity={0.4} strokeDasharray="2 4" />}
      {highlightD && <path d={highlightD} fill="none" stroke={accent} strokeWidth={lineStrokeW(rec) * z} strokeOpacity={0.45} strokeLinecap="round" strokeLinejoin="round" />}
      {single && kind === 'elbow' && <ElbowHandles rec={rec} cA={clip.cA} cB={clip.cB} />}
      {single &&
        ([{ s: cAs, which: 'start' as const }, { s: cBs, which: 'end' as const }]).map((h) => (
          <circle key={h.which} cx={h.s.x} cy={h.s.y} r={DOT} fill="#fff" stroke={accent} strokeWidth={1.5} style={{ pointerEvents: 'all', cursor: 'grab' }} onPointerDown={startEndpoint(h.which) as any} />
        ))}
      {lb && (
        <rect x={lb.x} y={lb.y} width={lb.w} height={lb.h} rx={4} fill="transparent" style={{ pointerEvents: 'all', cursor: 'move' }} onPointerDown={startLabel as any} onDblClick={(() => editLabelOf(rec.id)) as any} />
      )}
      {single && kind !== 'elbow' && (() => {
        const bh = bendHandlePos(apex, cAs, cBs, bend, !!rec.props.text)
        return <circle cx={bh.x} cy={bh.y} r={COARSE ? 9 : 5} fill={accent} stroke="#fff" strokeWidth={1.5} style={{ pointerEvents: 'all', cursor: 'grab' }} onPointerDown={startBend as any} onDblClick={straightenOf(rec.id) as any} />
      })()}
    </svg>
  )
}

// The ephemeral eraser trail: a soft tail that fades toward older points plus a
// ring at the cursor showing the eraser's reach. Cleared on pointer-up.
function EraserTrail() {
  const trail = useValue('erase-trail', () => eraseTrail(), [])
  useValue('erase-cam', () => store.camera(), []) // re-render on pan/zoom
  if (!trail.length) return null
  const pts = trail.map((p) => pageToScreen(p))
  const head = pts[pts.length - 1]
  return (
    <svg style={fullSvg}>
      {pts.slice(1).map((p, i) => {
        const prev = pts[i]
        const op = 0.08 + 0.4 * (i / Math.max(1, pts.length - 1))
        return <line key={i} x1={prev.x} y1={prev.y} x2={p.x} y2={p.y} stroke="rgb(175,185,200)" strokeOpacity={op} strokeWidth={5} strokeLinecap="round" />
      })}
      <circle cx={head.x} cy={head.y} r={11} fill="rgba(200,210,225,0.16)" stroke="rgba(205,214,228,0.75)" strokeWidth={1.5} />
    </svg>
  )
}

// The grab target is a larger TRANSPARENT box centred on the handle point, with a
// small visible square (handleSquareStyle) inside it. The hit area is much bigger
// than the dot so corners are easy to grab; rendered after the rotate zones, it
// also wins the overlap with them — so aiming at a corner resizes rather than
// accidentally rotating. (Rotation stays grabbable in the ring just outside.)
const HIT = COARSE ? 28 : 20
function handleStyle(hd: Handle): any {
  const half = HIT / 2
  const base: any = {
    position: 'absolute',
    width: HIT,
    height: HIT,
    background: 'transparent',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    pointerEvents: 'all',
  }
  const mid = `calc(50% - ${half}px)`
  const map: Record<Handle, any> = {
    nw: { left: -half, top: -half, cursor: 'nwse-resize' },
    n: { left: mid, top: -half, cursor: 'ns-resize' },
    ne: { right: -half, top: -half, cursor: 'nesw-resize' },
    e: { right: -half, top: mid, cursor: 'ew-resize' },
    se: { right: -half, bottom: -half, cursor: 'nwse-resize' },
    s: { left: mid, bottom: -half, cursor: 'ns-resize' },
    sw: { left: -half, bottom: -half, cursor: 'nesw-resize' },
    w: { left: -half, top: mid, cursor: 'ew-resize' },
  }
  return { ...base, ...map[hd] }
}
// The visible square in the centre of each handle's (larger) hit area.
const handleSquareStyle: any = {
  width: BOX_HANDLE,
  height: BOX_HANDLE,
  background: '#fff',
  border: `1.5px solid ${accent}`,
  borderRadius: 2,
  boxSizing: 'border-box',
  pointerEvents: 'none',
}
