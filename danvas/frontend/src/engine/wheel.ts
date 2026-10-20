// What a wheel event means: zoom or pan.
//
// A laptop trackpad sends two-finger swipes as wheel events (deltaX/deltaY),
// and a pinch as a wheel event with ctrlKey set. A mouse wheel sends notched
// vertical steps. So on a trackpad a swipe pans (any direction, diagonals
// included) and a pinch zooms; a mouse wheel keeps zooming at the cursor
// (right-drag / middle-drag / Space-drag still pan). Ctrl/Cmd + wheel always
// zooms.
//
// Telling them apart is a heuristic, decided once per gesture (a gap of
// > 400 ms starts a new one) so a gesture never flips between the two: line
// or page delta modes are wheels; any horizontal component is a trackpad;
// otherwise a big step (>= 50 px) or a Mac's fractional ~4 px notch is a
// wheel, and small whole-number steps are a trackpad. Settings → Scroll wheel overrides it ('zoom' or 'pan') for
// the devices it gets wrong, remembered per browser.
import { signal } from 'alien-signals'
import type { WriteSignal } from './types'

export type WheelMode = 'auto' | 'zoom' | 'pan'
const KEY = 'pc_wheel'

function load(): WheelMode {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'zoom' || v === 'pan' ? v : 'auto'
  } catch {
    return 'auto'
  }
}
export const wheelMode = signal<WheelMode>(load()) as WriteSignal<WheelMode>
export function setWheelMode(m: WheelMode): void {
  wheelMode(m)
  try {
    localStorage.setItem(KEY, m)
  } catch {
    /* private mode: just for this page */
  }
}

// The fields we need — a real WheelEvent, or the copy a Custom iframe posts.
export interface WheelInfo {
  deltaX: number
  deltaY: number
  deltaMode: number
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
  wheelDeltaY?: number
}

let gesture: 'mouse' | 'trackpad' | null = null
let lastAt = 0

function looksLikeMouse(e: WheelInfo): boolean {
  if (e.deltaMode !== 0) return true // lines / pages: a notched wheel (Firefox)
  if (e.deltaX !== 0) return false // a horizontal component: a trackpad
  const a = Math.abs(e.deltaY)
  const wd = e.wheelDeltaY
  if (typeof wd === 'number' && wd !== 0 && wd % 120 !== 0) return false // a fine-grained device
  // Notched wheels step big (Windows/Linux: 100-120 px per notch) or, on a
  // Mac, by ~4.0002 px — never a whole number. A trackpad gesture starts with
  // small whole-number steps.
  return a >= 50 || (a > 0 && !Number.isInteger(a))
}

export function wheelIntent(e: WheelInfo): 'zoom' | 'pan' {
  if (e.ctrlKey || e.metaKey) return 'zoom' // pinch, or Ctrl/Cmd + wheel
  const mode = wheelMode()
  if (mode !== 'auto') return mode
  const now = performance.now()
  if (now - lastAt > 400) gesture = null
  lastAt = now
  if (!gesture) gesture = looksLikeMouse(e) ? 'mouse' : 'trackpad'
  else if (gesture === 'mouse' && e.deltaX !== 0) gesture = 'trackpad'
  return gesture === 'mouse' ? 'zoom' : 'pan'
}

// The pan in screen pixels for a wheel event (line/page modes normalised;
// Shift turns a vertical wheel into a horizontal pan).
export function wheelPanDelta(e: WheelInfo): { dx: number; dy: number } {
  const k = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1
  let dx = e.deltaX * k
  let dy = e.deltaY * k
  if (e.shiftKey && dx === 0) {
    dx = dy
    dy = 0
  }
  return { dx, dy }
}

export function wheelInfo(e: WheelEvent): WheelInfo {
  return {
    deltaX: e.deltaX,
    deltaY: e.deltaY,
    deltaMode: e.deltaMode,
    ctrlKey: e.ctrlKey,
    metaKey: e.metaKey,
    shiftKey: e.shiftKey,
    wheelDeltaY: (e as any).wheelDeltaY,
  }
}
