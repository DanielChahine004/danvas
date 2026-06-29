// Geometry for user-drawn lines/arrows (shapeType 'line'). A line has two
// endpoints (a1/a2) plus an optional `bend` — the signed perpendicular offset of
// the curve's apex from the straight midpoint. bend=0 is straight; otherwise the
// line is a quadratic Bézier whose control point is placed so the apex sits at
// midpoint + perp*bend. Shared by DrawingLayer (render) and SelectionOverlay
// (the draggable bend handle) so both agree on the curve.
//
// An endpoint may also be BOUND to a shape (props.bindStart / props.bindEnd hold
// the target id). A bound end ignores its stored offset and is clipped to the
// target's edge facing the other end, so the arrow reroutes when the shape moves
// (resolveEnds reads the live target bbox from the store).
import { store } from './store'
import { recordBBox, type Box } from './hittest'

export interface Pt {
  x: number
  y: number
}

export type ArrowKind = 'straight' | 'elbow' | 'curved'

export function lineKind(rec: any): ArrowKind {
  const k = rec?.props?.arrowKind
  return k === 'elbow' || k === 'curved' ? k : 'straight'
}

// Absolute (page-space) endpoints of a line record, or null if degenerate.
export function lineAbs(rec: any): { a: Pt; b: Pt } | null {
  const raw = rec?.props?.points ? (Object.values(rec.props.points) as any[]) : []
  if (raw.length < 2) return null
  const ord = [...raw].sort((u, v) => (u.index < v.index ? -1 : 1))
  const a = ord[0]
  const b = ord[ord.length - 1]
  return { a: { x: rec.x + a.x, y: rec.y + a.y }, b: { x: rec.x + b.x, y: rec.y + b.y } }
}

// Point where the segment from the box centre toward `toward` crosses the box edge.
export function edgePoint(b: Box, toward: Pt): Pt {
  const cx = b.x + b.w / 2
  const cy = b.y + b.h / 2
  const dx = toward.x - cx
  const dy = toward.y - cy
  if (dx === 0 && dy === 0) return { x: cx, y: cy }
  const sx = dx === 0 ? Infinity : b.w / 2 / Math.abs(dx)
  const sy = dy === 0 ? Infinity : b.h / 2 / Math.abs(dy)
  const s = Math.min(sx, sy)
  return { x: cx + dx * s, y: cy + dy * s }
}

// Rotate a local offset (dx,dy) about a centre and translate to world.
function rotAdd(cx: number, cy: number, dx: number, dy: number, rot: number): Pt {
  if (!rot) return { x: cx + dx, y: cy + dy }
  const c = Math.cos(rot)
  const s = Math.sin(rot)
  return { x: cx + dx * c - dy * s, y: cy + dx * s + dy * c }
}
// World position of a normalised anchor on a (possibly rotated) shape.
function anchorWorld(rec: any, bb: Box, anchor: Pt): Pt {
  const cx = bb.x + bb.w / 2
  const cy = bb.y + bb.h / 2
  return rotAdd(cx, cy, anchor.x * bb.w - bb.w / 2, anchor.y * bb.h - bb.h / 2, rec?.rotation || 0)
}
// Edge point of a (possibly rotated) shape on the ray from its centre toward a
// world point — computed in the shape's local frame, then rotated back.
function edgeWorld(rec: any, bb: Box, toward: Pt): Pt {
  const rot = rec?.rotation || 0
  const cx = bb.x + bb.w / 2
  const cy = bb.y + bb.h / 2
  const c = Math.cos(-rot)
  const s = Math.sin(-rot)
  const dx = toward.x - cx
  const dy = toward.y - cy
  const local = edgePoint({ x: -bb.w / 2, y: -bb.h / 2, w: bb.w, h: bb.h }, { x: dx * c - dy * s, y: dx * s + dy * c })
  return rotAdd(cx, cy, local.x, local.y, rot)
}

