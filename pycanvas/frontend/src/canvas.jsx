import { useEffect, useRef } from 'react'
import { BaseBoxShapeUtil, HTMLContainer, T } from 'tldraw'
import Plotly from 'plotly.js-basic-dist-min'
import { sendInput, componentIdOf, registerLive, unregisterLive } from './bridge'

// Shared card styling for all PyCanvas component shapes. The card is pinned to
// the shape's exact w/h (not 100% of an ancestor) so it tracks resizing
// continuously and lines up with tldraw's selection box.
function cardStyle(shape) {
  return {
    display: 'flex',
    flexDirection: 'column',
    width: shape.props.w,
    height: shape.props.h,
    boxSizing: 'border-box',
    padding: '10px 12px',
    background: '#ffffff',
    border: '1px solid #e2e2e2',
    borderRadius: 8,
    boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
    fontFamily: 'system-ui, sans-serif',
    overflow: 'hidden',
    // Anchors the lock overlay (see Card) to the card's own box.
    position: 'relative',
  }
}

// Card chrome shared by every panel. When the shape is fully locked (isLocked),
// it lays a transparent overlay over the content that swallows pointer events,
// so the panel's controls stop responding to interaction. tldraw's isLocked
// only blocks its own select/move/resize gestures — pointer events still reach
// inner HTML (our controls set pointerEvents:'all'), so without this a locked
// slider would keep firing value changes. Pinned panels (movable/resizable
// false) deliberately stay interactive and get no overlay.
function Card({ shape, children }) {
  return (
    <HTMLContainer style={cardStyle(shape)}>
      {children}
      {shape.isLocked && (
        <div
          style={{ position: 'absolute', inset: 0, pointerEvents: 'all', cursor: 'default' }}
          onPointerDown={(e) => e.stopPropagation()}
        />
      )}
    </HTMLContainer>
  )
}

const labelStyle = {
  fontSize: 12,
  fontWeight: 600,
  color: '#666',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  marginBottom: 6,
}

// Shared base for every PyCanvas panel. It reads two per-shape flags from the
// shape's `meta` to support interaction-preserving locks (set from Python via
// `movable` / `resizable`):
//   meta.lockMove   -> the user can't drag the panel (onTranslate pins x/y)
//   meta.lockResize -> the user can't resize it (no resize, handles hidden)
// These only gate *user* gestures; programmatic editor.updateShape calls (the
// Python move()/resize() path) bypass them, and the panel's own controls keep
// working since their pointerdown handlers stopPropagation. For a full lock
// that also blocks interaction, use the shape's top-level `isLocked` instead.
class PcShapeUtil extends BaseBoxShapeUtil {
  canResize(shape) {
    return !shape.meta?.lockResize
  }

  hideResizeHandles(shape) {
    return !!shape.meta?.lockResize
  }

  onTranslate(initial, current) {
    if (initial.meta?.lockMove) {
      // Override the dragged position back to the original each frame so the
      // panel stays put while still being selectable and interactive.
      return { id: initial.id, type: initial.type, x: initial.x, y: initial.y }
    }
  }
}

// --- Slider -----------------------------------------------------------------
export class SliderShapeUtil extends PcShapeUtil {
  static type = 'pcSlider'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    min: T.number,
    max: T.number,
    value: T.number,
  }

  getDefaultProps() {
    return { w: 240, h: 96, label: 'slider', min: 0, max: 100, value: 50 }
  }

  component(shape) {
    const { label, min, max, value } = shape.props
    const id = componentIdOf(shape.id)
    return (
      <Card shape={shape}>
        <div style={labelStyle}>{label}</div>
        <input
          type="range"
          min={min}
          max={max}
          value={value}
          // pointerEvents:'all' lets the input receive clicks; stopPropagation
          // on pointerdown keeps tldraw from starting a drag on the shape.
          style={{ width: '100%', pointerEvents: 'all', cursor: 'pointer' }}
          onPointerDown={(e) => e.stopPropagation()}
          onChange={(e) => {
            const v = Number(e.target.value)
            this.editor.updateShape({
              id: shape.id,
              type: shape.type,
              props: { value: v },
            })
            sendInput(id, { value: v })
          }}
        />
        <div style={{ fontSize: 16, fontWeight: 600, color: '#222', marginTop: 4 }}>
          {value}
        </div>
      </Card>
    )
  }

  indicator(shape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={8} />
  }
}

// --- Label ------------------------------------------------------------------
export class LabelShapeUtil extends PcShapeUtil {
  static type = 'pcLabel'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    value: T.string,
  }

  getDefaultProps() {
    return { w: 240, h: 84, label: 'label', value: '' }
  }

  component(shape) {
    const { label, value } = shape.props
    return (
      <Card shape={shape}>
        <div style={labelStyle}>{label}</div>
        <div style={{ fontSize: 20, fontWeight: 600, color: '#222' }}>{value}</div>
      </Card>
    )
  }

  indicator(shape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={8} />
  }
}

