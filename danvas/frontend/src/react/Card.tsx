// The card chrome shared by every panel — port of canvas.jsx's Card/CardLabel/
// DragHandle + the colour helpers. tldraw's HTMLContainer is replaced by a plain
// div (class `pc-card`, so theme.css hover rules still apply); the lock / grab /
// ghost overlay logic and pointer policy are preserved verbatim. Hit-testing for
// "drawing on top" is stubbed (no drawing layer until M8), so under the select
// tool a panel claims the pointer exactly as before.
import { useEditor, useValue } from './EngineContext'

// Derive tinted frame CSS variables from an accent hex (frameColor).
function deriveFrameVars(fc: string, isDark: boolean): Record<string, string> {
  const r = parseInt(fc.slice(1, 3), 16)
  const g = parseInt(fc.slice(3, 5), 16)
  const b = parseInt(fc.slice(5, 7), 16)
  const rn = r / 255,
    gn = g / 255,
    bn = b / 255
  const max = Math.max(rn, gn, bn),
    min = Math.min(rn, gn, bn),
    d = max - min
  let h = 0
  if (d > 0) {
    if (max === rn) h = ((gn - bn) / d) % 6
    else if (max === gn) h = (bn - rn) / d + 2
    else h = (rn - gn) / d + 4
    h = h * 60
    if (h < 0) h += 360
  }
  const l = (max + min) / 2
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1))
  function hsl(h: number, s: number, l: number): string {
    const a = s * Math.min(l, 1 - l)
    const k = (n: number) => {
      const kv = (n + h / 30) % 12
      return l - a * Math.max(-1, Math.min(kv - 3, 9 - kv, 1))
    }
    return `rgb(${Math.round(k(0) * 255)},${Math.round(k(8) * 255)},${Math.round(k(4) * 255)})`
  }
  return isDark
    ? {
        '--pc-bg': hsl(h, Math.min(s, 0.6), 0.15),
        '--pc-border': hsl(h, Math.min(s, 0.7), 0.27),
        '--pc-shadow': `rgba(${r},${g},${b},0.25)`,
      }
    : {
        '--pc-bg': hsl(h, Math.min(s, 0.4), 0.94),
        '--pc-border': hsl(h, Math.min(s, 0.5), 0.78),
        '--pc-shadow': `rgba(${r},${g},${b},0.1)`,
      }
}

function frameLabelColor(fc: string, isDark: boolean): string {
  if (isDark) return fc
  const r = parseInt(fc.slice(1, 3), 16)
  const g = parseInt(fc.slice(3, 5), 16)
  const b = parseInt(fc.slice(5, 7), 16)
  const rn = r / 255,
    gn = g / 255,
    bn = b / 255
  const max = Math.max(rn, gn, bn),
    min = Math.min(rn, gn, bn),
    d = max - min
  let h = 0
  if (d > 0) {
    if (max === rn) h = ((gn - bn) / d) % 6
    else if (max === gn) h = (bn - rn) / d + 2
    else h = (rn - gn) / d + 4
    h = h * 60
    if (h < 0) h += 360
  }
  const a = 0.38 * Math.min(0.38, 1 - 0.38)
  const k = (n: number) => {
    const kv = (n + h / 30) % 12
    return 0.38 - a * Math.max(-1, Math.min(kv - 3, 9 - kv, 1))
  }
  return `rgb(${Math.round(k(0) * 255)},${Math.round(k(8) * 255)},${Math.round(k(4) * 255)})`
}

export function cardStyle(shape: any, isDark = false): any {
  const noFrame = !!shape.meta?.noFrame
  const fc = shape.meta?.frameColor
  const frameVars = !noFrame && fc ? deriveFrameVars(fc, isDark) : {}
  return {
    display: 'flex',
    flexDirection: 'column',
    width: shape.props.w,
    height: shape.props.h,
    boxSizing: 'border-box',
    padding: noFrame ? 0 : '10px 12px',
    background: noFrame ? 'transparent' : 'var(--pc-bg)',
    color: 'var(--pc-text)',
    border: noFrame ? 'none' : '1px solid var(--pc-border)',
    borderRadius: 8,
    boxShadow: noFrame ? 'none' : '0 1px 3px var(--pc-shadow)',
    fontFamily: 'system-ui, sans-serif',
    overflow: 'hidden',
    position: 'relative',
    ...frameVars,
  }
}