// Endpoints honouring bindings: a bound end snaps to the target shape's edge
// facing the other end (or a precise anchor point), honouring the target's
// ROTATION so the arrow meets the actual rotated panel, not its upright bbox.
export function resolveEnds(rec: any): { a: Pt; b: Pt; sBound: boolean; eBound: boolean } | null {
  const free = lineAbs(rec)
  if (!free) return null
  const srec = rec.props.bindStart ? store.peek(rec.props.bindStart) : null
  const erec = rec.props.bindEnd ? store.peek(rec.props.bindEnd) : null
  const sbb = srec ? recordBBox(srec) : null
  const ebb = erec ? recordBBox(erec) : null
  const sAnchor = rec.props.bindStartAnchor
  const eAnchor = rec.props.bindEndAnchor
  // precise anchor → that exact (rotated) point; else edge-clip toward the other end
  const sExact = sbb && sAnchor ? anchorWorld(srec, sbb, sAnchor) : null
  const eExact = ebb && eAnchor ? anchorWorld(erec, ebb, eAnchor) : null
  const aRef = sbb ? sExact || { x: sbb.x + sbb.w / 2, y: sbb.y + sbb.h / 2 } : free.a
  const bRef = ebb ? eExact || { x: ebb.x + ebb.w / 2, y: ebb.y + ebb.h / 2 } : free.b
  return {
    a: sbb ? sExact || edgeWorld(srec, sbb, bRef) : free.a,
    b: ebb ? eExact || edgeWorld(erec, ebb, aRef) : free.b,
    sBound: !!sbb,
    eBound: !!ebb,
  }
}

// Endpoints of a CONNECTOR arrow (canvas.connect): the two records' edge points
// facing each other's centre, honouring rotation. Shared by the renderer and the
// selection overlay so they agree.
export function connectorEnds(startRec: any, endRec: any, rec?: any): { a: Pt; b: Pt } | null {
  const clip = clipConnector(startRec, endRec, rec)
  if (!clip) return null
  return { a: clip.a, b: clip.b }
}

// Is `pt` (page space) inside the record's actual shape? Rotation-aware; an ellipse
// uses the ellipse equation, everything else (rect/panel/note/text/frame) the box.
export function pointInRecord(rec: any, bb: Box, pt: Pt): boolean {
  const cx = bb.x + bb.w / 2
  const cy = bb.y + bb.h / 2
  const rot = rec?.rotation || 0
  const c = Math.cos(-rot)
  const s = Math.sin(-rot)
  const lx = (pt.x - cx) * c - (pt.y - cy) * s
  const ly = (pt.x - cx) * s + (pt.y - cy) * c
  const hw = bb.w / 2 || 1
  const hh = bb.h / 2 || 1
  if (rec?.typeName === 'drawing' && rec.shapeType === 'geo' && rec.props?.geo === 'ellipse') {
    return (lx * lx) / (hw * hw) + (ly * ly) / (hh * hh) <= 1
  }
  return Math.abs(lx) <= hw && Math.abs(ly) <= hh
}

// Outward unit surface normal of the record at perimeter point `pt` — for ellipse
// the gradient (radial-ish), for a box the nearest-edge normal. Used to point a
// right-angle arrowhead PERPENDICULAR to the surface it meets (round shapes too).
export function recordNormal(rec: any, bb: Box, pt: Pt): Pt {
  const cx = bb.x + bb.w / 2
  const cy = bb.y + bb.h / 2
  const rot = rec?.rotation || 0
  const c = Math.cos(-rot)
  const s = Math.sin(-rot)
  const lx = (pt.x - cx) * c - (pt.y - cy) * s
  const ly = (pt.x - cx) * s + (pt.y - cy) * c
  const hw = bb.w / 2 || 1
  const hh = bb.h / 2 || 1
  let nx: number
  let ny: number
  if (rec?.typeName === 'drawing' && rec.shapeType === 'geo' && rec.props?.geo === 'ellipse') {
    nx = lx / (hw * hw)
    ny = ly / (hh * hh)
  } else if (Math.abs(lx) / hw >= Math.abs(ly) / hh) {
    nx = Math.sign(lx) || 1
    ny = 0
  } else {
    nx = 0
    ny = Math.sign(ly) || 1
  }
  const cc = Math.cos(rot)
  const ss = Math.sin(rot)
  const wx = nx * cc - ny * ss
  const wy = nx * ss + ny * cc
  const len = Math.hypot(wx, wy) || 1
  return { x: wx / len, y: wy / len }
}