// --- VideoFeed --------------------------------------------------------------
export class VideoShapeUtil extends PcShapeUtil {
  static type = 'pcVideo'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    src: T.string,
  }

  getDefaultProps() {
    return { w: 340, h: 280, label: 'video', src: '' }
  }

  component(shape) {
    const { label, src } = shape.props
    return (
      <Card shape={shape}>
        <div style={labelStyle}>{label}</div>
        <div
          style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: '#111',
            borderRadius: 4,
            overflow: 'hidden',
          }}
        >
          {src ? (
            <img
              src={src}
              draggable={false}
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'contain',
                pointerEvents: 'none',
              }}
            />
          ) : (
            <span style={{ color: '#666', fontSize: 13 }}>no signal</span>
          )}
        </div>
      </Card>
    )
  }

  indicator(shape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={8} />
  }
}

// --- Custom (arbitrary HTML in a sandboxed iframe) --------------------------
export class HtmlShapeUtil extends PcShapeUtil {
  static type = 'pcHtml'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    html: T.string,
  }

  getDefaultProps() {
    return { w: 380, h: 320, label: 'custom', html: '' }
  }

  component(shape) {
    const { label, html } = shape.props
    return (
      <Card shape={shape}>
        {/* Header has no pointerEvents, so dragging it moves the panel. */}
        <div style={labelStyle}>{label}</div>
        <iframe
          title={label}
          srcDoc={html}
          // allow-scripts lets interactive content (e.g. Plotly) run.
          // No allow-same-origin keeps the user HTML sandboxed from the app.
          sandbox="allow-scripts allow-popups allow-forms"
          style={{
            flex: 1,
            width: '100%',
            border: 'none',
            borderRadius: 4,
            background: '#fff',
            pointerEvents: 'all',
          }}
          // Keep tldraw from hijacking drags/zoom meant for the iframe content.
          onPointerDown={(e) => e.stopPropagation()}
        />
      </Card>
    )
  }

  indicator(shape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={8} />
  }
}

// --- Toggle (pick one of N options) -----------------------------------------
export class ToggleShapeUtil extends PcShapeUtil {
  static type = 'pcToggle'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    options: T.arrayOf(T.string),
    value: T.string,
  }

  getDefaultProps() {
    return { w: 260, h: 84, label: 'toggle', options: ['off', 'on'], value: 'off' }
  }

  component(shape) {
    const { label, options, value } = shape.props
    const id = componentIdOf(shape.id)
    return (
      <Card shape={shape}>
        <div style={labelStyle}>{label}</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {options.map((opt) => {
            const active = opt === value
            return (
              <button
                key={opt}
                style={{
                  flex: 1,
                  minWidth: 64,
                  padding: '8px 10px',
                  border: 'none',
                  borderRadius: 6,
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: 'pointer',
                  background: active ? '#2563eb' : '#eee',
                  color: active ? '#fff' : '#333',
                  pointerEvents: 'all',
                }}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => {
                  this.editor.updateShape({
                    id: shape.id,
                    type: shape.type,
                    props: { value: opt },
                  })
                  sendInput(id, { value: opt })
                }}
              >
                {opt}
              </button>
            )
          })}
        </div>
      </Card>
    )
  }

  indicator(shape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={8} />
  }
}

// --- LivePlot (streaming Plotly, no iframe reload) --------------------------
// Plotly is loaded once with the app bundle; data arrives over the bridge's
// live-data channel and is applied with Plotly.react (an efficient diff).
function LivePlotView({ shape }) {
  const ref = useRef(null)
  const id = componentIdOf(shape.id)

  useEffect(() => {
    const node = ref.current
    const render = (plot) => {
      if (!node) return
      Plotly.react(node, plot.data || [], plot.layout || {}, {
        responsive: true,
        displayModeBar: false,
      })
    }
    registerLive(id, render)
    return () => {
      unregisterLive(id)
      if (node) Plotly.purge(node)
    }
  }, [id])

  // Keep the chart sized to the (resizable) shape.
  useEffect(() => {
    if (ref.current) Plotly.Plots.resize(ref.current)
  }, [shape.props.w, shape.props.h])

  return (
    <div
      ref={ref}
      style={{ flex: 1, width: '100%', minHeight: 0, pointerEvents: 'all' }}
      onPointerDown={(e) => e.stopPropagation()}
    />
  )
}

export class LivePlotShapeUtil extends PcShapeUtil {
  static type = 'pcLivePlot'
  static props = { w: T.number, h: T.number, label: T.string }

  getDefaultProps() {
    return { w: 560, h: 380, label: 'live plot' }
  }

  component(shape) {
    return (
      <Card shape={shape}>
        <div style={labelStyle}>{shape.props.label}</div>
        <LivePlotView shape={shape} />
      </Card>
    )
  }

  indicator(shape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={8} />
  }
}

// Map of the `component` string sent by Python -> tldraw shape type.
export const COMPONENT_TO_SHAPE = {
  Slider: 'pcSlider',
  Label: 'pcLabel',
  VideoFeed: 'pcVideo',
  Custom: 'pcHtml',
  Toggle: 'pcToggle',
  LivePlot: 'pcLivePlot',
}

export const shapeUtils = [
  SliderShapeUtil,
  LabelShapeUtil,
  VideoShapeUtil,
  HtmlShapeUtil,
  ToggleShapeUtil,
  LivePlotShapeUtil,
]
