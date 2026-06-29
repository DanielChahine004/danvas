// Inline editor for an editable shape's text (instance.editingId): a text/note
// shape, or a label on a geo shape / line / arrow. It's a transparent contentEditable
// laid over the shape inside a flex container, so the text is centred WHILE editing
// exactly as it renders once committed (a <textarea> can't vertical-centre, which
// made note text edit at the top then snap to the middle). It wraps + grows.
//
// Persistence: every keystroke patches the record's props.text live; commit() reads
// the text back FROM THE RECORD (not the DOM), so an unmount race can't blank it.
import { useState, useEffect } from 'preact/hooks'
import { store } from '../engine/store'
import { pageToScreen } from '../engine/editor'
import { recordBBox } from '../engine/hittest'
import { clipArrow, polyPointAt } from '../engine/lineGeo'
import { colorOf, DRAW_FONT, alignCss } from './palette'
import { sanitizeRich, richToPlain } from './richtext'
import { useValue } from './EngineContext'

const FONT: Record<string, number> = { s: 14, m: 20, l: 28, xl: 40 }
// The rendered line/arrow caption is a 150-PAGE-unit-wide wrapping box (DrawingLayer
// lw); the editor lives in screen space, so it must use lw*z to wrap identically.
const LABEL_W = 150
const fontSz = (s?: string) => FONT[s || 'm'] || 20

// Resolve a `var(--pc-*)` colour to its computed value — the editor sits OUTSIDE
// .tl-container, where those vars don't cascade, so without this a theme colour
// (e.g. an arrow/geo label) would edit in the fallback dark/black then snap to the
// real colour once committed.
function resolveVar(color: string): string {
  if (typeof color !== 'string' || !color.includes('var(') || typeof document === 'undefined') return color
  const m = color.match(/var\((--[\w-]+)\s*(?:,\s*([^)]+))?\)/)
  const el = document.querySelector('.tl-container')
  if (m && el) {
    const v = getComputedStyle(el).getPropertyValue(m[1]).trim()
    return v || (m[2] || '#e6e6e6').trim()
  }
  return color
}

// Seed the contentEditable with the shape's (rich) text and focus it once (callback
// ref, so it runs synchronously on attach — reliable, unlike effect/autofocus).
function initEditable(text: string) {
  return (el: HTMLDivElement | null) => {
    if (!el || el.dataset.pcInit) return
    el.dataset.pcInit = '1'
    el.innerHTML = sanitizeRich(text)
    // emit semantic tags (<b>/<i>/…) from execCommand, not inline-styled spans, so
    // the stored markup matches our sanitiser allowlist.
    try {
      document.execCommand('styleWithCSS', false, 'false')
    } catch {
      /* execCommand may be unavailable */
    }
    el.focus()
    try {
      const sel = window.getSelection()
      const range = document.createRange()
      range.selectNodeContents(el)
      range.collapse(false) // caret to end
      sel?.removeAllRanges()
      sel?.addRange(range)
    } catch {
      /* selection unavailable */
    }
  }
}

