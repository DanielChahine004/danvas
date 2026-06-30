// Geometry helpers for selection. Panels are hit-tested via the DOM (their
// interactive content needs real pointer routing); drawings are hit-tested
// geometrically here, since they're display-only SVG. Marquee selection uses
// bbox intersection across both.
import { store } from './store'
import { lineSamples, connectorSamples, clipArrow, nearestPolyParam, polyPointAt, type Pt } from './lineGeo'
import type { Id } from './types'

const LABEL_FONT_PX: Record<string, number> = { s: 14, m: 20, l: 28, xl: 40 }

// The new labelPosition (0..1) for a line/arrow when its caption is dragged to
// `pt` — the nearest point on the VISIBLE (clipped) arc, so the label follows the
// actual drawn curve under the cursor.
export function labelParamAt(r: any, pt: Pt): number | null {
  const clip = clipArrow(r)
  if (!clip || clip.visible.length < 2) return null
  return nearestPolyParam(clip.visible, pt)
}

// The topmost line/arrow whose rendered caption contains `pt` (page space), so a
// label can be grabbed + dragged WITHOUT first selecting its line/arrow. Mirrors
// SelectionOverlay.labelBox but in page units (axis-aligned around the label point).
export function hitTestLabel(pt: Pt): Id | null {
  const ids = store.ids()
  for (let i = ids.length - 1; i >= 0; i--) {
    const r = store.peek(ids[i]) as any
    if (!r) continue
    const isArrow = r.typeName === 'arrow'
    const isLine = r.typeName === 'drawing' && r.shapeType === 'line'
    if ((!isArrow && !isLine) || !r.props?.text) continue
    const clip = clipArrow(r)
    if (!clip || clip.visible.length < 2) continue
    const t = Math.max(0, Math.min(1, typeof r.props.labelPosition === 'number' ? r.props.labelPosition : 0.5))
    const lp = polyPointAt(clip.visible, t)
    const lines = String(r.props.text).split('\n')
    const maxLen = Math.max(1, ...lines.map((l: string) => l.length))
    const f = LABEL_FONT_PX[r.props.size || 'm'] ?? 20
    const halfW = Math.max(14, maxLen * f * 0.62) / 2
    const halfH = Math.max(f * 1.25, lines.length * f * 1.3) / 2
    if (Math.abs(pt.x - lp.x) <= halfW && Math.abs(pt.y - lp.y) <= halfH) return ids[i]
  }
  return null
}

export interface Box {
  x: number
  y: number
  w: number
  h: number
}

// Page-space bounding box of any record, or null for records without geometry
// (connector arrows reroute off their endpoints, so they aren't directly hit).
export function recordBBox(r: any): Box | null {
  if (!r) return null
  if (r.typeName === 'panel') return { x: r.x, y: r.y, w: r.props.w || 0, h: r.props.h || 0 }
  if (r.typeName === 'drawing') {
    // Point-based shapes (freehand + lines/arrows) can extend into NEGATIVE
    // local offsets when drawn up or left — the stored w/h are unsigned
    // magnitudes, so using them places the selection box offset beside/under
    // the actual shape. Derive the bbox from the real points instead so the
    // outline (and hit-test) wraps the shape tightly. Geo/note/text/frame are
    // always anchored top-left at (x,y), so w/h are correct for them.
    const st = r.shapeType
    if (st === 'draw' || st === 'highlight' || st === 'line') {
      const pts = Array.isArray(r.props.points) ? r.props.points : r.props.points ? (Object.values(r.props.points) as any[]) : []
      if (pts.length) {
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
        return { x: r.x + minx, y: r.y + miny, w: Math.max(maxx - minx, 1), h: Math.max(maxy - miny, 1) }
      }
    }
    if (typeof r.props.w === 'number' && typeof r.props.h === 'number') {
      return { x: r.x, y: r.y, w: r.props.w, h: r.props.h }
    }
    return { x: r.x, y: r.y, w: 0, h: 0 }
  }
  return null
}

function pointInBox(px: number, py: number, b: Box, pad = 0): boolean {
  return px >= b.x - pad && px <= b.x + b.w + pad && py >= b.y - pad && py <= b.y + b.h + pad
}