// Fraction (0..1, by arc length) of the point on a polyline nearest `pt`.
export function nearestPolyParam(pts: Pt[], pt: Pt): number {
  if (pts.length < 2) return 0
  const seg: number[] = []
  let total = 0
  for (let i = 0; i < pts.length - 1; i++) {
    const d = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y)
    seg.push(d)
    total += d
  }
  let best = 0
  let bestD = Infinity
  let acc = 0
  for (let i = 0; i < pts.length - 1; i++) {
    const ax = pts[i].x
    const ay = pts[i].y
    const dx = pts[i + 1].x - ax
    const dy = pts[i + 1].y - ay
    const l2 = dx * dx + dy * dy || 1
    let f = ((pt.x - ax) * dx + (pt.y - ay) * dy) / l2
    f = f < 0 ? 0 : f > 1 ? 1 : f
    const d = (pt.x - (ax + dx * f)) ** 2 + (pt.y - (ay + dy * f)) ** 2
    if (d < bestD) {
      bestD = d
      best = (acc + seg[i] * f) / (total || 1)
    }
    acc += seg[i]
  }
  return best
}

// Point at fraction t (0..1, by arc length) along a polyline.
export function polyPointAt(pts: Pt[], t: number): Pt {
  if (!pts.length) return { x: 0, y: 0 }
  if (pts.length === 1) return pts[0]
  const segs: number[] = []
  let total = 0
  for (let i = 0; i < pts.length - 1; i++) {
    const d = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y)
    segs.push(d)
    total += d
  }
  let target = Math.max(0, Math.min(1, t)) * total
  for (let i = 0; i < segs.length; i++) {
    if (target <= segs[i] || i === segs.length - 1) {
      const f = segs[i] ? target / segs[i] : 0
      return { x: pts[i].x + (pts[i + 1].x - pts[i].x) * f, y: pts[i].y + (pts[i + 1].y - pts[i].y) * f }
    }
    target -= segs[i]
  }
  return pts[pts.length - 1]
}

// A connector arrow's geometry as the user's example wants it: the SKELETON spans
// the two shapes' CENTRES (so endpoints/handles + a faint guide sit at the centres),
// but the VISIBLE arrow is only the part of that curve BETWEEN the two perimeters —
// invisible inside either shape, and shape-aware (clips to an ellipse, not its box).
export interface ConnectorClip {
  cA: Pt // start centre (skeleton end + bindable endpoint)
  cB: Pt // end centre
  a: Pt // start perimeter crossing (visible start)
  b: Pt // end perimeter crossing (visible end, where the head sits)
  visible: Pt[] // polyline of the visible arc (a … b)
  apex: Pt // midpoint of the full centre-to-centre skeleton (bend handle)
  kind: ArrowKind
}
export function clipConnector(startRec: any, endRec: any, rec?: any): ConnectorClip | null {
  const sb = recordBBox(startRec)
  const eb = recordBBox(endRec)
  if (!sb || !eb) return null
  const cA = { x: sb.x + sb.w / 2, y: sb.y + sb.h / 2 }
  const cB = { x: eb.x + eb.w / 2, y: eb.y + eb.h / 2 }
  return clipBetween(cA, cB, startRec, endRec, rec)
}

