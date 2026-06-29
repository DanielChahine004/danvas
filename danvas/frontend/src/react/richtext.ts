// Lightweight rich text for shape/label captions. We store a TINY allowlist of
// inline HTML (bold / italic / underline / strikethrough + <br> line breaks) inside
// props.text, so it remains a plain wire string (danvas-compatible — Python just
// round-trips it) yet supports formatting. Everything is sanitised on render AND on
// edit, so peer- or Python-authored text can never inject real markup/script.
const ALLOWED = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'S', 'STRIKE'])
const BLOCK = new Set(['DIV', 'P', 'LI']) // contentEditable wraps pasted lines in these
const DROP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META'])

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// True if the string carries any of our formatting markup (else it's plain text).
export function isRich(s: string): boolean {
  return typeof s === 'string' && /<(b|strong|i|em|u|s|strike|br)\b/i.test(s)
}

// Reduce arbitrary HTML to the safe inline subset: keep allowed tags (no
// attributes), turn block boundaries + <br> into <br>, escape all text, drop
// everything else (incl. <script>/<style> and their contents).
export function sanitizeRich(input: string): string {
  if (!input) return ''
  if (typeof document === 'undefined' || !isRich(input)) return esc(input)
  const tpl = document.createElement('template')
  tpl.innerHTML = input
  let out = ''
  const block = { first: true }
  const walk = (node: Node) => {
    node.childNodes.forEach((child) => {
      if (child.nodeType === 3) {
        out += esc(child.textContent || '')
      } else if (child.nodeType === 1) {
        const el = child as HTMLElement
        const tag = el.tagName
        if (tag === 'BR') out += '<br>'
        else if (DROP.has(tag)) {
          /* drop tag + contents */
        } else if (ALLOWED.has(tag)) {
          const t = tag.toLowerCase()
          out += `<${t}>`
          walk(el)
          out += `</${t}>`
        } else if (BLOCK.has(tag)) {
          if (!block.first) out += '<br>'
          block.first = false
          walk(el)
        } else {
          walk(el) // unknown wrapper: keep its (escaped) text, drop the tag
        }
      }
    })
  }
  walk(tpl.content)
  return out
}

// Plain-text projection (tags stripped, <br> → \n) — for measuring, blank checks,
// and sizing the caption gap.
export function richToPlain(input: string): string {
  if (!input) return ''
  if (!isRich(input)) return input
  if (typeof document === 'undefined') return input.replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]*>/g, '')
  const tpl = document.createElement('template')
  tpl.innerHTML = sanitizeRich(input)
  tpl.content.querySelectorAll('br').forEach((br) => br.replaceWith('\n'))
  return tpl.content.textContent || ''
}
