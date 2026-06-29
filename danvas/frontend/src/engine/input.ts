// Camera pointer input — the engine reimplementation of App.jsx's
// enableRightDragPan + enableSmartScroll. The container owns every wheel gesture
// in a capture-phase listener (so it behaves the same over empty canvas and over
// interactive panels), and right-drag pans. All camera writes go through
// camera.setCamera, which respects the zoom limits and a locked camera. The math
// is identical to the old build (and to the standard convention: screen = (page+cam)*z).
import { setCamera, getScrollMode } from './camera'
import { store } from './store'
import { openContextMenuAt, closeContextMenu } from './contextmenu'
import { gesturing } from './gesture'

const WHEEL_ZOOM_RATE = 0.002
const PINCH_ZOOM_RATE = 0.01

export function attachCameraInput(el: HTMLElement): () => void {
  const cleanups = [enableRightDragPan(el), enableWheelZoom(el), enablePinch(el)]
  return () => cleanups.forEach((c) => c())
}

// Two-finger pinch = zoom + pan (the standard touch gesture). The transform is
// ANCHORED to the gesture start (initial zoom + finger distance + the page point
// under the initial centroid), not computed incrementally frame-to-frame — so it
// can't drift/oscillate. The anchor point is pinned under the live centroid and
// scaled by the distance ratio, folding zoom and pan into one stable transform.
function enablePinch(el: HTMLElement): () => void {
  const pts = new Map<number, { x: number; y: number }>()
  let start: { z: number; dist: number; p0x: number; p0y: number } | null = null
  // Coalesce camera writes to one per animation frame: touch fires moves at up to
  // 120Hz, and each synchronous setCamera re-renders the overlay/wrapper. The pinch
  // transform is ABSOLUTE (recomputed from the anchor each move), so applying only
  // the latest per frame is exact — no lost movement, much smoother on mobile.
  let pending: { x: number; y: number; z: number } | null = null
  let raf = 0
  const flush = () => {
    raf = 0
    const p = pending
    pending = null
    if (p) setCamera(p)
  }
  const schedule = (cam: { x: number; y: number; z: number }) => {
    pending = cam
    if (!raf) raf = requestAnimationFrame(flush)
  }

  const anchor = () => {
    const [a, b] = [...pts.values()]
    const cx = (a.x + b.x) / 2
    const cy = (a.y + b.y) / 2
    const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1
    const cam = store.camera()
    const rect = el.getBoundingClientRect()
    start = { z: cam.z, dist, p0x: (cx - rect.left) / cam.z - cam.x, p0y: (cy - rect.top) / cam.z - cam.y }
  }
  const onDown = (e: PointerEvent) => {
    if (e.pointerType !== 'touch') return
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pts.size === 2) {
      anchor()
      gesturing(true)
    }
  }
  const onMove = (e: PointerEvent) => {
    if (!pts.has(e.pointerId)) return
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pts.size !== 2 || !start) return
    e.preventDefault()
    e.stopPropagation()
    const [a, b] = [...pts.values()]
    const cx = (a.x + b.x) / 2
    const cy = (a.y + b.y) / 2
    const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1
    const rect = el.getBoundingClientRect()
    const inst = store.instance()
    const nz = Math.max(inst.zoomLimits.min, Math.min(inst.zoomLimits.max, start.z * (dist / start.dist)))
    schedule({ x: (cx - rect.left) / nz - start.p0x, y: (cy - rect.top) / nz - start.p0y, z: nz })
  }
  const onUp = (e: PointerEvent) => {
    if (!pts.has(e.pointerId)) return
    pts.delete(e.pointerId)
    if (pts.size === 2) anchor() // dropped from 3→2: re-anchor to the remaining pair
    else if (pts.size < 2) {
      start = null
      gesturing(false)
      if (raf) flush() // apply the final frame immediately so the camera settles exactly
    }
  }
  el.addEventListener('pointerdown', onDown, true)
  el.addEventListener('pointermove', onMove, true)
  el.addEventListener('pointerup', onUp, true)
  el.addEventListener('pointercancel', onUp, true)
  return () => {
    el.removeEventListener('pointerdown', onDown, true)
    el.removeEventListener('pointermove', onMove, true)
    el.removeEventListener('pointerup', onUp, true)
    el.removeEventListener('pointercancel', onUp, true)
  }
}

