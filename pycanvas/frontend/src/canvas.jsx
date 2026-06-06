import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { BaseBoxShapeUtil, HTMLContainer, T, useEditor, useValue } from 'tldraw'
import Plotly from 'plotly.js-basic-dist-min'
import { sendInput, componentIdOf, registerLive, unregisterLive, requestCompletions } from './bridge'

// Monaco is heavy, so the Repl editor is code-split into its own chunk that
// only loads when a Repl panel is shown (see MonacoRepl.jsx).
const MonacoRepl = lazy(() => import('./MonacoRepl'))

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
    background: 'var(--pc-bg)',
    color: 'var(--pc-text)',
    border: '1px solid var(--pc-border)',
    borderRadius: 8,
    boxShadow: '0 1px 3px var(--pc-shadow)',
    fontFamily: 'system-ui, sans-serif',
    overflow: 'hidden',
    // Anchors the lock overlay (see Card) to the card's own box.
    position: 'relative',
  }
}

// Card chrome shared by every panel. When the shape is fully locked (isLocked)
// or input-locked (meta.lockInput), it lays a transparent overlay over the
// content. The overlay sits on top with pointerEvents:'all', so it — not the
// inner controls — receives the pointer, making the contents inert (tldraw's
// isLocked only blocks its own gestures; pointer events still reach inner HTML,
// which sets pointerEvents:'all', so without this a locked slider would keep
// firing value changes).
//
// The two locks differ in what they do with that pointer, keeping *content
// interactivity* separate from *move/resize/select permission*:
//   - isLocked (full lock): the overlay swallows the event (stopPropagation) so
//     nothing reaches tldraw — no interaction at all.
//   - meta.lockInput only: the overlay lets the event bubble to tldraw, so the
//     panel can still be selected/moved/resized exactly as its movable /
//     resizable / locked permissions allow — only the inner controls are inert.
//     The shape stays unlocked, so Python value updates still render and a
//     slider thumb keeps tracking them while the user can't drag it.
// Pinned panels (movable/resizable false) stay fully interactive: no overlay.
function Card({ shape, children }) {
  const fullyLocked = shape.isLocked
  const blockInput = fullyLocked || shape.meta?.lockInput
  return (
    <HTMLContainer style={cardStyle(shape)}>
      {children}
      {blockInput && (
        <div
          style={{ position: 'absolute', inset: 0, pointerEvents: 'all', cursor: 'default' }}
          // Full lock swallows the event; input-only lock lets it bubble so
          // tldraw's move/select/resize still obey the panel's own permissions.
          onPointerDown={fullyLocked ? (e) => e.stopPropagation() : undefined}
        />
      )}
    </HTMLContainer>
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
        <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--pc-text)', marginTop: 4 }}>
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
        <div style={{ fontSize: 20, fontWeight: 600, color: 'var(--pc-text)' }}>{value}</div>
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
            background: 'var(--pc-video-bg)',
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
            <span style={{ color: 'var(--pc-muted)', fontSize: 13 }}>no signal</span>
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
            background: 'var(--pc-bg)',
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
                  background: active ? 'var(--pc-accent)' : 'var(--pc-off-bg)',
                  color: active ? 'var(--pc-accent-text)' : 'var(--pc-off-text)',
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

// --- Repl (code cell against the shared kernel namespace) -------------------
export class ReplShapeUtil extends PcShapeUtil {
  static type = 'pcRepl'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    code: T.string,
    output: T.string,
    result: T.string,
  }

  getDefaultProps() {
    return { w: 460, h: 260, label: 'repl', code: '', output: '', result: '' }
  }

  component(shape) {
    const { label, code, output, result } = shape.props
    const id = componentIdOf(shape.id)
    // Follow tldraw's theme so the Monaco editor uses a matching dark/light
    // syntax theme (CSS vars handle the rest of the card). useValue keeps this
    // reactive, so toggling tldraw's dark mode re-themes the editor live.
    const dark = useValue('pc-dark', () => this.editor.user.getIsDarkMode(), [])
    // Run the given text (from the editor), falling back to the stored prop.
    const run = (text) =>
      sendInput(id, { code: text != null ? text : shape.props.code })
    const setCode = (v) =>
      this.editor.updateShape({ id: shape.id, type: shape.type, props: { code: v } })
    return (
      <Card shape={shape}>
        <div style={labelStyle}>{label}</div>
        <Suspense
          fallback={
            <div
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--pc-faint)',
                fontSize: 13,
                border: '1px solid var(--pc-border)',
                borderRadius: 4,
              }}
            >
              loading editor…
            </div>
          }
        >
          <MonacoRepl
            value={code}
            dark={dark}
            onChange={setCode}
            onRun={run}
            onComplete={(text) => requestCompletions(id, text)}
          />
        </Suspense>
        <button
          style={{
            alignSelf: 'flex-start',
            marginTop: 6,
            padding: '4px 10px',
            border: 'none',
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 600,
            background: 'var(--pc-accent)',
            color: 'var(--pc-accent-text)',
            cursor: 'pointer',
            pointerEvents: 'all',
          }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => run()}
        >
          Run (⌘/Ctrl+Enter)
        </button>
        {(output || result) && (
          <pre
            style={{
              margin: '6px 0 0',
              maxHeight: '40%',
              overflow: 'auto',
              background: 'var(--pc-code-bg)',
              color: 'var(--pc-text)',
              borderRadius: 4,
              fontSize: 12,
              padding: 6,
              whiteSpace: 'pre-wrap',
              pointerEvents: 'all',
            }}
            onPointerDown={(e) => e.stopPropagation()}
          >
            {output}
            {result ? `=> ${result}` : ''}
          </pre>
        )}
      </Card>
    )
  }

  indicator(shape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={8} />
  }
}

