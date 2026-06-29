// Style panel (top-right): colour / thickness / fill for new shapes and the
// selected drawings, plus arrange / duplicate / delete actions. Shown when a draw
// tool is active or one or more drawings are selected.
import { useEffect, useRef, useState } from 'preact/hooks'
import { store } from '../engine/store'
import { useValue } from './EngineContext'
import { alignSelection, arrangeSelection, bumpInteracting } from '../engine/interaction'
import { customColors, addCustomColor, editCustomColor, removeCustomColor, similar } from './boardcolors'
import { stylePanelOpen } from './uistate'
import { Icon } from './icons'

const DASHES: [string, string][] = [
  ['solid', 'dashSolid'],
  ['dashed', 'dashDashed'],
  ['dotted', 'dashDotted'],
]

// Premade swatches — the original palette + order. 'black' is the theme-adaptive
// canvas ink: its swatch is resolved per-theme at render (see inkHex), NOT this map
// (the value here is only a representative hex for custom-colour de-duplication).
const PALETTE = ['black', 'blue', 'red', 'green', 'orange', 'violet', 'yellow', 'grey']
const HEX: Record<string, string> = {
  black: '#e6e6e6',
  blue: '#4263eb',
  red: '#e03131',
  green: '#0c8599',
  orange: '#f76707',
  violet: '#ae3ec9',
  yellow: '#ffc034',
  grey: '#9fa8b2',
}
const SIZES: [string, string][] = [
  ['s', 'S'],
  ['m', 'M'],
  ['l', 'L'],
  ['xl', 'XL'],
]
const FONT_PX: Record<string, number> = { s: 14, m: 20, l: 28, xl: 40 } // text px per size
// Discrete opacity stops for the stepped slider (whiteboard-style), low→high.
const OPACITY_STEPS = [0.1, 0.25, 0.5, 0.75, 1]
const FILLS: [string, string][] = [
  ['none', 'fillNone'],
  ['semi', 'fillSemi'],
  ['solid', 'fillSolid'],
]

const wrap: any = {
  position: 'absolute',
  top: 10,
  right: 10,
  zIndex: 320,
  width: 'min(194px, calc(100vw - 16px))',
  maxHeight: 'calc(100dvh - 20px)',
  overflowY: 'auto',
  boxSizing: 'border-box',
  padding: 12,
  borderRadius: 16,
  background: 'var(--ui-bg)',
  border: '1px solid var(--ui-border)',
  boxShadow: 'var(--ui-shadow)',
  backdropFilter: 'blur(6px)',
  fontFamily: 'system-ui, sans-serif',
  fontSize: 12,
  color: 'var(--ui-fg-dim)',
  userSelect: 'none',
}
// Section headers are collapsed to a thin spacer — the icons + tooltips say what
// each control does, so the descriptive text just made the panel taller.
const rowLabel: any = { fontSize: 0, lineHeight: 0, height: 0, margin: '12px 0 0' }
const rowLabelFirst: any = { ...rowLabel, marginTop: 0 }
// Hairline divider between control groups (colour · opacity · fill/dash · size).
const divider: any = { height: 1, background: 'var(--ui-divider)', margin: '11px 0' }

const OPACITY_CSS = `
.pc-opacity{-webkit-appearance:none;appearance:none;height:6px;border-radius:4px;background:var(--ui-track);outline:none;cursor:pointer}
.pc-opacity::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:18px;height:18px;border-radius:50%;background:#fff;border:2px solid var(--ui-accent);cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,0.4)}
.pc-opacity::-moz-range-thumb{width:18px;height:18px;border-radius:50%;background:#fff;border:2px solid var(--ui-accent);cursor:pointer}
`

