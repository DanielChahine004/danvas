// Camera pointer input — the engine reimplementation of App.jsx's
// enableRightDragPan + enableSmartScroll. The container owns every wheel gesture
// in a capture-phase listener (so it behaves the same over empty canvas and over
// interactive panels), and right-drag pans. All camera writes go through
// camera.setCamera, which respects the zoom limits and a locked camera. The math
// is identical to the old build (and to the standard convention: screen = (page+cam)*z).
import { setCamera, getScrollMode, relayoutScroll, getScrollFitZoom } from './camera'
import { store } from './store'
import { openContextMenuAt, closeContextMenu } from './contextmenu'
import { gesturing } from './gesture'

const WHEEL_ZOOM_RATE = 0.002
const PINCH_ZOOM_RATE = 0.01

// Webpage-style scroll rule: a touch that begins on an INTERACTIVE element gives that
// element the gesture (slider slides, a plot pans, a bar/header taps, a field focuses);
// a touch on non-interactive content (text, padding, empty canvas) scrolls the page.
// "Interactive" = a form control, OR an element whose cursor signals it's clickable/
// draggable (covers custom widgets like Plotly's drag layers and the table's SVG bars,
// which set their own cursor). Text/default cursors are treated as non-interactive so
// you can scroll by dragging over body text, like a phone browser.
// Form controls, links, ARIA widgets, editables — and Plotly's root (it runs its own
// touch pan/zoom on descendants). `.js-plotly-plot` covers the whole plot so we don't
// rely on its internal cursors.
const INTERACTIVE_SEL =
  'input,button,select,textarea,a,[role="button"],[role="slider"],[role="switch"],[role="tab"],[role="checkbox"],[contenteditable="true"],.js-plotly-plot'
// Only unambiguously clickable/draggable cursors count — NOT text/default/auto — so a
// plain text panel (label, markdown) scrolls instead of being mistaken for a control.
// Clickable custom widgets (the table's filter bars / sort headers) set cursor:pointer.
const INTERACTIVE_CURSORS = new Set(['pointer', 'grab', 'grabbing'])
function isInteractiveTarget(target: EventTarget | null, root: HTMLElement): boolean {
  let n = target as Element | null
  while (n && n !== root && n.nodeType === 1) {
    if ((n as HTMLElement).matches?.(INTERACTIVE_SEL)) return true
    try {
      if (INTERACTIVE_CURSORS.has(getComputedStyle(n).cursor)) return true
    } catch {
      /* getComputedStyle can throw on detached nodes — ignore */
    }
    n = n.parentElement
  }
  return false
}

export function attachCameraInput(el: HTMLElement): () => void {
  // In a scroll mode, re-fit + centre the document column whenever the viewport box
  // changes — a window resize, a phone rotate, the mobile address bar showing/hiding,
  // and crucially the moment the container first gets its real size on load (which
  // otherwise left the column off-centre on a phone until the first drag). A
  // ResizeObserver catches all of these; relayoutScroll preserves the scroll position
  // after the first pass.
  let ro: ResizeObserver | null = null
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
      if (getScrollMode() !== 'free') relayoutScroll()
    })
    ro.observe(el)
  }
  const cleanups = [
    enableRightDragPan(el),
    enableWheelZoom(el),
    enablePinch(el),
    () => ro?.disconnect(),
  ]
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
    // In a scroll mode the lower bound is the fitted column zoom: pinch zooms IN but
    // not OUT past the starting (confined-column) view.
    const lo = getScrollMode() !== 'free' ? Math.max(inst.zoomLimits.min, getScrollFitZoom()) : inst.zoomLimits.min
    const nz = Math.max(lo, Math.min(inst.zoomLimits.max, start.z * (dist / start.dist)))
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
      // Back at (or below) the fitted zoom in a scroll mode → snap the column back to
      // centred/fitted; if still zoomed in, leave the pinched view as-is.
      if (getScrollMode() !== 'free' && store.camera().z <= getScrollFitZoom() * 1.02) relayoutScroll()
    }
  }
  el.addEventListener('pointerdown', onDown, true)
  el.addEventListener('pointermove', onMove, true)
  // Up/cancel on WINDOW (capture), not just el: a finger lifted OFF the canvas
  // (toolbar, browser chrome, off-screen) still ends the pinch, so a missed up can't
  // wedge `gesturing` true and silently kill one-finger scrolling.
  window.addEventListener('pointerup', onUp, true)
  window.addEventListener('pointercancel', onUp, true)
  return () => {
    el.removeEventListener('pointerdown', onDown, true)
    el.removeEventListener('pointermove', onMove, true)
    window.removeEventListener('pointerup', onUp, true)
    window.removeEventListener('pointercancel', onUp, true)
  }
}

