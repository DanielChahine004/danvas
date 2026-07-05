// Per-panel accent theme derivation — the frontend-owned port of
// danvas/components/_theme.py::derive. A panel that registers with a
// top-level `frameColor` but no `_th` in its data blob gets the derived
// CSS variables here, so SDKs only ever send the one accent color and the
// palette math can't drift between languages. (An owner-sent `_th` — what
// older Python wheels and live `post_style` restyles carry — always wins.)

function rgbToHls(r: number, g: number, b: number): [number, number, number] {
  const maxc = Math.max(r, g, b)
  const minc = Math.min(r, g, b)
  const l = (minc + maxc) / 2
  if (maxc === minc) return [0, l, 0]
  const d = maxc - minc
  const s = l <= 0.5 ? d / (maxc + minc) : d / (2 - maxc - minc)
  const rc = (maxc - r) / d
  const gc = (maxc - g) / d
  const bc = (maxc - b) / d
  let h: number
  if (r === maxc) h = bc - gc
  else if (g === maxc) h = 2 + rc - bc
  else h = 4 + gc - rc
  h = (((h / 6) % 1) + 1) % 1
  return [h, l, s]
}

function hlsToRgb(h: number, l: number, s: number): [number, number, number] {
  if (s === 0) return [l, l, l]
  const m2 = l <= 0.5 ? l * (1 + s) : l + s - l * s
  const m1 = 2 * l - m2
  const v = (hue: number): number => {
    hue = ((hue % 1) + 1) % 1
    if (hue < 1 / 6) return m1 + (m2 - m1) * hue * 6
    if (hue < 0.5) return m2
    if (hue < 2 / 3) return m1 + (m2 - m1) * (2 / 3 - hue) * 6
    return m1
  }
  return [v(h + 1 / 3), v(h), v(h - 1 / 3)]
}

function parseHex(color: string): [number, number, number] | null {
  let s = String(color).trim().replace(/^#/, '')
  if (s.length === 3) s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2]
  if (!/^[0-9a-fA-F]{6}$/.test(s)) return null
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)]
}

const hex = (r: number, g: number, b: number) =>
  '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')

/** The `_th` CSS-variable dict for an accent color, or null for bad input. */
export function deriveTheme(color: string): Record<string, string> | null {
  const rgb = parseHex(color)
  if (!rgb) return null
  const [r, g, b] = rgb
  const [h, l, s] = rgbToHls(r / 255, g / 255, b / 255)
  const clamp = (x: number) => Math.max(0, Math.min(1, x))
  const [dr, dg, db] = hlsToRgb(h, clamp(l * 0.75), clamp(s))
  const lum =
    0.2126 * Math.pow(r / 255, 2.2) +
    0.7152 * Math.pow(g / 255, 2.2) +
    0.0722 * Math.pow(b / 255, 2.2)
  return {
    '--pc-accent': hex(r, g, b),
    '--pc-accent-dk': hex(Math.round(dr * 255), Math.round(dg * 255), Math.round(db * 255)),
    '--pc-accent-t': `rgba(${r},${g},${b},.35)`,
    '--pc-accent-text': lum < 0.18 ? '#fff' : '#1a1a2e',
  }
}
