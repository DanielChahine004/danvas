// The drawing layer: an SVG rendered inside the camera transform, BELOW the
// panels (the v1 z-band: drawings under panels). It renders Python-managed canvas
// shapes (canvas.geo/text/line/frame) and connector arrows (canvas.connect).
// Arrows reroute reactively as their bound endpoints move. Display-only in v1
// (pointer-transparent) — user-drawn ink + drawing tools are a later milestone.
import { store } from '../engine/store'
import { useValue } from './EngineContext'
import { freehandStrokePath } from './freehand'
import { colorOf, DRAW_FONT, labelEllipse, alignCss, type Align } from './palette'
import { sanitizeRich, richToPlain } from './richtext'
import { lineKind, clipArrow, recordNormal, polyPointAt, type ConnectorClip } from '../engine/lineGeo'
import { recordBBox } from '../engine/hittest'
import { erasingIds } from '../engine/interaction'
import type { ArrowRecord, DrawingRecord, CanvasRecord } from '../engine/types'

// stroke thickness / font size from the style size letter.
const STROKE: Record<string, number> = { s: 2, m: 3.5, l: 6, xl: 9 }
const FONT: Record<string, number> = { s: 14, m: 20, l: 28, xl: 40 }
const strokeW = (size?: string) => STROKE[size || 'm'] || 3.5
const fontSz = (size?: string) => FONT[size || 'm'] || 20
const LABEL_W = 150 // caption wrap width (page units)

// The SVG <mask> that GAPS a line/arrow stroke behind its caption: a white field
// over the whole path minus a black ellipse around the text, so the stroke is cut
// out cleanly there (a vector gap — no box, no jagged text-shadow). Paired with the
// caption itself; `show` hides the caption while editing but KEEPS the gap so the
// inline editor sits in a clean break. `a`,`b`,`bend` bound the white field to the path.
function MaskedCaption({ id, a, b, bend, lp, ell, fs, color, opacity, text, align, show }: {
  id: string
  a: { x: number; y: number }
  b: { x: number; y: number }
  bend: number
  lp: { x: number; y: number }
  ell: { rx: number; ry: number }
  fs: number
  color: string
  opacity?: number
  text: string
  align: Align
  show: boolean
}) {
  const m = Math.abs(bend) + 400
  const x0 = Math.min(a.x, b.x) - m
  const y0 = Math.min(a.y, b.y) - m
  const w = Math.abs(b.x - a.x) + 2 * m
  const h = Math.abs(b.y - a.y) + 2 * m
  return (
    <>
      <defs>
        {/* explicit user-space region covering the path — WITHOUT x/y/w/h the mask
            region defaults to viewport percentages, which don't cover the path once
            the camera is panned (page coords), hiding the whole stroke. */}
        <mask id={id} maskUnits="userSpaceOnUse" maskContentUnits="userSpaceOnUse" x={x0} y={y0} width={w} height={h}>
          <rect x={x0} y={y0} width={w} height={h} fill="#fff" />
          <ellipse cx={lp.x} cy={lp.y} rx={ell.rx} ry={ell.ry} fill="#000" />
        </mask>
      </defs>
      {show && (
        <foreignObject x={lp.x - LABEL_W / 2} y={lp.y - fs * 1.6} width={LABEL_W} height={fs * 3.4} style={{ overflow: 'visible' }}>
          <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ font: `${fs}px/1.2 ${DRAW_FONT}`, color, textAlign: align, whiteSpace: 'pre-wrap', wordBreak: 'break-word', opacity, pointerEvents: 'none' }} dangerouslySetInnerHTML={{ __html: sanitizeRich(text) }} />
          </div>
        </foreignObject>
      )}
    </>
  )
}

function box(rec: CanvasRecord): { x: number; y: number; w: number; h: number } {
  const w = (rec as any).props?.w || 0
  const h = (rec as any).props?.h || 0
  return { x: (rec as any).x ?? 0, y: (rec as any).y ?? 0, w, h }
}

// Point on a box's border in the direction of `towards` (for arrow endpoints).
function edgePoint(b: { x: number; y: number; w: number; h: number }, towards: { x: number; y: number }) {
  const cx = b.x + b.w / 2
  const cy = b.y + b.h / 2
  const dx = towards.x - cx
  const dy = towards.y - cy
  if (dx === 0 && dy === 0) return { x: cx, y: cy }
  const hw = b.w / 2 || 1
  const hh = b.h / 2 || 1
  const scale = 1 / Math.max(Math.abs(dx) / hw, Math.abs(dy) / hh)
  return { x: cx + dx * scale, y: cy + dy * scale }
}

