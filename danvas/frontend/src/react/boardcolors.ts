// Colour presets that come from the board itself: distinct, colourful accent
// colours currently shown on the panels (so you can match what's on the canvas),
// plus a small remembered list of custom colours the user has picked from the
// spectrum. Both feed extra swatches into the StylePanel.
import { signal } from 'alien-signals'
import type { WriteSignal } from '../engine/types'

function rgbToHex(v: string): string | null {
  const m = v && v.match(/rgba?\(([^)]+)\)/)
  if (!m) return null
  const p = m[1].split(',').map((s) => parseFloat(s.trim()))
  const a = p.length > 3 ? p[3] : 1
  if (a < 0.5) return null // mostly-transparent → ignore
  const h = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')
  return '#' + h(p[0]) + h(p[1]) + h(p[2])
}

// Keep only saturated, mid-lightness colours — skip greys, near-black, near-white
// (panel chrome/text), so we surface the actual accent colours.
function colourful(hex: string): boolean {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const sat = max === 0 ? 0 : (max - min) / max
  const light = (max + min) / 2 / 255
  return sat > 0.25 && light > 0.12 && light < 0.92
}

export function similar(a: string, b: string): boolean {
  if (!a || !b || a[0] !== '#' || b[0] !== '#') return a === b
  const d = (i: number) => Math.abs(parseInt(a.slice(i, i + 2), 16) - parseInt(b.slice(i, i + 2), 16))
  return d(1) + d(3) + d(5) < 40
}

// Scan panel DOM for accent colours, most-frequent first, de-duplicated.
export function harvestBoardColors(max = 8): string[] {
  if (typeof document === 'undefined') return []
  const counts = new Map<string, number>()
  let budget = 4000 // cap elements scanned so a huge board can't stall
  for (const panel of Array.from(document.querySelectorAll('[data-pc-panel-id]'))) {
    const els = [panel, ...Array.from(panel.querySelectorAll('*'))]
    for (const el of els) {
      if (budget-- <= 0) break
      const cs = getComputedStyle(el as Element)
      for (const v of [cs.color, cs.backgroundColor, cs.borderTopColor]) {
        const hex = rgbToHex(v)
        if (hex && colourful(hex)) counts.set(hex, (counts.get(hex) || 0) + 1)
      }
    }
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]).map((e) => e[0])
  const out: string[] = []
  for (const c of sorted) {
    if (!out.some((o) => similar(o, c))) out.push(c)
    if (out.length >= max) break
  }
  return out
}

// Remembered custom picks (persisted per-browser).
const KEY = 'pc-custom-colors'
function load(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(v) ? v : []
  } catch {
    return []
  }
}
export const customColors = signal<string[]>(load()) as WriteSignal<string[]>
function save(next: string[]): void {
  customColors(next)
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* private mode */
  }
}
export function addCustomColor(hex: string): void {
  const cur = customColors()
  if (cur.includes(hex)) return
  save([hex, ...cur].slice(0, 8))
}
// Replace a remembered custom colour in place (used when the user double-clicks a
// well to tweak it — keeps its slot so the panel doesn't reshuffle).
export function editCustomColor(oldHex: string, newHex: string): void {
  const cur = customColors()
  const i = cur.indexOf(oldHex)
  if (i < 0 || oldHex === newHex) return
  const next = cur.slice()
  // de-dupe: if newHex already exists elsewhere, drop the old slot; else swap.
  const j = next.indexOf(newHex)
  if (j >= 0 && j !== i) next.splice(i, 1)
  else next[i] = newHex
  save(next)
}
export function removeCustomColor(hex: string): void {
  save(customColors().filter((c) => c !== hex))
}