// --- segment / rectangle geometry (for geometry-aware marquee selection) ------
function ccw(ax: number, ay: number, bx: number, by: number, cx: number, cy: number): boolean {
  return (cy - ay) * (bx - ax) > (by - ay) * (cx - ax)
}
function segSeg(ax: number, ay: number, bx: number, by: number, cx: number, cy: number, dx: number, dy: number): boolean {
  return ccw(ax, ay, cx, cy, dx, dy) !== ccw(bx, by, cx, cy, dx, dy) && ccw(ax, ay, bx, by, cx, cy) !== ccw(ax, ay, bx, by, dx, dy)
}
function segIntersectsRect(ax: number, ay: number, bx: number, by: number, r: Box): boolean {
  if (pointInBox(ax, ay, r) || pointInBox(bx, by, r)) return true
  const x0 = r.x,
    y0 = r.y,
    x1 = r.x + r.w,
    y1 = r.y + r.h
  return (
    segSeg(ax, ay, bx, by, x0, y0, x1, y0) ||
    segSeg(ax, ay, bx, by, x1, y0, x1, y1) ||
    segSeg(ax, ay, bx, by, x1, y1, x0, y1) ||
    segSeg(ax, ay, bx, by, x0, y1, x0, y0)
  )
}
function polylineIntersectsRect(pts: { x: number; y: number }[], r: Box): boolean {
  for (const p of pts) if (pointInBox(p.x, p.y, r)) return true
  for (let i = 0; i < pts.length - 1; i++) if (segIntersectsRect(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y, r)) return true
  return false
}

// Topmost drawing record at a page point (drawings only — panels are DOM-routed).
// Non-rectangular shapes (ink, lines, ellipses) hit by their actual geometry
// within `radius`; rectangular shapes (geo-rect, note, text, frame) by their box.
export function hitTestDrawing(pt: { x: number; y: number }, radius = 4): Id | null {
  const ids = store.ids()
  for (let i = ids.length - 1; i >= 0; i--) {
    const r = store.peek(ids[i]) as any
    if (!r || r.typeName !== 'drawing') continue
    const st = r.shapeType
    // Geo shapes (rect + ellipse) hit by their geometry: distToDrawing returns 0
    // inside a FILLED shape (hit anywhere) but the distance-to-border for a HOLLOW
    // (empty-fill) one — so clicking inside a hollow shape passes through to what's
    // underneath, and only its drawn outline selects it. note/text/image/frame use
    // their box (they're solid content).
    const useGeometry = st === 'draw' || st === 'highlight' || st === 'line' || st === 'geo'
    if (useGeometry) {
      if (distToDrawing(pt, r) <= radius + swOf(r.props.size) / 2) return ids[i]
    } else {
      const b = recordBBox(r)
      if (b && pointInBox(pt.x, pt.y, b, 4)) return ids[i]
    }
  }
  return null
}

// --- precise (geometry) hit-testing, for the eraser ---------------------------
const ERASE_STROKE: Record<string, number> = { s: 2, m: 3.5, l: 6 }
const swOf = (s?: string) => ERASE_STROKE[s || 'm'] || 3.5