// Drawings render in TWO passes around the panels so a shape can sit UNDER a panel
// (send-to-back) or over it (default). The split point is the first panel in the
// global z-order (idList): drawings before it go in the `below` layer, the rest
// above. So a freshly-drawn shape lands on top; "send to back" drops it under the
// panels (which stay clickable). `below` selects which bucket this instance draws.
export function DrawingLayer({ below = false }: { below?: boolean }) {
  const ids = useValue(
    'draw-ids:' + below,
    () => {
      const all = store.getIds()
      let cut = all.findIndex((id) => store.peek(id)?.typeName === 'panel')
      if (cut < 0) cut = all.length
      return all.filter((id, i) => {
        const t = store.peek(id)?.typeName
        if (t !== 'drawing' && t !== 'arrow') return false
        return below ? i < cut : i >= cut
      })
    },
    [below],
  )
  if (!ids.length) return null
  return (
    <svg data-pc-drawings={below ? 'below' : ''} style={{ position: 'absolute', left: 0, top: 0, width: 1, height: 1, overflow: 'visible', pointerEvents: 'none', userSelect: 'none' }}>
      {ids.map((id) => (
        <Drawn key={id} id={id} />
      ))}
    </svg>
  )
}

// The shared arrowhead marker. Rendered ONCE (PanelLayer), not per DrawingLayer:
// there are two layers (below/above the panels) and each used to emit its own
// <marker id="pc-arrowhead">, so two identical-id elements coexisted in the
// document. `url(#pc-arrowhead)` resolves document-wide, so a single hidden defs
// svg serves the paths in both layers; `fill="context-stroke"` still picks up
// each referencing path's own stroke colour.
export function ArrowMarkerDefs() {
  return (
    <svg width={0} height={0} aria-hidden="true" style={{ position: 'absolute' }}>
      <defs>
        <marker id="pc-arrowhead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="context-stroke" />
        </marker>
      </defs>
    </svg>
  )
}

function Drawn({ id }: { id: string }) {
  const rec = useValue('draw:' + id, () => store.get(id), [id])
  // dimmed while the eraser has it marked (deletes on release)
  const erasing = useValue('erasing:' + id, () => erasingIds().has(id), [id])
  if (!rec) return null
  let el: any = null
  if (rec.typeName === 'drawing') el = <DrawingShape rec={rec} />
  else if (rec.typeName === 'arrow') el = <ArrowShape rec={rec} />
  else return null
  return erasing ? <g opacity={0.4}>{el}</g> : el
}