// Right-click-drag pans. The context menu is suppressed only when the drag moved
// (>4px), so a plain right-click is still free for a future context menu.
function enableRightDragPan(el: HTMLElement): () => void {
  let panning = false
  let moved = 0
  let lastX = 0
  let lastY = 0
  // A scroll-mode touch scroll is DEFERRED: we record the press but don't grab the
  // gesture until the finger moves past a threshold along the scroll axis — so a TAP
  // (or a small/cross-axis move) still reaches the panel content under it (a table's
  // filter bars, a chat field, etc.). Right-drag and the hand tool grab immediately.
  let pendingPid: number | null = null
  let startX = 0
  let startY = 0
  const SCROLL_THRESHOLD = 8

  const begin = (e: PointerEvent) => {
    panning = true
    moved = 0
    lastX = e.clientX
    lastY = e.clientY
    if (e.pointerType !== 'touch') {
      // A mouse drag explicitly captures `el` so a drag that leaves the window keeps panning.
      try {
        el.setPointerCapture(e.pointerId)
      } catch {
        /* ignore */
      }
    }
    // Iframes click-through for the pan (pointer capture is flaky over sandboxed
    // iframes); also suppress the press's native action so a pan that begins on a
    // control doesn't nudge it. NOT for touch: preventDefault on a touch pointermove
    // can make the browser fire pointercancel (which was ending the scroll the instant
    // it committed); touch-action:none already blocks native scrolling.
    el.classList.add('pc-gesturing')
    if (e.pointerType !== 'touch') {
      e.preventDefault()
      e.stopPropagation()
    }
  }

  const onDown = (e: PointerEvent) => {
    const tool = store.instance().tool
    const inScroll = getScrollMode() !== 'free'
    const handPan = e.button === 0 && tool === 'hand'
    // Immediate grab: a right-drag (any mode), or a hand-tool pan with the mouse, or
    // the hand tool in free (non-document) mode.
    if (e.button === 2 || (handPan && (!inScroll || e.pointerType !== 'touch'))) {
      begin(e)
      return
    }
    // Scroll mode, one-finger touch: webpage rule — if it starts on interactive
    // content, that content owns the gesture (no scroll); otherwise defer and scroll
    // once it moves past the threshold (so a tap on non-interactive content is inert).
    if (e.button === 0 && e.pointerType === 'touch' && inScroll && (tool === 'select' || tool === 'hand')) {
      if (isInteractiveTarget(e.target, el)) return
      pendingPid = e.pointerId
      startX = e.clientX
      startY = e.clientY
      lastX = e.clientX
      lastY = e.clientY
      // Capture the pointer to the STABLE container now. Scrolling transforms the
      // camera LAYER (a child of el), which moves the element under the finger — and
      // the browser fires pointercancel when the touched element moves out from under
      // an active touch, which was ending the scroll the instant it committed.
      // Binding the pointer to el (which never transforms) keeps the gesture alive and
      // routes the moves straight here. Safe: interactive targets were excluded above.
      try {
        el.setPointerCapture(e.pointerId)
      } catch {
        /* ignore */
      }
    }
  }
  const onMove = (e: PointerEvent) => {
    // A two-finger pinch owns the camera; don't also one-finger pan (that dual
    // write is what made pinch flicker with the hand tool selected).
    if (gesturing()) {
      panning = false
      pendingPid = null
      return
    }
    const mode = getScrollMode()
    // Deferred scroll: commit only once past the threshold along the scroll axis.
    if (pendingPid === e.pointerId && !panning) {
      const along = mode === 'scroll_y' ? Math.abs(e.clientY - startY) : Math.abs(e.clientX - startX)
      if (along <= SCROLL_THRESHOLD) return
      pendingPid = null
      begin(e) // resets lastX/Y to here, so the threshold distance isn't a jump
    }
    if (!panning) return
    let dx = e.clientX - lastX
    let dy = e.clientY - lastY
    moved += Math.abs(dx) + Math.abs(dy)
    lastX = e.clientX
    lastY = e.clientY
    // In a scroll mode keep panning on the scroll axis only, so a drag can't knock
    // the centred document column sideways (or scroll the locked axis).
    if (mode === 'scroll_y') dx = 0
    else if (mode === 'scroll_x') dy = 0
    const cam = store.camera()
    setCamera({ x: cam.x + dx / cam.z, y: cam.y + dy / cam.z, z: cam.z })
    if (e.pointerType !== 'touch') {
      e.preventDefault()
      e.stopPropagation()
    }
  }
  const onUp = (e: PointerEvent) => {
    if (pendingPid === e.pointerId) pendingPid = null
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

  // The decisive native-scroll suppressant for touch: a NON-PASSIVE touchmove that
  // preventDefaults while a scroll is pending/active. Pointer-event preventDefault and
  // touch-action did NOT stop this browser from cancelling the touch the moment the
  // scroll committed; preventing the touchmove itself claims the gesture at the touch
  // layer and stops the pointercancel. Scoped to pending/panning so interactive
  // content (slider, plot) — which never sets pendingPid — keeps its native touch.
  const onTouchMove = (e: TouchEvent) => {
    if (pendingPid !== null || panning) e.preventDefault()
  }
  el.addEventListener('pointerdown', onDown, true)
  el.addEventListener('pointerdown', onDownDismiss)
  el.addEventListener('pointermove', onMove, true)
  el.addEventListener('pointerup', onUp, true)
  el.addEventListener('pointercancel', onUp, true)
  el.addEventListener('contextmenu', onContextMenu, true)
  el.addEventListener('touchmove', onTouchMove, { passive: false })
  return () => {
    el.removeEventListener('pointerdown', onDown, true)
    el.removeEventListener('pointerdown', onDownDismiss)
    el.removeEventListener('pointermove', onMove, true)
    el.removeEventListener('pointerup', onUp, true)
    el.removeEventListener('pointercancel', onUp, true)
    el.removeEventListener('contextmenu', onContextMenu, true)
    el.removeEventListener('touchmove', onTouchMove)
  }
}

// The wheel always zooms toward the cursor (mouse and trackpad alike — the
// canvas pans by right-drag, not by scroll), keeping the page point under the
// cursor fixed. ctrl/pinch uses a finer rate. Scroll modes (serve(view=...)) are
// the one exception: they lock zoom and pan one axis.
function enableWheelZoom(el: HTMLElement): () => void {
  const onWheel = (e: WheelEvent) => {
    // A panel marked wheel-local (React forward_wheel=False) keeps the wheel for
    // its own content — a scroll region, a map, a 3D viewer zooming its camera —
    // so don't preventDefault/stopPropagation; let the event reach the content.
    if ((e.target as Element | null)?.closest?.('[data-wheel-local]')) return
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