// Distance from point P to the segment AB.
function segDist(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

function absPoints(r: any): { x: number; y: number }[] {
  const raw = Array.isArray(r.props.points) ? r.props.points : r.props.points ? (Object.values(r.props.points) as any[]) : []
  const ordered = r.shapeType === 'line' ? [...raw].sort((a: any, b: any) => (a.index < b.index ? -1 : 1)) : raw
  return ordered.map((p: any) => ({ x: r.x + p.x, y: r.y + p.y }))
}

// Shortest distance from a page point to a drawing's actual geometry (not its
// bbox): the stroke polyline for ink/lines, the perimeter for hollow shapes, 0
// when inside a filled shape. Used so the eraser must actually touch the mark.
function distToDrawing(pt: { x: number; y: number }, r: any): number {
  const st = r.shapeType
  if (st === 'draw' || st === 'highlight' || st === 'line') {
    // a line is sampled along its real (curved/elbow, binding-resolved) path so
    // its whole body is grabbable; ink uses its raw points.
    const pts = st === 'line' ? lineSamples(r) : absPoints(r)
    if (!pts.length) return Infinity
    if (pts.length === 1) return Math.hypot(pt.x - pts[0].x, pt.y - pts[0].y)
    let min = Infinity
    for (let i = 0; i < pts.length - 1; i++) min = Math.min(min, segDist(pt.x, pt.y, pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y))
    return min
  }
  const b = recordBBox(r)
  if (!b) return Infinity
  const filled = r.props.fill && r.props.fill !== 'none'
  const insideRect = pt.x >= b.x && pt.x <= b.x + b.w && pt.y >= b.y && pt.y <= b.y + b.h
  if (st === 'geo' && r.props.geo === 'ellipse') {
    const cx = b.x + b.w / 2
    const cy = b.y + b.h / 2
    const rx = b.w / 2 || 1
    const ry = b.h / 2 || 1
    const k = Math.sqrt(((pt.x - cx) / rx) ** 2 + ((pt.y - cy) / ry) ** 2)
    if (filled && k <= 1) return 0
    return Math.abs(k - 1) * Math.min(rx, ry) // approx distance to perimeter
  }
  // note / text / image -> filled region; geo-rect / frame -> hollow unless filled.
  const solid = filled || st === 'note' || st === 'text' || st === 'image'
  if (insideRect) {
    if (solid) return 0
    return Math.min(pt.x - b.x, b.x + b.w - pt.x, pt.y - b.y, b.y + b.h - pt.y) // dist to nearest edge
  }
  const dx = Math.max(b.x - pt.x, 0, pt.x - (b.x + b.w))
  const dy = Math.max(b.y - pt.y, 0, pt.y - (b.y + b.h))
  return Math.hypot(dx, dy)
}

// Topmost drawing actually within `radius` of the cursor (page units). Unlike
// hitTestDrawing (bbox), the eraser must touch the real mark.
export function hitTestErase(pt: { x: number; y: number }, radius: number): Id | null {
  const ids = store.ids()
  for (let i = ids.length - 1; i >= 0; i--) {
    const r = store.peek(ids[i]) as any
    if (!r || r.typeName !== 'drawing') continue
    if (distToDrawing(pt, r) <= radius + swOf(r.props.size) / 2) return ids[i]
  }
  return null
}

// Topmost panel or box-shape whose bounds contain the point — a bind target for
// an arrow endpoint (so the arrow sticks to it and reroutes when it moves).
// Lines/freehand/arrows are not bind targets.
export function bindTargetAt(pt: { x: number; y: number }, excludeId?: Id): Id | null {
  const ids = store.ids()
  for (let i = ids.length - 1; i >= 0; i--) {
    const id = ids[i]
    if (id === excludeId) continue
    const r = store.peek(id) as any
    if (!r) continue
    const ok = r.typeName === 'panel' || (r.typeName === 'drawing' && ['geo', 'note', 'text', 'image', 'frame'].includes(r.shapeType))
    if (!ok) continue
    const b = recordBBox(r)
    if (b && pointInBox(pt.x, pt.y, b)) return id
  }
  return null
}

// Topmost connector arrow within `radius` of the cursor (centre-to-centre seg).
export function hitTestArrow(pt: { x: number; y: number }, radius: number): Id | null {
  const ids = store.ids()
  for (let i = ids.length - 1; i >= 0; i--) {
    const r = store.peek(ids[i]) as any
    if (!r || r.typeName !== 'arrow') continue
    const s = store.peek(r.start)
    const e = store.peek(r.end)
    if (!s || !e) continue
    // hit-test against the rendered (curved/elbow/bent) path, not the chord
    const pts = connectorSamples(r, s, e)
    for (let k = 0; k < pts.length - 1; k++) {
      if (segDist(pt.x, pt.y, pts[k].x, pts[k].y, pts[k + 1].x, pts[k + 1].y) <= radius) return ids[i]
    }
  }
  return null
}

// Records the marquee selects. Non-rectangular shapes (ink, lines, connector
// arrows) are tested against their actual geometry — the marquee must touch or
// contain the drawn path, not just its bounding rectangle. Rectangular shapes
// (panels, geo-rect, note, text, frame, ellipse) use bbox intersection.
export function recordsInRect(rect: Box): Id[] {
  const out: Id[] = []
  const rectHit = (b: Box | null) => !!b && b.x < rect.x + rect.w && b.x + b.w > rect.x && b.y < rect.y + rect.h && b.y + b.h > rect.y
  for (const id of store.ids()) {
    const r = store.peek(id) as any
    if (!r) continue
    // grabbable=False panels are invisible to selection (matching the body-click
    // guard in interaction.ts) — a marquee must never catch them either.
    if (r.meta?.noGrab) continue
    if (r.typeName === 'arrow') {
      // test the RENDERED (edge-to-edge, curved/elbow/bent) path, not the straight
      // centre-to-centre chord — otherwise a marquee over a bent arrow misses it.
      const s = store.peek(r.start)
      const e = store.peek(r.end)
      if (s && e) {
        const pts = connectorSamples(r, s, e)
        if (pts.length && polylineIntersectsRect(pts, rect)) out.push(id)
      }
      continue
    }
    if (r.typeName === 'drawing' && (r.shapeType === 'draw' || r.shapeType === 'highlight' || r.shapeType === 'line')) {
      const pts = r.shapeType === 'line' ? lineSamples(r) : absPoints(r)
      if (polylineIntersectsRect(pts, rect)) out.push(id)
      continue
    }
    if (rectHit(recordBBox(r))) out.push(id)
  }
  return out
}