function DrawingShape({ rec }: { rec: DrawingRecord }) {
  // hide this shape's text while it's being edited inline (TextEditor shows it)
  const editing = useValue('edit:' + rec.id, () => store.instance().editingId === rec.id, [rec.id])
  const { x, y, rotation, opacity, props } = rec
  const stroke = colorOf(props.color)
  const fill = props.fill === 'solid' || props.fill === 'pattern' || props.fill === 'semi' ? stroke : 'none'
  const fillOpacity = props.fill === 'semi' ? 0.22 : props.fill === 'pattern' ? 0.5 : 1
  const sw = strokeW(props.size)
  const w = props.w || 0
  const h = props.h || 0
  const rot = rotation ? `rotate(${(rotation * 180) / Math.PI} ${x + w / 2} ${y + h / 2})` : undefined
  // dashed / dotted edges (relative to stroke width); dotted needs round caps.
  const dashArray = props.dash === 'dashed' ? `${sw * 2.4} ${sw * 1.8}` : props.dash === 'dotted' ? `${sw * 0.1} ${sw * 1.9}` : undefined
  const dotCap = props.dash === 'dotted' ? 'round' : undefined

  switch (rec.shapeType) {
    case 'geo': {
      // Filled shapes absorb clicks over their whole area; hollow ones only on
      // the outline (so a big annotation outline doesn't trap panels inside it).
      const common = {
        fill,
        fillOpacity,
        stroke,
        strokeWidth: sw,
        opacity,
        transform: rot,
        strokeDasharray: dashArray,
        strokeLinecap: dotCap,
        pointerEvents: fill !== 'none' ? 'all' : 'visibleStroke',
      }
      const shapeEl =
        props.geo === 'ellipse' ? (
          <ellipse cx={x + w / 2} cy={y + h / 2} rx={w / 2} ry={h / 2} {...(common as any)} />
        ) : (
          <rect x={x} y={y} width={w} height={h} rx={4} {...(common as any)} />
        )
      const label =
        props.text && !editing ? (
          <foreignObject x={x} y={y} width={w} height={h} style={{ overflow: 'visible' }}>
            <div
              style={{
                width: '100%',
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                textAlign: alignCss(props.align, 'center'),
                font: `${fontSz(props.size)}px/1.25 ${DRAW_FONT}`,
                color: 'var(--pc-text, #222)',
                padding: 4,
                boxSizing: 'border-box',
                wordBreak: 'break-word',
                whiteSpace: 'pre-wrap',
                pointerEvents: 'none',
              }}
              dangerouslySetInnerHTML={{ __html: sanitizeRich(props.text) }}
            />
          </foreignObject>
        ) : null
      return label ? (
        <g opacity={opacity}>
          {shapeEl}
          {label}
        </g>
      ) : (
        shapeEl
      )
    }
    case 'frame':
      return (
        <g opacity={opacity}>
          <text x={x} y={y - 5} fontSize={12} fontFamily="system-ui, sans-serif" fill="var(--pc-muted, #888)">
            {props.name || props.label || ''}
          </text>
          <rect x={x} y={y} width={w} height={h} rx={4} fill="none" stroke="var(--pc-border, #999)" strokeWidth={1.5} />
        </g>
      )
    case 'note': {
      // sticky note: a filled coloured square with text inside.
      const nw = w || 160
      const nh = h || 160
      const bg = props.color ? stroke : '#f1ac4b'
      return (
        <g opacity={opacity} transform={rot}>
          <rect x={x} y={y} width={nw} height={nh} rx={6} fill={bg} pointerEvents="all" />
          <foreignObject x={x + 8} y={y + 6} width={nw - 16} height={nh - 12}>
            <div
              style={{
                width: '100%',
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                textAlign: alignCss(props.align, 'center'),
                font: `${fontSz(props.size)}px/1.25 ${DRAW_FONT}`,
                color: '#1d1d1d',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                pointerEvents: 'none',
              }}
              dangerouslySetInnerHTML={{ __html: editing ? '' : sanitizeRich(props.text || '') }}
            />
          </foreignObject>
        </g>
      )
    }
    case 'text': {
      // HTML div in a foreignObject so the text wraps within the box (resizable,
      // whiteboard-style) and aligns 1:1 with the inline editor's textarea. A numeric
      // props.fontSize (set by a vertical resize) overrides the s/m/l preset.
      const tfs = props.fontSize ?? fontSz(props.size)
      const tw = w || 160
      const th = Math.max(h || 0, tfs * 1.3)
      // auto-width (hug the text) by default; once a side handle sets a wrap width,
      // props.autoSize=false and the text wraps within the box (font unchanged).
      const autoW = props.autoSize !== false
      return (
        <foreignObject x={x} y={y} width={tw} height={th} transform={rot} style={{ overflow: 'visible' }} pointerEvents="all">
          <div
            style={{
              font: `${tfs}px/1.25 ${DRAW_FONT}`,
              color: props.color ? stroke : 'var(--pc-text, #222)',
              whiteSpace: autoW ? 'pre' : 'pre-wrap', // pre = auto-width; pre-wrap = wrap at box width
              width: autoW ? undefined : '100%',
              wordBreak: 'break-word',
              textAlign: alignCss(props.align, 'left'),
              opacity,
              pointerEvents: 'none',
            }}
            dangerouslySetInnerHTML={{ __html: editing ? '' : sanitizeRich(props.text || '') }}
          />
        </foreignObject>
      )
    }
    case 'image': {
      const iw = props.w || 0
      const ih = props.h || 0
      if (!props.src) return null
      return <image href={props.src} x={x} y={y} width={iw} height={ih} opacity={opacity} transform={rot} preserveAspectRatio="none" pointerEvents="all" style={{ imageRendering: 'auto' }} />
    }
    case 'draw':
    case 'highlight': {
      const raw = Array.isArray(props.points) ? props.points : props.points ? Object.values(props.points) : []
      const pts = raw.map((p: any) => [x + p.x, y + p.y] as [number, number])
      if (!pts.length) return null
      // Dashed/dotted ink renders as a stroked line through the points (a filled
      // perfect-freehand blob can't show dashes); solid keeps the smooth fill.
      if (props.dash === 'dashed' || props.dash === 'dotted') {
        return (
          <polyline
            points={pts.map((p) => `${p[0]},${p[1]}`).join(' ')}
            fill="none"
            stroke={stroke}
            strokeWidth={sw}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={dashArray}
            opacity={rec.shapeType === 'highlight' ? 0.4 : opacity}
            pointerEvents="visibleStroke"
          />
        )
      }
      return <path d={freehandStrokePath(pts, sw * 2.2)} fill={stroke} opacity={rec.shapeType === 'highlight' ? 0.4 : opacity} pointerEvents="all" />
    }
    case 'line':
      return <LineShape rec={rec} editing={editing} stroke={stroke} sw={sw} dashArray={dashArray} opacity={opacity} />
    default:
      return null
  }
}

