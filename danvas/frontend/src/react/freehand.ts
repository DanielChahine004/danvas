// Shared perfect-freehand helpers, used by both the renderer (DrawingLayer) and
// the selection overlay (so a freehand stroke's selection hugs the ink outline
// instead of a bounding rectangle).
import { getStroke } from 'perfect-freehand'

const r1 = (n: number) => Math.round(n * 10) / 10

// perfect-freehand outline points -> SVG path (canonical builder: seed with
// `M x0 y0 Q` and push 4 numbers per point so the Q groups stay complete).
export function strokePath(pts: number[][]): string {
  if (!pts.length) return ''
  const d = pts.reduce(
    (acc, [x0, y0], i, arr) => {
      const [x1, y1] = arr[(i + 1) % arr.length]
      acc.push(r1(x0), r1(y0), r1((x0 + x1) / 2), r1((y0 + y1) / 2))
      return acc
    },
    ['M', r1(pts[0][0]), r1(pts[0][1]), 'Q'] as (string | number)[],
  )
  d.push('Z')
  return d.join(' ')
}

// Full pipeline: raw input points -> filled stroke outline path. `last: true`
// finalises the end of the stroke so perfect-freehand tapers it instead of
// leaving a rounded "live" cap that reads as a blob/circle at the end.
export function freehandStrokePath(points: number[][], size: number): string {
  if (!points.length) return ''
  const outline = getStroke(points, {
    size,
    thinning: 0.6,
    smoothing: 0.5,
    streamline: 0.5,
    last: true,
  }) as number[][]
  return strokePath(outline)
}