// AGNOSTIC entry: resolve an arrow's two anchors + clip shapes from the record —
// whether it's a code-made connector ('arrow', start/end shape ids) OR a USER-drawn
// 'line' bound to shapes (props.bindStart/bindEnd). A bound, centre-anchored end →
// the shape CENTRE + perimeter clip; a precise-anchored end → that exact point; a
// free end → the drawn endpoint. So both kinds get the same centre-to-centre clip.
export function clipArrow(rec: any): ConnectorClip | null {
  if (rec?.typeName === 'arrow') {
    const s = store.peek(rec.start)
    const e = store.peek(rec.end)
    if (!s || !e) return null
    return clipConnector(s, e, rec)
  }
  const free = lineAbs(rec)
  if (!free) return null
  const resolve = (bindId: any, anchor: any, freePt: Pt): { c: Pt; clip: any } => {
    const sh = bindId ? store.peek(bindId) : null
    const bb = sh ? recordBBox(sh) : null
    if (sh && bb) {
      // The aim point is the centre OR the exact anchor (anywhere on the panel) —
      // but EITHER way the visible arrow is clipped to the perimeter, so it never
      // draws inside the shape and the head sits on the boundary.
      const c = anchor ? anchorWorld(sh, bb, anchor) : { x: bb.x + bb.w / 2, y: bb.y + bb.h / 2 }
      return { c, clip: sh }
    }
    return { c: freePt, clip: null }
  }
  const A = resolve(rec.props?.bindStart, rec.props?.bindStartAnchor, free.a)
  const B = resolve(rec.props?.bindEnd, rec.props?.bindEndAnchor, free.b)
  return clipBetween(A.c, B.c, A.clip, B.clip, rec)
}

// Core: sample the anchor-to-anchor skeleton and keep only the part OUTSIDE the
// (optional) clip shapes — the visible arc. Samples the FULL elbow ROUTE (not just
// linePointAt's mid leg, which clipped to nothing and made elbow arrows vanish).
function clipBetween(cA: Pt, cB: Pt, aClip: any, bClip: any, rec?: any): ConnectorClip {
  const bend = rec?.props?.bend || 0
  const kind = lineKind(rec)
  const split = typeof rec?.props?.elbowSplit === 'number' ? rec.props.elbowSplit : 0.5
  const N = 64
  const corners = kind === 'elbow' ? elbowRoutePts(cA, cB, split, rec?.props?.elbowCoords, rec?.props?.elbowAxis) : null
  const pts: Pt[] = []
  for (let i = 0; i <= N; i++) pts.push(corners ? polyPointAt(corners, i / N) : linePointAt(cA, cB, bend, kind, i / N, split))
  const apex = corners ? polyPointAt(corners, 0.5) : linePointAt(cA, cB, bend, kind, 0.5, split)
  const bbA = aClip ? recordBBox(aClip) : null
  const bbB = bClip ? recordBBox(bClip) : null
  let iA = 0
  if (aClip && bbA) while (iA <= N && pointInRecord(aClip, bbA, pts[iA])) iA++
  let iB = N
  if (bClip && bbB) while (iB >= 0 && pointInRecord(bClip, bbB, pts[iB])) iB--
  if (iA > iB) return { cA, cB, a: apex, b: apex, visible: [], apex, kind }
  const refine = (inside: Pt, outside: Pt, r: any, bb: Box): Pt => {
    let lo = inside
    let hi = outside
    for (let k = 0; k < 12; k++) {
      const m = { x: (lo.x + hi.x) / 2, y: (lo.y + hi.y) / 2 }
      if (pointInRecord(r, bb, m)) lo = m
      else hi = m
    }
    return hi
  }
  const a = aClip && bbA && iA > 0 ? refine(pts[iA - 1], pts[iA], aClip, bbA) : pts[iA]
  const b = bClip && bbB && iB < N ? refine(pts[iB + 1], pts[iB], bClip, bbB) : pts[iB]
  // An elbow keeps its actual CORNERS (so they can be rendered as tight rounded
  // corners) — a sparse [a, c1, c2, b], dropping any corner inside a clipped shape.
  // Curves stay a dense sampled polyline.
  const visible = corners
    ? [a, ...corners.slice(1, -1).filter((c) => !(aClip && bbA && pointInRecord(aClip, bbA, c)) && !(bClip && bbB && pointInRecord(bClip, bbB, c))), b]
    : [a, ...pts.slice(iA + 1, iB), b]
  return { cA, cB, a, b, visible, apex, kind }
}

