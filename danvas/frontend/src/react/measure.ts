// Measure a caption's natural (auto-width) or wrapped (fixedWidth) size in PAGE
// units: a detached, hidden div at the page font size (no camera transform), so
// the result maps 1:1 onto props.w/h. Used to reflow a text box's height when its
// wrap width is dragged — resizing happens with no live editor to read scrollHeight.
import { DRAW_FONT } from './palette'
import { sanitizeRich } from './richtext'

export function measuredTextSize(text: string, fontPx: number, fixedWidth?: number): { w: number; h: number } {
  if (typeof document === 'undefined') return { w: fixedWidth || fontPx * 4, h: fontPx * 1.3 }
  const d = document.createElement('div')
  d.style.cssText =
    `position:absolute;visibility:hidden;left:-99999px;top:-99999px;` +
    `white-space:${fixedWidth ? 'pre-wrap' : 'pre'};word-break:break-word;` +
    `font:${fontPx}px/1.25 ${DRAW_FONT};`
  if (fixedWidth) d.style.width = `${fixedWidth}px`
  d.innerHTML = sanitizeRich(text) || 'X'
  document.body.appendChild(d)
  const r = { w: d.offsetWidth, h: d.offsetHeight }
  document.body.removeChild(d)
  return r
}