export function StylePanel() {
  const tool = useValue('sp-tool', () => store.instance().tool, [])
  const style = useValue('sp-style', () => store.instance().style, [])
  const sel = useValue('sp-sel', () => store.instance().selectedIds, [])
  const customs = useValue('sp-customs', () => customColors(), [])
  // 'black' is the theme-adaptive canvas ink (var(--pc-text)); the panel lives
  // OUTSIDE .tl-container so that var doesn't cascade here — resolve it from the
  // theme ourselves so the swatch matches what the stroke actually renders as
  // (dark ink on light canvas, light ink on dark canvas) and flips with the theme.
  const dark = useValue('sp-dark', () => store.instance().darkMode, [])
  // Custom-colour menu: the hex whose edit/delete menu is open (null = closed).
  const [menuColor, setMenuColor] = useState<string | null>(null)
  const origColor = useRef<string>('') // the custom's hex when its menu opened
  // On phones the panel is hidden until the toolbar's style toggle opens it; on
  // desktop it's always shown (preconfigure).
  const panelOpen = useValue('sp-open', () => stylePanelOpen(), [])
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 680px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 680px)')
    const on = () => setIsMobile(mq.matches)
    mq.addEventListener?.('change', on)
    return () => mq.removeEventListener?.('change', on)
  }, [])
  // Reactive arrowhead state of the first selected line — TRACKED (store.get) so
  // the toggles re-render when the props change (peek would go stale, so a second
  // toggle re-applied the same value: the "can't toggle more than once" bug).
  const lineState = useValue(
    'sp-lineprops',
    () => {
      const id = store.instance().selectedIds.find((i) => {
        const r = store.peek(i) as any
        return (r?.typeName === 'drawing' && r.shapeType === 'line') || r?.typeName === 'arrow'
      })
      if (!id) return null
      const r = store.get(id) as any
      if (!r) return null
      const isArr = r.typeName === 'arrow'
      // connector arrows carry string heads (arrowheadStart/End), defaulting
      // to an END head; user lines carry boolean arrowStart/arrowEnd.
      const hs = isArr ? (r.props.arrowheadStart !== undefined ? r.props.arrowheadStart !== 'none' : !!r.props.arrowStart) : !!r.props.arrowStart
      const he = isArr ? (r.props.arrowheadEnd !== undefined ? r.props.arrowheadEnd !== 'none' : (r.props.arrowEnd ?? true)) : !!(r.props.arrowEnd ?? r.props.arrow)
      return { arrowStart: hs, arrowEnd: he, kind: r.props.arrowKind || 'straight' }
    },
    [],
  )
  // Colour/fill/dash/size to DISPLAY (which control reads active): the first
  // selected styleable shape's own props when there's a selection, else the default
  // style — so the panel reflects what's actually on the selection, never a stale
  // value left over from a previous selection. TRACKED so it updates on every patch.
  const disp = useValue(
    'sp-disp',
    () => {
      const st = store.instance().style as any
      const id = store.instance().selectedIds.find((i) => {
        const r = store.peek(i) as any
        return (r?.typeName === 'drawing' && r.shapeType !== 'image') || r?.typeName === 'arrow'
      })
      if (!id) return { color: st.color, fill: st.fill, dash: st.dash, size: st.size }
      const p = (store.get(id) as any)?.props || {}
      return { color: p.color ?? st.color, fill: p.fill ?? st.fill, dash: p.dash ?? st.dash, size: p.size ?? st.size }
    },
    [],
  )
  // Text alignment to display: the first selected text-bearing shape's effective
  // align (text boxes default left, labels centre), else the default style.
  const dispAlign = useValue(
    'sp-align',
    () => {
      const st = store.instance().style as any
      const id = store.instance().selectedIds.find((i) => {
        const r = store.peek(i) as any
        if (r?.typeName === 'arrow') return !!r.props?.text
        if (r?.typeName !== 'drawing') return false
        if (r.shapeType === 'text' || r.shapeType === 'note') return true
        return (r.shapeType === 'geo' || r.shapeType === 'line') && !!r.props?.text
      })
      if (!id) return st.align || 'left'
      const r = store.get(id) as any
      const dflt = r.typeName === 'drawing' && r.shapeType === 'text' ? 'left' : 'center'
      return r.props?.align ?? dflt
    },
    [],
  )
  // Opacity to display: the first selected drawing's (tracked), else the default.
  const dispOpacity = useValue(
    'sp-opacity',
    () => {
      const id = store.instance().selectedIds.find((i) => store.peek(i)?.typeName === 'drawing')
      if (id) {
        const r = store.get(id) as any
        return r && typeof r.opacity === 'number' ? r.opacity : 1
      }
      return store.instance().style.opacity ?? 1
    },
    [],
  )

  const isDrawTool = tool !== 'select' && tool !== 'hand' && tool !== 'eraser'
  // Phone: shown only when the toggle opens it (not auto on tool select). Desktop:
  // always shown to pre-configure styles. (All hooks run above this gate.)
  if (isMobile && !panelOpen) return null

  // Drawings AND connector arrows take colour/size/dash/opacity, so a canvas.connect
  // arrow is as controllable as a user-drawn one. (Arrow edits are local — there's
  // no wire message to push appearance back to Python.)
  const selDrawings = sel.filter((id) => {
    const t = store.peek(id)?.typeName
    return t === 'drawing' || t === 'arrow'
  })
  // The panel stays in the top-right for ANY tool, so you can pre-configure the
  // colour/thickness/fill/opacity of the next shape before drawing it. With a
  // selection it edits that instead. (Hidden only via App's kiosk `hideUi`.)
  const noSel = sel.length === 0
  // Style (colour/thickness/fill) applies to drawings — not to a selected panel
  // (Python-owned, no such props) or image; suppress those, show only actions.
  const selStyleable = selDrawings.filter((id) => (store.peek(id) as any)?.shapeType !== 'image')
  const showStyle = noSel || isDrawTool || selStyleable.length > 0
  // Opacity applies to ANY drawing (images included) + the default style.
  const showOpacity = noSel || isDrawTool || selDrawings.length > 0

  const applyOpacity = (v: number) => {
    bumpInteracting()
    store.transact('local', () => {
      store.instance({ ...store.instance(), style: { ...store.instance().style, opacity: v } })
      for (const id of selDrawings) store.patch(id, { opacity: v })
    })
  }

  // Arrowheads apply to selected line/arrow drawings (start = ←, end = →; both =
  // two-way). DrawingLayer reads props.arrowEnd ?? props.arrow (legacy) for the end.
  const selLines = sel.filter((id) => {
    const r = store.peek(id) as any
    return (r?.typeName === 'drawing' && r.shapeType === 'line') || r?.typeName === 'arrow'
  })
  // Text-bearing shapes/labels take a text-alignment control (left/centre/right).
  const selText = sel.filter((id) => {
    const r = store.peek(id) as any
    if (r?.typeName === 'arrow') return !!r.props?.text
    if (r?.typeName !== 'drawing') return false
    if (r.shapeType === 'text' || r.shapeType === 'note') return true
    return (r.shapeType === 'geo' || r.shapeType === 'line') && !!r.props?.text
  })
  const applyAlign = (a: string) => {
    if (sel.length) bumpInteracting()
    store.transact('local', () => {
      store.instance({ ...store.instance(), style: { ...store.instance().style, align: a } as any })
      for (const id of selText) store.patch(id, { props: { align: a } as any })
    })
  }
  const arrowStart = lineState?.arrowStart ?? false
  const arrowEnd = lineState?.arrowEnd ?? false
  const arrowKind = lineState?.kind ?? (store.instance().style as any).arrowKind ?? 'straight'
  const applyArrow = (patch: any) =>
    store.transact('local', () => {
      // mirror the boolean toggle into the connector-arrow string head props too
      const full = { ...patch }
      if ('arrowStart' in patch) full.arrowheadStart = patch.arrowStart ? 'arrow' : 'none'
      if ('arrowEnd' in patch) full.arrowheadEnd = patch.arrowEnd ? 'arrow' : 'none'
      for (const id of selLines) store.patch(id, { props: { ...full } })
    })
  // Arrow routing style. Also stored on the default style so the NEXT arrow drawn
  // uses it, and applied to any selected lines.
  const applyKind = (kind: string) => {
    store.transact('local', () => {
      const inst = store.instance()
      store.instance({ ...inst, style: { ...inst.style, arrowKind: kind } as any })
      // 'straight' also resets any prior bend so it actually straightens.
      for (const id of selLines) store.patch(id, { props: { arrowKind: kind, ...(kind === 'straight' ? { bend: 0 } : {}) } })
    })
  }

  const describe = (): string => {
    if (sel.length > 1) return `${sel.length} selected`
    const r = store.peek(sel[0]) as any
    if (!r) return ''
    if (r.typeName === 'panel') return 'Panel'
    if (r.typeName === 'arrow') return 'Arrow'
    const st = r.shapeType
    if (st === 'geo') return r.props.geo === 'ellipse' ? 'Ellipse' : 'Rectangle'
    if (st === 'line') return r.props.arrow ? 'Arrow' : 'Line'
    if (st === 'note') return 'Sticky note'
    if (st === 'draw' || st === 'highlight') return 'Drawing'
    return st ? st[0].toUpperCase() + st.slice(1) : 'Shape'
  }

  const apply = (patch: any) => {
    if (sel.length) bumpInteracting() // hide the box so the change is visible
    store.transact('local', () => {
      store.instance({ ...store.instance(), style: { ...store.instance().style, ...patch } })
      for (const id of selDrawings) store.patch(id, { props: { ...patch } })
    })
  }
  // Size also drives the FONT of text shapes (so the S/M/L/XL buttons resize text,
  // not just stroke). A text box keeps its proportions: its box scales with the font.
  const applySize = (s: string) => {
    if (sel.length) bumpInteracting()
    store.transact('local', () => {
      store.instance({ ...store.instance(), style: { ...store.instance().style, size: s } as any })
      for (const id of selDrawings) {
        const r = store.peek(id) as any
        if (r?.typeName === 'drawing' && r.shapeType === 'text') {
          const oldFs = r.props?.fontSize ?? FONT_PX[r.props?.size as string] ?? 20
          const nf = FONT_PX[s] ?? 20
          const ratio = oldFs ? nf / oldFs : 1
          store.patch(id, { props: { size: s, fontSize: nf, w: Math.max(12, (r.props?.w || 0) * ratio), h: Math.max(12, (r.props?.h || 0) * ratio) } as any })
        } else {
          store.patch(id, { props: { size: s } as any })
        }
      }
    })
  }
  // Re-colour every shape currently using `oldC` to `newC`, so editing a custom
  // colour updates all references to it at once.
  const recolor = (oldC: string, newC: string) =>
    store.transact('local', () => {
      for (const id of store.ids()) {
        const r = store.peek(id) as any
        if (r && r.typeName === 'drawing' && r.props?.color === oldC) store.patch(id, { props: { color: newC } })
      }
    })
  const openCustomMenu = (hex: string) => {
    origColor.current = hex
    setMenuColor(hex)
  }
  // Live edit: as the user drags through the picker, recolour every reference from
  // the current menu colour to `raw` and keep the preview/default in step.
  const liveEditCustom = (raw: string) => {
    const v = (raw || '').toLowerCase()
    if (!v || v === menuColor) return
    recolor(menuColor as string, v)
    if (style.color === menuColor) apply({ color: v })
    setMenuColor(v)
  }
  // Commit the new colour into the saved customs (on the picker's `change`).
  const commitCustom = (raw: string) => {
    const v = (raw || '').toLowerCase()
    if (v && v !== origColor.current) {
      editCustomColor(origColor.current, v)
      origColor.current = v
    }
  }

  // A colour cell: a small circle inside a larger rounded-rect hit target. The
  // rounded rect highlights when active — bigger + easier to click than ringing
  // the circle itself.
  const cell = (selected: boolean, greyed?: boolean): any => ({
    width: '100%',
    aspectRatio: '1',
    minWidth: 0,
    padding: 0,
    border: 'none',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 8,
    cursor: greyed ? 'default' : 'pointer',
    background: selected ? 'var(--ui-hover)' : 'transparent',
    boxShadow: selected ? 'inset 0 0 0 1.5px var(--ui-accent, #3b82f6)' : 'none',
    position: 'relative',
  })
  const dot = (bg: string, greyed?: boolean): any => ({
    width: 22,
    height: 22,
    borderRadius: '50%',
    boxSizing: 'border-box',
    background: greyed ? 'transparent' : bg,
    // a ring (theme border colour) so near-bg colours (black/white) stay visible
    border: greyed ? '1.5px dashed var(--ui-border)' : '1px solid var(--ui-border)',
  })
  // The theme ink for the 'black' swatch — matches theme.css --pc-text exactly so
  // the dot previews the real stroke colour for the current canvas theme.
  const inkHex = dark ? '#e6e6e6' : '#222222'
  // Premade swatch (resolved by colour name).
  const swatch = (c: string) => (
    <button key={c} title={c} onClick={() => { setMenuColor(null); apply({ color: c }) }} style={cell(disp.color === c)}>
      <div style={dot(c === 'black' ? inkHex : HEX[c])} />
    </button>
  )
  // Custom swatch: click = apply · double-click = open its edit/delete menu.
  const customSwatch = (c: string) => (
    <button
      key={'cust:' + c}
      title={`${c} — double-click to edit or delete`}
      onClick={() => { setMenuColor(null); apply({ color: c }) }}
      onDblClick={(e: any) => {
        e.preventDefault()
        openCustomMenu(c)
      }}
      style={cell(disp.color === c)}
    >
      <div style={dot(c)} />
    </button>
  )
  // Customs the user has picked, de-duped against the premades.
  const premadeHexes = PALETTE.map((n) => (HEX[n] || '').toLowerCase())
  const customList = customs.filter((c) => c && c.startsWith('#') && !premadeHexes.some((p) => similar(p, c)))
  // A segmented-control cell. `content` is either a string label or an Icon name.
  const seg = (active: boolean, onClick: () => void, content: any, key: string, title?: string) => (
    <button
      key={key}
      title={title}
      onClick={onClick}
      style={{
        flex: 1,
        minWidth: 0,
        height: 30,
        borderRadius: 8,
        cursor: 'pointer',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 13,
        fontWeight: 700,
        color: active ? 'var(--ui-accent-fg)' : 'var(--ui-fg-dim)',
        background: active ? 'var(--ui-accent)' : 'var(--ui-btn)',
      }}
    >
      {content}
    </button>
  )

  return (
    <div style={wrap} data-pc-stylepanel="">
      <style>{OPACITY_CSS}</style>
      {sel.length > 0 && (
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ui-fg)', marginBottom: showOpacity || showStyle ? 10 : 2 }}>{describe()}</div>
      )}

      {showStyle && (
        <>
          <div style={sel.length > 0 ? rowLabel : rowLabelFirst}>Colour</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4 }}>
            {PALETTE.map(swatch)}
            {/* spectral picker — opens the OS wheel to add a NEW custom colour */}
            <label title="Pick a custom colour" style={{ ...cell(false), cursor: 'pointer' }}>
              <div style={{ width: 22, height: 22, borderRadius: '50%', background: 'conic-gradient(red, yellow, lime, aqua, blue, magenta, red)', boxShadow: 'inset 0 0 0 1px rgba(0,0,0,0.25)' }} />
              <input
                type="color"
                // open at the CURRENT colour (not #000000 → the picker showed black
                // until you raised value/saturation).
                value={(disp.color || '').startsWith('#') ? disp.color : HEX[disp.color] || '#4263eb'}
                // add the pick to the customs on the native `change` (fires ONCE on
                // close); preact maps onChange→input, which spammed a preset per
                // intermediate colour while dragging the wheel.
                ref={(el: any) => {
                  if (el && !el.dataset.pcBound) {
                    el.dataset.pcBound = '1'
                    el.addEventListener('change', () => addCustomColor((el.value || '').toLowerCase()))
                  }
                }}
                onInput={(e: any) => apply({ color: (e.target.value || '').toLowerCase() })}
                style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0, cursor: 'pointer', border: 'none', padding: 0 }}
              />
            </label>
            {/* the user's custom colours (or a greyed first-custom placeholder) */}
            {customList.length === 0 ? (
              <div title="Custom colours you pick appear here" style={cell(false, true)}>
                <div style={dot('', true)} />
              </div>
            ) : (
              customList.map(customSwatch)
            )}
          </div>
          {/* custom-colour menu (double-click a custom): live edit via the picker
              + a Delete button (so removing isn't an accidental right-click). */}
          {menuColor && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginTop: 7, padding: 5, borderRadius: 9, background: 'var(--ui-btn)' }}>
              <label title="Edit colour (drag updates everything live)" style={{ position: 'relative', width: 30, height: 30, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 8, cursor: 'pointer' }}>
                <div style={dot(menuColor)} />
                <input
                  type="color"
                  value={menuColor}
                  ref={(el: any) => {
                    if (el && !el.dataset.pcb) {
                      el.dataset.pcb = '1'
                      el.addEventListener('change', () => commitCustom(el.value))
                    }
                  }}
                  onInput={(e: any) => liveEditCustom(e.target.value)}
                  style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0, cursor: 'pointer', border: 'none', padding: 0 }}
                />
              </label>
              <span style={{ flex: 1, fontSize: 11.5, color: 'var(--ui-fg-dim)' }}>Edit colour</span>
              <button
                onClick={() => { removeCustomColor(origColor.current || menuColor); setMenuColor(null) }}
                title="Delete this custom colour"
                style={{ width: 30, height: 30, flex: 'none', borderRadius: 7, border: 'none', cursor: 'pointer', background: 'var(--ui-danger-bg)', color: 'var(--ui-danger-fg)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
              >
                <Icon name="trash" size={15} />
              </button>
              <button
                onClick={() => setMenuColor(null)}
                title="Done"
                style={{ width: 30, height: 30, flex: 'none', borderRadius: 7, border: 'none', cursor: 'pointer', background: 'var(--ui-btn)', color: 'var(--ui-fg-dim)', fontSize: 15, fontWeight: 700 }}
              >
                ✓
              </button>
            </div>
          )}
        </>
      )}

      {showOpacity && (
        <>
          {showStyle ? <div style={divider} /> : <div style={sel.length > 0 ? rowLabel : rowLabelFirst} />}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* 5-step (discrete) opacity slider — snaps to OPACITY_STEPS. */}
            <input
              class="pc-opacity"
              type="range"
              min={0}
              max={OPACITY_STEPS.length - 1}
              step={1}
              value={OPACITY_STEPS.reduce((best, v, i) => (Math.abs(v - dispOpacity) < Math.abs(OPACITY_STEPS[best] - dispOpacity) ? i : best), OPACITY_STEPS.length - 1)}
              onInput={(e: any) => applyOpacity(OPACITY_STEPS[+e.target.value])}
              style={{ flex: 1 }}
            />
            <span style={{ width: 34, textAlign: 'right', fontSize: 11.5, fontWeight: 600, color: 'var(--ui-fg-dim)' }}>{Math.round(dispOpacity * 100)}%</span>
          </div>
        </>
      )}

      {showStyle && (
        <>
          <div style={divider} />
          <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>{FILLS.map(([f, icon]) => seg(disp.fill === f, () => apply({ fill: f }), <Icon name={icon} size={18} />, f, f))}</div>
          <div style={{ display: 'flex', gap: 6 }}>{DASHES.map(([d, icon]) => seg(disp.dash === d, () => apply({ dash: d }), <Icon name={icon} size={18} />, d, d))}</div>
          <div style={divider} />
          <div style={{ display: 'flex', gap: 6 }}>{SIZES.map(([s, l]) => seg(disp.size === s, () => applySize(s), l, s))}</div>
        </>
      )}

      {(selText.length > 0 || tool === 'text') && (
        <>
          {showStyle && <div style={divider} />}
          <div style={{ display: 'flex', gap: 6 }}>
            {seg(dispAlign === 'left', () => applyAlign('left'), <Icon name="textAlignLeft" size={18} />, 'tal', 'Align text left')}
            {seg(dispAlign === 'center', () => applyAlign('center'), <Icon name="textAlignCenter" size={18} />, 'tac', 'Align text centre')}
            {seg(dispAlign === 'right', () => applyAlign('right'), <Icon name="textAlignRight" size={18} />, 'tar', 'Align text right')}
          </div>
          {/* divider so the align row isn't crammed against the rows below it */}
          {(selLines.length > 0 || sel.length > 0) && <div style={divider} />}
        </>
      )}

      {selLines.length > 0 && (
        <>
          <div style={rowLabel}>Arrowheads</div>
          <div style={{ display: 'flex', gap: 5 }}>
            {seg(arrowStart, () => applyArrow({ arrowStart: !arrowStart }), 'Start', 'as', 'Toggle the start arrowhead')}
            {seg(arrowEnd, () => applyArrow({ arrowEnd: !arrowEnd }), 'End', 'ae', 'Toggle the end arrowhead')}
          </div>
          <div style={rowLabel}>Arrow style</div>
          <div style={{ display: 'flex', gap: 5 }}>
            {seg(arrowKind === 'straight', () => applyKind('straight'), <Icon name="arrowStraight" size={18} />, 'akS', 'Straight')}
            {seg(arrowKind === 'elbow', () => applyKind('elbow'), <Icon name="arrowElbow" size={18} />, 'akE', 'Right-angle (elbow)')}
            {seg(arrowKind === 'curved', () => applyKind('curved'), <Icon name="arrowCurved" size={18} />, 'akC', 'Curved')}
          </div>
        </>
      )}

      {sel.length > 1 && (
        <>
          <div style={showStyle ? rowLabel : rowLabelFirst}>Align</div>
          <div style={{ display: 'flex', gap: 5 }}>
            {seg(false, () => alignSelection('x', 'start'), <Icon name="alignLeft" size={18} />, 'al', 'Align left')}
            {seg(false, () => alignSelection('x', 'center'), <Icon name="alignCenterX" size={18} />, 'acx', 'Align horizontal centres')}
            {seg(false, () => alignSelection('x', 'end'), <Icon name="alignRight" size={18} />, 'ar', 'Align right')}
            {seg(false, () => alignSelection('y', 'start'), <Icon name="alignTop" size={18} />, 'at', 'Align top')}
            {seg(false, () => alignSelection('y', 'center'), <Icon name="alignMiddleY" size={18} />, 'amy', 'Align vertical centres')}
            {seg(false, () => alignSelection('y', 'end'), <Icon name="alignBottom" size={18} />, 'ab', 'Align bottom')}
          </div>
        </>
      )}

      {sel.length > 0 && (
        <>
          <div style={sel.length > 1 || showStyle ? rowLabel : rowLabelFirst}>Arrange</div>
          <div style={{ display: 'flex', gap: 5 }}>
            {seg(false, () => arrangeSelection('back'), <Icon name="toBack" size={18} />, 'zback', 'Send to back')}
            {seg(false, () => arrangeSelection('backward'), <Icon name="backward" size={18} />, 'zbwd', 'Send backward')}
            {seg(false, () => arrangeSelection('forward'), <Icon name="forward" size={18} />, 'zfwd', 'Bring forward')}
            {seg(false, () => arrangeSelection('front'), <Icon name="toFront" size={18} />, 'zfront', 'Bring to front')}
          </div>
        </>
      )}
    </div>
  )
}