function DragHandle() {
  return (
    <div
      className="pc-drag-handle"
      title="drag to move · click to select"
      style={{
        position: 'absolute',
        top: 4,
        right: 4,
        width: 18,
        height: 18,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: 5,
        background: 'var(--pc-bg)',
        border: '1px solid var(--pc-border)',
        boxShadow: '0 1px 2px var(--pc-shadow)',
        cursor: 'grab',
        pointerEvents: 'all',
        zIndex: 2,
      }}
    >
      <svg width="10" height="10" viewBox="0 0 10 10" fill="var(--pc-muted)" aria-hidden="true">
        <circle cx="2.5" cy="2" r="1" />
        <circle cx="7.5" cy="2" r="1" />
        <circle cx="2.5" cy="5" r="1" />
        <circle cx="7.5" cy="5" r="1" />
        <circle cx="2.5" cy="8" r="1" />
        <circle cx="7.5" cy="8" r="1" />
      </svg>
    </div>
  )
}

const labelStyle = {
  fontSize: 12,
  fontWeight: 600,
  color: 'var(--pc-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  marginBottom: 6,
}

export function CardLabel({ shape }: { shape: any }) {
  const editor = useEditor()
  const isDark = useValue('pc-dark', () => editor.user.getIsDarkMode(), [editor])
  if (shape.meta?.noFrame) return null
  const fc = shape.meta?.frameColor
  const style = fc ? { ...labelStyle, color: frameLabelColor(fc, isDark) } : labelStyle
  return <div style={style as any}>{shape.props.label}</div>
}

export function Card({
  shape,
  children,
  grab = false,
  ghostable = false,
  handle = false,
}: {
  shape: any
  children: any
  grab?: boolean
  ghostable?: boolean
  handle?: boolean
}) {
  const editor = useEditor()
  const isDark = useValue('pc-dark', () => editor.user.getIsDarkMode(), [editor])
  const toolIsSelect = useValue('pc-tool', () => editor.getCurrentToolId() === 'select', [editor])
  const nonPanelHovered = useValue(
    'pc-hover',
    () => {
      const hid = editor.getHoveredShapeId()
      if (!hid) return false
      const s: any = editor.getShape(hid)
      return !!s && !String(s.shapeType || '').startsWith('pc')
    },
    [editor],
  )
  const fullyLocked = shape.isLocked
  const blockInput = fullyLocked || shape.meta?.lockInput
  const noGrab = !!shape.meta?.noGrab
  const ghost = ghostable && noGrab && !!shape.meta?.lockInput && !fullyLocked
  const selected = useValue('pc-selected', () => editor.getSelectedShapeIds().includes(shape.id), [editor, shape.id])

  return (
    <div
      className={nonPanelHovered ? 'pc-card pc-draw-passthrough' : 'pc-card'}
      style={
        ghost || !toolIsSelect || nonPanelHovered
          ? // Non-select tool (drawing), ghost, or a drawing hovered on top: the
            // card chrome is pointer-transparent so the gesture reaches the canvas.
            { ...cardStyle(shape, isDark), pointerEvents: 'none' }
          : { ...cardStyle(shape, isDark), pointerEvents: 'all' }
      }
    >
      {children}
      {toolIsSelect && handle && !noGrab && !blockInput && <DragHandle />}
      {toolIsSelect && grab && !noGrab && !selected && !blockInput && (
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'all', cursor: 'grab' }} />
      )}
      {toolIsSelect && blockInput && (
        <div
          style={{ position: 'absolute', inset: 0, pointerEvents: ghost ? 'none' : 'all', cursor: 'default' }}
          onPointerDown={fullyLocked ? (e: any) => e.stopPropagation() : undefined}
        />
      )}
    </div>
  )
}