// User-drawn lines/arrows. Uses the SAME centre-to-centre clip as code-made
// connectors (clipArrow) so a bound user arrow leaves the real shape perimeter
// (ellipse, not box) with its endpoints at the centres — agnostic to how it was
// made. A free end just uses the drawn point. Reactive to bound shapes moving.
function LineShape({ rec, editing, stroke, sw, dashArray, opacity }: { rec: DrawingRecord; editing: boolean; stroke: string; sw: number; dashArray?: string; opacity: number }) {
  const props = rec.props as any
  const bend = props.bend || 0
  const bs = props.bindStart
  const be = props.bindEnd
  useValue('lb-s:' + rec.id, () => (bs ? store.get(bs) : undefined), [bs])
  useValue('lb-e:' + rec.id, () => (be ? store.get(be) : undefined), [be])
  const clip = clipArrow(rec)
  if (!clip || clip.visible.length < 2) return null
  const kind = clip.kind
  // Arrowheads: `arrowEnd` / `arrowStart` (legacy `arrow` = end only).
  const arrowEnd = !!(props.arrowEnd ?? props.arrow)
  const arrowStart = !!props.arrowStart
  const vis = perpStub(clip, bs ? store.peek(bs) : null, be ? store.peek(be) : null, arrowStart, arrowEnd, sw)
  const dStr = elbowDStr(vis, kind)
  // A caption gaps the stroke behind it (clean vector mask, no halo).
  const hasText = !!props.text
  const t = typeof props.labelPosition === 'number' ? Math.max(0, Math.min(1, props.labelPosition)) : 0.5
  const lp = hasText ? polyPointAt(clip.visible, t) : null
  const fs = fontSz(props.size)
  const ell = hasText ? labelEllipse(richToPlain(props.text), fs, LABEL_W) : null
  const maskId = hasText ? `pclbl-${rec.id}` : undefined
  const line = (
    <path
      d={dStr}
      fill="none"
      stroke={stroke}
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeDasharray={dashArray}
      opacity={opacity}
      pointerEvents="visibleStroke"
      mask={maskId ? `url(#${maskId})` : undefined}
      markerStart={arrowStart ? 'url(#pc-arrowhead)' : undefined}
      markerEnd={arrowEnd ? 'url(#pc-arrowhead)' : undefined}
    />
  )
  if (!hasText) return line
  return (
    <g>
      {line}
      <MaskedCaption id={maskId!} a={clip.a} b={clip.b} bend={bend} lp={lp!} ell={ell!} fs={fs} color={stroke} opacity={opacity} text={props.text} align={alignCss(props.align, 'center')} show={!editing} />
    </g>
  )
}

// True if an arrowhead prop means "show a head". Connector arrows carry the
// string props (arrowheadStart/End: 'none'|'arrow'|…) and default to an
// END head; user-set toggles (arrowStart/arrowEnd booleans) override.
const showHead = (str: any, bool: any, dflt: boolean) => (str !== undefined ? str !== 'none' : bool !== undefined ? !!bool : dflt)

// SVG path through `pts` with each interior corner rounded by radius `r` (clamped to
// half the shorter adjacent leg) — tight rounded corners for right-angle arrows.
function roundedPath(pts: { x: number; y: number }[], r: number): string {
  if (pts.length < 3) return 'M ' + pts.map((p) => `${p.x},${p.y}`).join(' L ')
  let d = `M ${pts[0].x},${pts[0].y}`
  for (let i = 1; i < pts.length - 1; i++) {
    const p0 = pts[i - 1]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const d1 = Math.hypot(p1.x - p0.x, p1.y - p0.y) || 1
    const d2 = Math.hypot(p2.x - p1.x, p2.y - p1.y) || 1
    const rr = Math.min(r, d1 / 2, d2 / 2)
    const ax = p1.x + ((p0.x - p1.x) / d1) * rr
    const ay = p1.y + ((p0.y - p1.y) / d1) * rr
    const bx = p1.x + ((p2.x - p1.x) / d2) * rr
    const by = p1.y + ((p2.y - p1.y) / d2) * rr
    d += ` L ${ax},${ay} Q ${p1.x},${p1.y} ${bx},${by}`
  }
  d += ` L ${pts[pts.length - 1].x},${pts[pts.length - 1].y}`
  return d
}
const elbowDStr = (vis: { x: number; y: number }[], kind: string) =>
  kind === 'elbow' ? roundedPath(vis, 10) : 'M ' + vis.map((p) => `${p.x},${p.y}`).join(' L ')