// Quadratic control point for a given bend (apex offset).
export function lineControl(a: Pt, b: Pt, bend: number): Pt {
  const mx = (a.x + b.x) / 2
  const my = (a.y + b.y) / 2
  if (!bend) return { x: mx, y: my }
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  return { x: mx + (-dy / len) * 2 * bend, y: my + (dx / len) * 2 * bend }
}

// Two cubic control points for the 'curved' kind. bend (if any) sets the bow;
// with no bend a fresh curved arrow still bows gently so it reads as a curve.
function cubicControls(a: Pt, b: Pt, bend: number): { c1: Pt; c2: Pt } {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  const off = bend !== 0 ? bend * 1.4 : len * 0.16
  const px = -dy / len
  const py = dx / len
  return {
    c1: { x: a.x + dx / 3 + px * off, y: a.y + dy / 3 + py * off },
    c2: { x: a.x + (dx * 2) / 3 + px * off, y: a.y + (dy * 2) / 3 + py * off },
  }
}

// A right-angle (elbow) route reads naturally only if it turns along the SHORT
// axis: shapes side-by-side get H-V-H, stacked shapes get V-H-V. Choosing by which
// gap is larger fixes the ugly tiny jogs (+ sideways arrowhead) a fixed H-V-H made
// for a vertical arrangement.
export function elbowVertical(a: Pt, b: Pt): boolean {
  return Math.abs(b.y - a.y) > Math.abs(b.x - a.x)
}
// MULTI-BEND rectilinear route for an elbow arrow. Modelled as a first-segment axis
// + a list of interior TURN positions (`coords`, alternating perpendicular axes);
// the route auto-completes to b. Default (no coords) = a single auto-bend from
// `split` (= the classic elbow). More coords ⇒ more bends.
export function elbowDefaultAxis(a: Pt, b: Pt, axis?: 'h' | 'v'): 'h' | 'v' {
  return axis === 'h' || axis === 'v' ? axis : elbowVertical(a, b) ? 'v' : 'h'
}
export function elbowDefaultCoords(a: Pt, b: Pt, split: number, ax: 'h' | 'v'): number[] {
  return [ax === 'v' ? a.y + (b.y - a.y) * split : a.x + (b.x - a.x) * split]
}
export function elbowRoutePts(a: Pt, b: Pt, split: number, coords?: number[], axis?: 'h' | 'v'): Pt[] {
  const ax0 = elbowDefaultAxis(a, b, axis)
  const cs = coords && coords.length ? coords : elbowDefaultCoords(a, b, split, ax0)
  const pts: Pt[] = [{ x: a.x, y: a.y }]
  let cur = { x: a.x, y: a.y }
  let ax = ax0
  for (const coord of cs) {
    cur = ax === 'v' ? { x: cur.x, y: coord } : { x: coord, y: cur.y }
    pts.push({ x: cur.x, y: cur.y })
    ax = ax === 'v' ? 'h' : 'v'
  }
  // auto-complete to b (a final jog only if neither coordinate already lines up)
  if (Math.abs(cur.x - b.x) > 0.5 && Math.abs(cur.y - b.y) > 0.5) {
    pts.push(ax === 'h' ? { x: b.x, y: cur.y } : { x: cur.x, y: b.y })
  }
  pts.push({ x: b.x, y: b.y })
  // drop any zero-length steps
  return pts.filter((p, i) => i === 0 || Math.abs(p.x - pts[i - 1].x) > 0.01 || Math.abs(p.y - pts[i - 1].y) > 0.01)
}
// Route segments with which `coords` index each draggable one writes to (-1 = the
// fixed end legs touching the shapes). orient = 'h' (horizontal) | 'v' (vertical).
// Built from an UNFILTERED node list (one node per coord, in order) so segment i in
// 1..coords.length always maps to coord i-1 — even when a jog is colinear/degenerate.
export function elbowSegments(a: Pt, b: Pt, split: number, coords?: number[], axis?: 'h' | 'v') {
  const ax0 = elbowDefaultAxis(a, b, axis)
  const cs = coords && coords.length ? coords : elbowDefaultCoords(a, b, split, ax0)
  const pts: Pt[] = [{ x: a.x, y: a.y }]
  let cur = { x: a.x, y: a.y }
  let ax = ax0
  for (const coord of cs) {
    cur = ax === 'v' ? { x: cur.x, y: coord } : { x: coord, y: cur.y }
    pts.push({ x: cur.x, y: cur.y })
    ax = ax === 'v' ? 'h' : 'v'
  }
  if (Math.abs(cur.x - b.x) > 0.5 && Math.abs(cur.y - b.y) > 0.5) {
    cur = ax === 'h' ? { x: b.x, y: cur.y } : { x: cur.x, y: b.y }
    pts.push({ x: cur.x, y: cur.y })
  }
  pts.push({ x: b.x, y: b.y })
  const segs: { p0: Pt; p1: Pt; mid: Pt; orient: 'h' | 'v'; coordIndex: number; len: number }[] = []
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i]
    const p1 = pts[i + 1]
    // orient from geometry (robust to the auto-jog breaking strict alternation)
    const orient: 'h' | 'v' = Math.abs(p1.x - p0.x) >= Math.abs(p1.y - p0.y) ? 'h' : 'v'
    segs.push({
      p0,
      p1,
      mid: { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2 },
      orient,
      coordIndex: i >= 1 && i <= cs.length ? i - 1 : -1,
      len: Math.hypot(p1.x - p0.x, p1.y - p0.y),
    })
  }
  return { segs, coords: cs.slice(), axis: ax0 }
}
// Remove tiny jogs: a draggable segment shorter than `min` is dropped together with
// its partner turn (coords come in out-and-back pairs), collapsing the route to fewer
// bends. Returns the cleaned coords (or undefined to fall back to the single default
// bend when nothing meaningful remains).
export function collapseElbowCoords(a: Pt, b: Pt, split: number, coords: number[], axis: 'h' | 'v', min: number): number[] | undefined {
  let cs = coords.slice()
  // coords length stays odd: 1 primary bend + jog pairs. A short DRAGGABLE segment
  // belongs to a jog pair starting at an odd index; drop that whole pair so the
  // remaining coords keep their alternating-axis parity. The primary bend (index 0)
  // is never collapsed.
  for (let pass = 0; pass < coords.length; pass++) {
    const { segs } = elbowSegments(a, b, split, cs, axis)
    const bad = segs.find((s) => s.coordIndex >= 1 && s.len < min)
    if (!bad) break
    const start = bad.coordIndex % 2 === 1 ? bad.coordIndex : bad.coordIndex - 1
    cs.splice(start, 2)
    if (!cs.length) break
  }
  return cs.length ? cs : undefined
}