// Floating B / I / U / S bar shown while editing. Buttons run the browser's native
// contentEditable formatting on the current selection. onMouseDown preventDefault
// keeps the editor focused + selected (so clicking a button doesn't blur→commit).
function FormatBar({ below }: { below: boolean }) {
  const [active, setActive] = useState<Record<string, boolean>>({})
  useEffect(() => {
    const update = () => {
      try {
        setActive({
          bold: document.queryCommandState('bold'),
          italic: document.queryCommandState('italic'),
          underline: document.queryCommandState('underline'),
          strikeThrough: document.queryCommandState('strikeThrough'),
        })
      } catch {
        /* queryCommandState unavailable */
      }
    }
    document.addEventListener('selectionchange', update)
    update()
    return () => document.removeEventListener('selectionchange', update)
  }, [])
  const btn = (cmd: string, label: any, title: string) => (
    <button
      key={cmd}
      title={title}
      onMouseDown={(e: any) => { e.preventDefault(); e.stopPropagation() }}
      onClick={(e: any) => { e.preventDefault(); document.execCommand(cmd) }}
      style={{
        width: 26, height: 26, border: 'none', borderRadius: 6, cursor: 'pointer',
        background: active[cmd] ? '#2563eb' : 'rgba(255,255,255,0.09)',
        color: active[cmd] ? '#fff' : '#e2e8f0', fontSize: 13, lineHeight: 1,
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'system-ui, sans-serif',
      }}
    >
      {label}
    </button>
  )
  return (
    <div
      onPointerDown={(e: any) => e.stopPropagation()}
      onMouseDown={(e: any) => e.preventDefault()}
      style={{
        position: 'absolute',
        left: '50%',
        transform: 'translateX(-50%)',
        ...(below ? { top: '100%', marginTop: 8 } : { bottom: '100%', marginBottom: 8 }),
        display: 'flex',
        gap: 4,
        padding: 4,
        borderRadius: 9,
        background: 'rgba(24,24,27,0.96)',
        border: '1px solid rgba(255,255,255,0.12)',
        boxShadow: '0 4px 16px rgba(0,0,0,0.45)',
        zIndex: 340,
        pointerEvents: 'auto',
        whiteSpace: 'nowrap',
      }}
    >
      {btn('bold', <b>B</b>, 'Bold (Ctrl+B)')}
      {btn('italic', <i>I</i>, 'Italic (Ctrl+I)')}
      {btn('underline', <span style={{ textDecoration: 'underline' }}>U</span>, 'Underline (Ctrl+U)')}
      {btn('strikeThrough', <span style={{ textDecoration: 'line-through' }}>S</span>, 'Strikethrough')}
    </div>
  )
}