// --- Inspector (live table of canvas components or kernel globals) ----------
const INSPECTOR_COLS = ['name', 'type', 'value', 'x', 'y', 'w', 'h']

// The body is a component so the search box / type filter can hold local state
// (they filter the already-sent rows client-side, so typing is instant).
function InspectorView({ shape }) {
  const id = componentIdOf(shape.id)
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  // Which row is drilled into (its key), or null for the table view.
  const [selected, setSelected] = useState(null)

  let rows = []
  try {
    rows = JSON.parse(shape.props.rows) || []
  } catch {
    rows = []
  }
  let cols = INSPECTOR_COLS
  try {
    const parsed = JSON.parse(shape.props.cols)
    if (Array.isArray(parsed) && parsed.length) cols = parsed
  } catch {
    // keep default
  }

  const controlStyle = {
    fontSize: 12,
    padding: '3px 6px',
    border: '1px solid var(--pc-border-mid)',
    borderRadius: 6,
    background: 'var(--pc-input-bg)',
    color: 'var(--pc-text)',
    pointerEvents: 'all',
  }

  // --- detail (drill-down) view -------------------------------------------
  if (selected != null) {
    let detail = null
    try {
      detail = JSON.parse(shape.props.detail || 'null')
    } catch {
      detail = null
    }
    // Only show detail once it's arrived for the row we clicked (avoid stale).
    const ready = detail && detail.key === selected
    return (
      <DetailView
        selected={selected}
        detail={ready ? detail : null}
        onBack={() => {
          setSelected(null)
          sendInput(id, { action: 'detail', key: null }) // stop live updates
        }}
        onRefresh={() => sendInput(id, { action: 'detail', key: selected })}
        controlStyle={controlStyle}
      />
    )
  }

  const types = ['all', ...Array.from(new Set(rows.map((r) => r.type))).sort()]
  const q = query.toLowerCase()
  const shown = rows.filter(
    (r) =>
      (typeFilter === 'all' || r.type === typeFilter) &&
      (!q || String(r.name ?? '').toLowerCase().includes(q))
  )

  const openDetail = (r) => {
    const key = r.key ?? r.name
    if (!key) return
    setSelected(key)
    sendInput(id, { action: 'detail', key })
  }

  const source = shape.props.source || 'components'
  const switchSource = (next) => {
    if (next === source) return
    setTypeFilter('all') // the type set differs between the two views
    setQuery('')
    sendInput(id, { action: 'source', source: next })
  }

  return (
    <>
      <div
        style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center' }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <select
          value={source}
          onChange={(e) => switchSource(e.target.value)}
          style={controlStyle}
          title="what to inspect"
        >
          <option value="components">panels</option>
          <option value="globals">globals</option>
        </select>
        <input
          placeholder="search name…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ ...controlStyle, flex: 1, minWidth: 0 }}
        />
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          style={controlStyle}
        >
          {types.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <button
          style={{ ...controlStyle, cursor: 'pointer' }}
          onClick={() => sendInput(id, { action: 'refresh' })}
        >
          Refresh
        </button>
      </div>
      <div
        style={{ flex: 1, minHeight: 0, overflow: 'auto', pointerEvents: 'all' }}
        // Keep tldraw from treating a row click as a panel drag.
        onPointerDown={(e) => e.stopPropagation()}
      >
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {cols.map((c) => (
                <th
                  key={c}
                  style={{
                    textAlign: 'left',
                    padding: '2px 6px',
                    borderBottom: '1px solid var(--pc-border-mid)',
                    color: 'var(--pc-muted)',
                    position: 'sticky',
                    top: 0,
                    background: 'var(--pc-bg)',
                  }}
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr
                key={i}
                onClick={() => openDetail(r)}
                style={{ cursor: 'pointer' }}
                title="click to inspect fields"
              >
                {cols.map((c) => (
                  <td
                    key={c}
                    style={{
                      padding: '2px 6px',
                      borderBottom: '1px solid var(--pc-border-soft)',
                      fontFamily: c === 'value' ? 'ui-monospace, monospace' : 'inherit',
                    }}
                  >
                    {String(r[c] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// Drill-down: an object's type/repr header plus a field/type/value table.
function DetailView({ selected, detail, onBack, onRefresh, controlStyle }) {
  const fields = detail && Array.isArray(detail.fields) ? detail.fields : []
  return (
    <>
      <div
        style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center' }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <button style={{ ...controlStyle, cursor: 'pointer' }} onClick={onBack}>
          ← back
        </button>
        <span
          style={{
            flex: 1,
            minWidth: 0,
            fontSize: 13,
            fontWeight: 600,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {selected}
          {detail && (
            <span style={{ fontWeight: 400, color: 'var(--pc-faint)' }}> : {detail.type}</span>
          )}
        </span>
        <button style={{ ...controlStyle, cursor: 'pointer' }} onClick={onRefresh}>
          Refresh
        </button>
      </div>
      <div
        style={{ flex: 1, minHeight: 0, overflow: 'auto', pointerEvents: 'all' }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        {!detail ? (
          <div style={{ fontSize: 12, color: 'var(--pc-faint2)', padding: 6 }}>loading…</div>
        ) : detail.missing ? (
          <div style={{ fontSize: 12, color: 'var(--pc-faint2)', padding: 6 }}>
            no longer available
          </div>
        ) : (
          <>
            <div
              style={{
                fontSize: 12,
                fontFamily: 'ui-monospace, monospace',
                color: 'var(--pc-detail-text)',
                background: 'var(--pc-detail-bg)',
                border: '1px solid var(--pc-detail-border)',
                borderRadius: 4,
                padding: '4px 6px',
                marginBottom: 6,
                wordBreak: 'break-all',
              }}
            >
              {detail.repr}
            </div>
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  {['field', 'type', 'value'].map((c) => (
                    <th
                      key={c}
                      style={{
                        textAlign: 'left',
                        padding: '2px 6px',
                        borderBottom: '1px solid var(--pc-border-mid)',
                        color: 'var(--pc-muted)',
                        position: 'sticky',
                        top: 0,
                        background: 'var(--pc-bg)',
                      }}
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {fields.length === 0 ? (
                  <tr>
                    <td
                      colSpan={3}
                      style={{ padding: 6, color: 'var(--pc-faint2)', fontStyle: 'italic' }}
                    >
                      no fields — see repr above
                    </td>
                  </tr>
                ) : (
                  fields.map((f, i) => (
                    <tr key={i}>
                      <td style={{ padding: '2px 6px', borderBottom: '1px solid var(--pc-border-soft)' }}>
                        {f.field}
                      </td>
                      <td
                        style={{
                          padding: '2px 6px',
                          borderBottom: '1px solid var(--pc-border-soft)',
                          color: 'var(--pc-faint)',
                        }}
                      >
                        {f.type}
                      </td>
                      <td
                        style={{
                          padding: '2px 6px',
                          borderBottom: '1px solid var(--pc-border-soft)',
                          fontFamily: 'ui-monospace, monospace',
                        }}
                      >
                        {f.value}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </>
        )}
      </div>
    </>
  )
}

export class InspectorShapeUtil extends PcShapeUtil {
  static type = 'pcInspector'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    rows: T.string,
    cols: T.string,
    detail: T.string,
    source: T.string,
  }

  getDefaultProps() {
    return {
      w: 520,
      h: 320,
      label: 'inspector',
      rows: '[]',
      cols: JSON.stringify(INSPECTOR_COLS),
      detail: '',
      source: 'components',
    }
  }

  component(shape) {
    return (
      <Card shape={shape}>
        <div style={labelStyle}>{shape.props.label}</div>
        <InspectorView shape={shape} />
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
  Repl: 'pcRepl',
  Inspector: 'pcInspector',
}

export const shapeUtils = [
  SliderShapeUtil,
  LabelShapeUtil,
  VideoShapeUtil,
  HtmlShapeUtil,
  ToggleShapeUtil,
  LivePlotShapeUtil,
  ReplShapeUtil,
  InspectorShapeUtil,
]