// The 4 corner points of an elbow route a→b. `split` (0..1) is the turn position
// along the dominant axis.
export function elbowPts(a: Pt, b: Pt, split = 0.5): [Pt, Pt, Pt, Pt] {
  if (elbowVertical(a, b)) {
    const my = a.y + (b.y - a.y) * split
    return [a, { x: a.x, y: my }, { x: b.x, y: my }, b]
  }
  const mx = a.x + (b.x - a.x) * split
  return [a, { x: mx, y: a.y }, { x: mx, y: b.y }, b]
}

// SVG path data for a line of the given kind. `split` (0..1) is the elbow's turn
// position (where the mid leg sits between the ends).
export function linePathD(a: Pt, b: Pt, bend: number, kind: ArrowKind = 'straight', split = 0.5): string {
  if (kind === 'elbow') {
    const p = elbowPts(a, b, split)
    return `M ${p[0].x},${p[0].y} L ${p[1].x},${p[1].y} L ${p[2].x},${p[2].y} L ${p[3].x},${p[3].y}`
  }
  if (kind === 'curved') {
    const { c1, c2 } = cubicControls(a, b, bend)
    return `M ${a.x},${a.y} C ${c1.x},${c1.y} ${c2.x},${c2.y} ${b.x},${b.y}`
  }
  if (bend) {
    const c = lineControl(a, b, bend)
    return `M ${a.x},${a.y} Q ${c.x},${c.y} ${b.x},${b.y}`
  }
  return `M ${a.x},${a.y} L ${b.x},${b.y}`
}

