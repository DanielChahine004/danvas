// Camera control: the page<->screen transform is one signal (store.camera); this
// module owns the higher-level operations over it — lock/zoom-limit-respecting
// writes, the union-of-panels bounds, fit-to-bounds, centre-on-a-point, and the
// once-per-load initial auto-fit. Ports bridge.js's fitCameraToBounds /
// applyCameraFrom / scheduleInitialFit and App.jsx's camera math.
import { store } from './store'
import { editor } from './editor'
import type { Camera, PanelRecord } from './types'

const FIT_PAD = 80 // px of breathing room around panels on first fit

// --- navigation mode (serve(view={navigation:...})) -------------------------
// 'free' = pan/zoom; 'scroll_y'/'scroll_x' lock zoom and constrain panning to one
// axis (a vertical feed / horizontal deck). The wheel handler in input.ts reads
// this to switch behaviour.
export type ScrollMode = 'free' | 'scroll_x' | 'scroll_y'
let scrollMode: ScrollMode = 'free'

export function getScrollMode(): ScrollMode {
  return scrollMode
}

export function setNavigationMode(mode: ScrollMode, zoom = 1): void {
  scrollMode = mode || 'free'
  if (scrollMode === 'free') return
  // Lock to the requested zoom at the origin; we own the camera from here.
  markInitialFitDone()
  store.camera({ x: 0, y: 0, z: zoom })
}

// Clamp z to the configured limits and refuse to move a locked camera (unless
// forced — fit/setView override the lock the same way the old build did).
export function setCamera(cam: Camera, opts?: { force?: boolean }): void {
  const inst = store.instance()
  if (inst.lockedCamera && !opts?.force) return
  const z = Math.max(inst.zoomLimits.min, Math.min(inst.zoomLimits.max, cam.z))
  store.camera({ x: cam.x, y: cam.y, z })
}

// Union of every positioned record's page-space bounds, or null if empty. Skips
// records without geometry (arrows have no x/y/w/h) so an undefined never poisons
// the bounds to NaN — that would NaN the camera zoom and break drags.
export function currentPageBounds(): { x: number; y: number; w: number; h: number } | null {
  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity
  let any = false
  for (const id of store.ids()) {
    const r = store.peek(id) as any
    if (!r || typeof r.x !== 'number' || typeof r.y !== 'number') continue
    const w = r.props?.w || 0
    const h = r.props?.h || 0
    any = true
    minX = Math.min(minX, r.x)
    minY = Math.min(minY, r.y)
    maxX = Math.max(maxX, r.x + w)
    maxY = Math.max(maxY, r.y + h)
  }
  if (!any) return null
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
}

// Centre `bounds` in the viewport, zoomed to fit with a margin (never zooming in
// past 100%). Honours the configured zoom limits; forces past a locked camera.
export function fitCameraToBounds(bounds: { x: number; y: number; w: number; h: number }): void {
  const vsb = editor.getViewportScreenBounds()
  const fitW = Math.max(1, vsb.w - FIT_PAD * 2)
  const fitH = Math.max(1, vsb.h - FIT_PAD * 2)
  const inst = store.instance()
  let z = Math.min(fitW / bounds.w, fitH / bounds.h, 1)
  z = Math.max(inst.zoomLimits.min, Math.min(inst.zoomLimits.max, z))
  const cx = bounds.x + bounds.w / 2
  const cy = bounds.y + bounds.h / 2
  setCamera({ x: vsb.w / (2 * z) - cx, y: vsb.h / (2 * z) - cy, z }, { force: true })
}

// Centre the view on a page point at a zoom, taking each of x/y/zoom from `src`
// and leaving the rest at the current camera (port of bridge.js applyCameraFrom).
export function applyCameraFrom(src: { x?: number; y?: number; zoom?: number }): void {
  const hasX = typeof src.x === 'number'
  const hasY = typeof src.y === 'number'
  const hasZ = typeof src.zoom === 'number'
  if (!(hasX || hasY || hasZ)) return
  const cur = editor.getViewportPageBounds().center
  const z = hasZ ? (src.zoom as number) : editor.getZoomLevel()
  const x = hasX ? (src.x as number) : cur.x
  const y = hasY ? (src.y as number) : cur.y
  const vsb = editor.getViewportScreenBounds()
  setCamera({ x: vsb.w / (2 * z) - x, y: vsb.h / (2 * z) - y, z }, { force: true })
}

// Zoom toward a screen point, keeping the page point under the cursor fixed
// (used by the iframe wheel-forward path in M6; same math as smart-scroll).
export function zoomCanvasAtClient(clientX: number, clientY: number, deltaY: number): void {
  const inst = store.instance()
  if (inst.lockedCamera) return
  const cam = store.camera()
  const vsb = editor.getViewportScreenBounds()
  const sx = clientX - vsb.x
  const sy = clientY - vsb.y
  const z0 = cam.z
  const z1 = Math.min(inst.zoomLimits.max, Math.max(inst.zoomLimits.min, z0 * Math.exp(-deltaY * 0.0015)))
  if (z1 === z0) return
  const k = 1 / z1 - 1 / z0
  setCamera({ x: cam.x + sx * k, y: cam.y + sy * k, z: z1 })
}

// --- once-per-load initial auto-fit -----------------------------------------
let initialFitDone = false
let initialFitTimer: any = null

// Cancel/disable the fit (a scroll mode or explicit camera owns the view).
export function markInitialFitDone(): void {
  initialFitDone = true
  if (initialFitTimer) {
    clearTimeout(initialFitTimer)
    initialFitTimer = null
  }
}

// Re-arm on a run change so the new run's panels get framed.
export function resetInitialFit(): void {
  initialFitDone = false
  if (initialFitTimer) {
    clearTimeout(initialFitTimer)
    initialFitTimer = null
  }
}

// (Re)arm the debounced one-shot fit. `hasExplicitCamera` lets the caller (bridge)
// skip the fit when serve(view={x/y/zoom}) pinned the camera.
export function scheduleInitialFit(hasExplicitCamera: () => boolean): void {
  if (initialFitDone) return
  if (hasExplicitCamera()) return
  if (initialFitTimer) clearTimeout(initialFitTimer)
  initialFitTimer = setTimeout(runInitialFit, 180)
}

function runInitialFit(): void {
  initialFitTimer = null
  if (initialFitDone) return
  const bounds = currentPageBounds()
  if (!bounds) return // no panels yet; a later register reschedules
  initialFitDone = true
  fitCameraToBounds(bounds)
}
