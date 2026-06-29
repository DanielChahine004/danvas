// Shared drawing colour palette + resolver, used by the renderer (DrawingLayer)
// and the inline editor (TextEditor) so a shape's text shows the SAME colour
// while you're editing it as it does once rendered (no colour pop on commit).
export const COLORS: Record<string, string> = {
  black: '#1d1d1d',
  grey: '#9fa8b2',
  'light-violet': '#e599f7',
  violet: '#ae3ec9',
  blue: '#4263eb',
  indigo: '#5f3dc4',
  'light-blue': '#4dabf7',
  yellow: '#ffc034',
  orange: '#f76707',
  green: '#0c8599',
  'light-green': '#40c057',
  'light-red': '#ff8787',
  red: '#e03131',
  white: '#ffffff',
}

// No colour (or 'black') follows the canvas theme so a shape stays visible on
// both light and dark; named colours render as their palette hex.
export const colorOf = (c?: string): string =>
  !c || c === 'black' ? 'var(--pc-text, #1d1d1d)' : COLORS[c] || c

// Shared font for all drawing text (shapes, notes, labels) + the inline editor,
// so what you type matches what renders. Prefers Inter / Segoe UI Variable where
// installed, then the platform UI font.
export const DRAW_FONT = "'Inter Variable', 'Inter', 'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"

// Text alignment for shape/label text. props.align → a CSS text-align, falling
// back to the per-shape default (text boxes left, captions/labels centred).
export type Align = 'left' | 'center' | 'right'
export const alignCss = (a: any, dflt: Align): Align => (a === 'left' || a === 'center' || a === 'right' ? a : dflt)

// Half-extents (page units) of an ellipse that covers a line/arrow caption, used
// as an SVG mask to CUT the stroke out from behind the text — a clean vector gap
// (no box, no jagged text-shadow knockout). `lw` is the caption's wrap width
// (DrawingLayer's foreignObject width). The text soft-wraps at lw, so estimate the
// wrapped line count + widest line and pad a little so the stroke clears the glyphs.
export function labelEllipse(text: string, fontPx: number, lw = 150): { rx: number; ry: number } {
  const lines = String(text || '').split('\n')
  let rows = 0
  let widest = 0
  for (const ln of lines) {
    const w = ln.length * fontPx * 0.55 // rough glyph advance
    rows += Math.max(1, Math.ceil(w / lw))
    widest = Math.max(widest, Math.min(lw, w))
  }
  const w = Math.max(fontPx, widest)
  const h = Math.max(1, rows) * fontPx * 1.2
  // pad enough that a stroke grazing the text edge (incl. a bent curve) is cleared
  return { rx: w / 2 + fontPx * 0.6, ry: h / 2 + fontPx * 0.45 }
}