// For a right-angle (elbow) arrow, bend the final stub along the connected shape's
// surface normal so the head meets it PERPENDICULARLY (round shapes too). Agnostic
// to arrow type — pass the shape each clipped end touches (null = free/unbound end).
function perpStub(clip: ConnectorClip, startShape: any, endShape: any, headStart: boolean, headEnd: boolean, sw: number): { x: number; y: number }[] {
  let vis = clip.visible
  if (clip.kind !== 'elbow') return vis
  const stub = sw * 3 + 8
  if (headEnd && endShape && vis.length >= 2) {
    const bb = recordBBox(endShape)
    if (bb) {
      const n = recordNormal(endShape, bb, clip.b)
      vis = [...vis.slice(0, -1), { x: clip.b.x + n.x * stub, y: clip.b.y + n.y * stub }, clip.b]
    }
  }
  if (headStart && startShape && vis.length >= 2) {
    const bb = recordBBox(startShape)
    if (bb) {
      const n = recordNormal(startShape, bb, clip.a)
      vis = [clip.a, { x: clip.a.x + n.x * stub, y: clip.a.y + n.y * stub }, ...vis.slice(1)]
    }
  }
  return vis
}

function ArrowShape({ rec }: { rec: ArrowRecord }) {
  const start = useValue('arr-s:' + rec.id, () => (rec.start ? store.get(rec.start) : undefined), [rec.start])
  const end = useValue('arr-e:' + rec.id, () => (rec.end ? store.get(rec.end) : undefined), [rec.end])
  const editing = useValue('edit:' + rec.id, () => store.instance().editingId === rec.id, [rec.id])
  if (!start || !end) return null
  // Skeleton spans the two CENTRES; only the part BETWEEN the perimeters is drawn —
  // invisible inside either shape, clipped to the real shape (ellipse, not its box).
  const clip = clipArrow(rec)
  if (!clip || clip.visible.length < 2) return null // shapes overlap → nothing visible
  const props = rec.props as any
  const c = colorOf(props.color)
  const bend = props.bend || 0
  const kind = clip.kind
  const sw = props.size ? strokeW(props.size) : 2.5
  const dashArray = props.dash === 'dashed' ? `${sw * 2.4} ${sw * 1.8}` : props.dash === 'dotted' ? `${sw * 0.1} ${sw * 1.9}` : undefined
  const headStart = showHead(props.arrowheadStart, props.arrowStart, false)
  const headEnd = showHead(props.arrowheadEnd, props.arrowEnd, true)
  const vis = perpStub(clip, start, end, headStart, headEnd, sw)
  const dStr = elbowDStr(vis, kind)
  // caption gaps the stroke behind it (clean vector mask); gap persists while editing
  const hasText = !!props.text
  const t = typeof props.labelPosition === 'number' ? Math.max(0, Math.min(1, props.labelPosition)) : 0.5
  const lp = hasText ? polyPointAt(clip.visible, t) : null // label sits on the visible arc
  const fs = fontSz(props.size)
  const ell = hasText ? labelEllipse(richToPlain(props.text), fs, LABEL_W) : null
  const maskId = hasText ? `pclbl-${rec.id}` : undefined
  const line = (
    <path
      d={dStr}
      fill="none"
      stroke={c}
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeDasharray={dashArray}
      pointerEvents="visibleStroke"
      mask={maskId ? `url(#${maskId})` : undefined}
      markerStart={headStart ? 'url(#pc-arrowhead)' : undefined}
      markerEnd={headEnd ? 'url(#pc-arrowhead)' : undefined}
    />
  )
  if (!hasText) return <g opacity={rec.opacity}>{line}</g>
  return (
    <g opacity={rec.opacity}>
      {line}
      <MaskedCaption id={maskId!} a={clip.a} b={clip.b} bend={bend} lp={lp!} ell={ell!} fs={fs} color={c} text={props.text} align={alignCss(props.align, 'center')} show={!editing} />
    </g>
  )
}