// Point on the line at parameter t (0..1), used for label/bend-handle placement.
export function linePointAt(a: Pt, b: Pt, bend: number, kind: ArrowKind, t: number, split = 0.5): Pt {
  if (kind === 'elbow') {
    // place the label along the MIDDLE leg (the one the text naturally sits on)
    const p = elbowPts(a, b, split)
    return { x: p[1].x + (p[2].x - p[1].x) * t, y: p[1].y + (p[2].y - p[1].y) * t }
  }
  if (kind === 'curved') {
    const { c1, c2 } = cubicControls(a, b, bend)
    const u = 1 - t
    return {
      x: u * u * u * a.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * b.x,
      y: u * u * u * a.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * b.y,
    }
  }
  if (bend) {
    const c = lineControl(a, b, bend)
    const u = 1 - t
    return { x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y }
  }
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
}

// Polyline samples of a user line's VISIBLE (clipped) body — for hit-testing and
// marquee so you grab the actual drawn arc (perimeter-to-perimeter when bound), not
// the part hidden inside a bound shape or the straight chord.
export function lineSamples(rec: any): Pt[] {
  const clip = clipArrow(rec)
  return clip ? clip.visible : []
}

// Polyline samples of a CONNECTOR arrow's VISIBLE (clipped, perimeter-to-perimeter)
// path, so hit-testing grabs the actual drawn arc — not the part hidden inside the
// shapes or the straight centre chord.
export function connectorSamples(rec: any, startRec: any, endRec: any): Pt[] {
  const clip = clipConnector(startRec, endRec, rec)
  return clip ? clip.visible : []
}

// The path parameter (0..1) of the point on the RENDERED path nearest to `pt` —
// for natural label dragging: the caption follows the actual curve under the
// cursor, not a projection onto the straight end-to-end chord. Samples the path
// and projects onto each segment, so it tracks curved/elbow/bent arrows.
export function nearestParamOnPath(a: Pt, b: Pt, bend: number, kind: ArrowKind, split: number, pt: Pt, N = 40): number {
  let best = 0
  let bestD = Infinity
  let prev = linePointAt(a, b, bend, kind, 0, split)
  for (let i = 1; i <= N; i++) {
    const cur = linePointAt(a, b, bend, kind, i / N, split)
    const dx = cur.x - prev.x
    const dy = cur.y - prev.y
    const len2 = dx * dx + dy * dy || 1
    let lt = ((pt.x - prev.x) * dx + (pt.y - prev.y) * dy) / len2
    lt = lt < 0 ? 0 : lt > 1 ? 1 : lt
    const qx = prev.x + dx * lt
    const qy = prev.y + dy * lt
    const d = (pt.x - qx) ** 2 + (pt.y - qy) ** 2
    if (d < bestD) {
      bestD = d
      best = (i - 1 + lt) / N
    }
    prev = cur
  }
  return best < 0 ? 0 : best > 1 ? 1 : best
}

// The tangent direction at an endpoint (for orienting arrowheads), end='a'|'b'.
export function lineTangent(a: Pt, b: Pt, bend: number, kind: ArrowKind, end: 'a' | 'b'): Pt {
  const near = end === 'a' ? linePointAt(a, b, bend, kind, 0.04) : linePointAt(a, b, bend, kind, 0.96)
  const tip = end === 'a' ? a : b
  const dx = tip.x - near.x
  const dy = tip.y - near.y
  const len = Math.hypot(dx, dy) || 1
  return { x: dx / len, y: dy / len }
}

// Given a dragged apex point, the signed bend (perpendicular distance from mid).
export function bendFromApex(a: Pt, b: Pt, apex: Pt): number {
  const mx = (a.x + b.x) / 2
  const my = (a.y + b.y) / 2
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  // perpendicular unit (matches lineControl)
  return (apex.x - mx) * (-dy / len) + (apex.y - my) * (dx / len)
}