export function TextEditor() {
  const editingId = useValue('te-id', () => store.instance().editingId, [])
  const cam = useValue('te-cam', () => store.camera(), [])
  const rec = useValue('te-rec:' + editingId, () => (editingId ? store.get(editingId) : undefined), [editingId])

  if (!editingId || !rec) return null
  const r = rec as any
  const isArrow = r.typeName === 'arrow'
  const st = r.typeName === 'drawing' ? r.shapeType : null
  // a label that floats over a stroke gets the same halo it renders with, so the
  // line behind it is cut while editing too (no box, matches the committed look)
  const isLineLabel = isArrow || st === 'line'
  const isNote = st === 'note'
  const isText = st === 'text'
  // a text box is auto-width (hug text) until a side handle sets a wrap width
  // (props.autoSize=false) — then it wraps within its box while editing too.
  const autoText = isText && r.props?.autoSize !== false
  const isGeo = st === 'geo'
  const z = cam.z
  const fs = (r.props?.fontSize ?? fontSz(r.props?.size)) * z

  // Box (screen space) + alignment, chosen so it matches what DrawingLayer renders.
  let left = 0,
    top = 0,
    width = 0,
    height = 0,
    hAlign: 'center' | 'flex-start' = 'flex-start',
    vAlign: 'center' | 'flex-start' = 'flex-start',
    color = 'var(--pc-text, #e6e6e6)'
  // text-align follows props.align (text boxes default left, labels centre); set
  // once below so editing aligns the lines exactly as the committed text renders.
  const textAlign = alignCss(r.props?.align, isText ? 'left' : 'center')

  if (isLineLabel) {
    // Position the editor at the rendered label point (on the line/visible arc at
    // labelPosition) with the SAME wrap width the caption renders with (lw*z), so
    // the text wraps + centres exactly as it does once deselected.
    const t = typeof r.props?.labelPosition === 'number' ? Math.max(0, Math.min(1, r.props.labelPosition)) : 0.5
    // clipArrow handles both connector arrows AND user-drawn lines (free or bound),
    // so the caption sits on the visible arc either way.
    const clip = clipArrow(r)
    const mp = clip && clip.visible.length >= 2 ? polyPointAt(clip.visible, t) : null
    if (!mp) return null
    const m = pageToScreen(mp)
    width = LABEL_W * z
    height = Math.max(fs * 2, 40)
    left = m.x - width / 2
    top = m.y - height / 2
    hAlign = vAlign = 'center'
    color = colorOf(r.props?.color)
  } else {
    const b = recordBBox(r)
    if (!b) return null
    const tl = pageToScreen({ x: b.x, y: b.y })
    left = tl.x
    top = tl.y
    width = b.w * z
    height = b.h * z
    if (isNote) {
      left = tl.x + 8 * z
      top = tl.y + 6 * z
      width = (b.w - 16) * z
      height = (b.h - 12) * z
      color = '#1d1d1d'
      hAlign = vAlign = 'center'
    } else if (isText) {
      color = colorOf(r.props.color)
    } else {
      // geo / line label: centred in the box
      hAlign = vAlign = 'center'
      color = isGeo ? 'var(--pc-text, #222)' : colorOf(r.props.color)
    }
  }
  // Resolve any theme var so the caret/text edit in the SAME colour they render.
  color = resolveVar(color)

  const commit = () => {
    store.transact('local', () => {
      const cur = store.peek(editingId) as any
      const text = (cur?.props?.text ?? '').toString()
      // a blank text box / sticky note is dropped; a blank label just stays blank.
      if (richToPlain(text).trim() === '' && (isText || isNote)) store.remove(editingId)
      store.instance({ ...store.instance(), editingId: null })
    })
  }

  return (
    <div
      style={{
        position: 'absolute',
        left,
        top,
        width: Math.max(24, width),
        height: Math.max(fs * 1.4, height),
        zIndex: 330,
        display: 'flex',
        alignItems: vAlign,
        justifyContent: hAlign,
        pointerEvents: 'auto',
      }}
      onPointerDown={(e: any) => {
        // click the empty part of the box -> focus the editable
        e.stopPropagation()
        if (e.target === e.currentTarget) (e.currentTarget.querySelector('[contenteditable]') as HTMLElement | null)?.focus()
      }}
    >
      <FormatBar below={top < 64} />
      <div
        key={editingId}
        ref={initEditable(r.props.text || '')}
        contentEditable
        onInput={(e: any) => {
          const el = e.currentTarget
          store.transact('local', () => {
            // store the SANITISED markup (allowlisted inline tags only); we don't
            // write it back to `el` so the caret isn't disturbed mid-edit.
            const patch: any = { props: { text: sanitizeRich(el.innerHTML) } }
            // a text box auto-fits BOTH axes (whiteboard-style): width to the longest
            // line, height to the line count — so the bounding box hugs the text.
            if (isText) {
              const pf = r.props?.fontSize ?? fontSz(r.props?.size)
              // auto-width: fit BOTH axes. fixed-width (wrapped): keep w, grow height.
              if (autoText) patch.props.w = Math.max(el.scrollWidth / z + 4, pf)
              patch.props.h = Math.max(el.scrollHeight / z, pf * 1.3)
            }
            store.patch(editingId, patch)
          })
        }}
        onPointerDown={(e: any) => e.stopPropagation()}
        onBlur={commit}
        onKeyDown={(e: any) => {
          e.stopPropagation() // keep canvas tool shortcuts from firing while typing
          if (e.key === 'Escape') {
            e.preventDefault()
            commit()
          } else if (e.key === 'Enter') {
            // a clean <br> newline (not a wrapping <div>) so the markup stays tidy
            e.preventDefault()
            document.execCommand('insertLineBreak')
          }
          // Ctrl/Cmd+B/I/U fall through to the browser's native contentEditable
          // formatting (styleWithCSS=false → semantic tags), captured by onInput.
        }}
        style={{
          // line/arrow captions render at line-height 1.2 (DrawingLayer) — match it
          font: `${fs}px/${isLineLabel ? '1.2' : '1.25'} ${DRAW_FONT}`,
          color,
          caretColor: '#3b82f6',
          textAlign,
          outline: 'none',
          border: 'none',
          background: 'transparent',
          // auto-width text grows horizontally (pre); a wrapped (fixed-width) box or
          // any label wraps within its width (pre-wrap, fill the container).
          whiteSpace: autoText ? 'pre' : 'pre-wrap',
          wordBreak: autoText ? 'normal' : 'break-word',
          width: isText && !autoText ? '100%' : undefined,
          maxWidth: autoText ? 'none' : '100%',
          minWidth: 8,
          minHeight: fs,
          pointerEvents: 'all',
        }}
      />
    </div>
  )
}