// Right-click-drag pans. The context menu is suppressed only when the drag moved
// (>4px), so a plain right-click is still free for a future context menu.
function enableRightDragPan(el: HTMLElement): () => void {
  let panning = false
  let moved = 0
  let lastX = 0
  let lastY = 0

  const onDown = (e: PointerEvent) => {
    // Right-drag always pans; left-drag pans too when the hand tool is active.
    const handPan = e.button === 0 && store.instance().tool === 'hand'
    if (e.button !== 2 && !handPan) return
    panning = true
    moved = 0
    lastX = e.clientX
    lastY = e.clientY
    try {
      el.setPointerCapture(e.pointerId)
    } catch {
      /* ignore */
    }
    // Make panel iframes click-through for the pan so they can't intercept the
    // move events as the cursor crosses them (pointer capture alone is flaky over
    // sandboxed iframes, which made the pan flicker).
    el.classList.add('pc-gesturing')
    e.stopPropagation()
  }
  const onMove = (e: PointerEvent) => {
    // A two-finger pinch owns the camera; don't also one-finger pan (that dual
    // write is what made pinch flicker with the hand tool selected).
    if (gesturing()) {
      panning = false
      return
    }
    if (!panning) return
    const dx = e.clientX - lastX
    const dy = e.clientY - lastY
    moved += Math.abs(dx) + Math.abs(dy)
    lastX = e.clientX
    lastY = e.clientY
    const cam = store.camera()
    setCamera({ x: cam.x + dx / cam.z, y: cam.y + dy / cam.z, z: cam.z })
    e.preventDefault()
    e.stopPropagation()
  }
  const onUp = (e: PointerEvent) => {
    if (!panning) return
    panning = false
    el.classList.remove('pc-gesturing')
    try {
      el.releasePointerCapture(e.pointerId)
    } catch {
      /* ignore */
    }
    e.stopPropagation()
  }
  const onContextMenu = (e: MouseEvent) => {
    // Always suppress the browser menu; open ours only on a click (not a pan-drag).
    e.preventDefault()
    if (moved > 4) return
    openContextMenuAt(e.clientX, e.clientY, e.target)
  }
  // Any left-press dismisses an open context menu.
  const onDownDismiss = (e: PointerEvent) => {
    if (e.button === 0) closeContextMenu()
  }

  el.addEventListener('pointerdown', onDown, true)
  el.addEventListener('pointerdown', onDownDismiss)
  el.addEventListener('pointermove', onMove, true)
  el.addEventListener('pointerup', onUp, true)
  el.addEventListener('pointercancel', onUp, true)
  el.addEventListener('contextmenu', onContextMenu, true)
  return () => {
    el.removeEventListener('pointerdown', onDown, true)
    el.removeEventListener('pointerdown', onDownDismiss)
    el.removeEventListener('pointermove', onMove, true)
    el.removeEventListener('pointerup', onUp, true)
    el.removeEventListener('pointercancel', onUp, true)
    el.removeEventListener('contextmenu', onContextMenu, true)
  }
}

// The wheel always zooms toward the cursor (mouse and trackpad alike — the
// canvas pans by right-drag, not by scroll), keeping the page point under the
// cursor fixed. ctrl/pinch uses a finer rate. Scroll modes (serve(view=...)) are
// the one exception: they lock zoom and pan one axis.
function enableWheelZoom(el: HTMLElement): () => void {
  const onWheel = (e: WheelEvent) => {
    const mode = getScrollMode()
    if (mode !== 'free') {
      e.preventDefault()
      e.stopPropagation()
      const cam = store.camera()
      const scale = e.deltaMode === 1 ? 20 : e.deltaMode === 2 ? 400 : 1
      if (mode === 'scroll_y') {
        setCamera({ x: cam.x, y: cam.y - e.deltaY * scale, z: cam.z })
      } else {
        const dx = (e.deltaX + e.deltaY) * scale
        setCamera({ x: cam.x - dx, y: cam.y, z: cam.z })
      }
      return
    }

    e.preventDefault()
    e.stopPropagation()
    const inst = store.instance()
    const cam = store.camera()
    const rect = el.getBoundingClientRect()
    const sx = e.clientX - rect.left
    const sy = e.clientY - rect.top
    // Normalise line/page mode to pixels.
    const dy = e.deltaMode === 1 ? e.deltaY * 40 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY
    const rate = e.ctrlKey ? PINCH_ZOOM_RATE : WHEEL_ZOOM_RATE
    const newZ = Math.max(inst.zoomLimits.min, Math.min(inst.zoomLimits.max, cam.z * Math.exp(-dy * rate)))
    if (newZ === cam.z) return
    const newX = sx / newZ - sx / cam.z + cam.x
    const newY = sy / newZ - sy / cam.z + cam.y
    setCamera({ x: newX, y: newY, z: newZ })
  }

  el.addEventListener('wheel', onWheel, { capture: true, passive: false })
  return () => el.removeEventListener('wheel', onWheel, { capture: true } as any)
}
