import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { BaseBoxShapeUtil, HTMLContainer, T, useEditor, useValue } from 'tldraw'
import Plotly from 'plotly.js-basic-dist-min'
import {
  sendInput,
  componentIdOf,
  registerLive,
  unregisterLive,
  requestCompletions,
  subscribeIdentity,
  subscribeChat,
  getChatLog,
  sendChat,
  setMyName,
} from './bridge'

// Monaco is heavy, so the Repl editor is code-split into its own chunk that
// only loads when a Repl panel is shown (see MonacoRepl.jsx).
const MonacoRepl = lazy(() => import('./MonacoRepl'))

// The React-panel host bundles a JSX compiler (Babel, ~3 MB), so it too is
// code-split and only loaded the first time a React panel appears.
const ReactHost = lazy(() => import('./ReactHost'))

// Shared card styling for all PyCanvas component shapes. The card is pinned to
// the shape's exact w/h (not 100% of an ancestor) so it tracks resizing
// continuously and lines up with tldraw's selection box. A frameless panel
// (meta.noFrame, Python `frame=False`) keeps the same flex box but drops every
// visible piece of chrome — background, border, shadow, padding — so its
// content appears to sit directly on the canvas.
function cardStyle(shape) {
  const noFrame = !!shape.meta?.noFrame
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
//
// ``grab``: panels whose *whole body* is interactive (iframes, plots, chat,
// repl, inspector, react) would otherwise swallow every click into their content
// — so tldraw never sees it and the panel can't be selected or dragged by its
// body, only its thin header. Where panels overlap, that makes the top one hard
// to grab and the click seems to fall through to the panel underneath. For those
// panels, while the panel is *unselected* we lay a transparent cover over it: the
// cover takes the pointer (so the content doesn't) but does **not**
// stopPropagation, so the event bubbles to tldraw and selects/drags the topmost
// panel normally. The first click selects it; the cover then lifts and the
// content becomes interactive. Small-control panels (slider/toggle/button) pass
// ``grab=false`` and keep single-click interaction.
//
// The cover has a cost: while it's up, the pointer never reaches the content,
// so CSS :hover inside the panel is dead until the first click (native panels
// and iframes alike). Python can opt a panel out with ``selectable=False``
// (meta.noGrab): no cover, content live from the first hover, body clicks
// never select the panel.
function Card({ shape, children, grab = false }) {
  const editor = useEditor()
  const fullyLocked = shape.isLocked
  const blockInput = fullyLocked || shape.meta?.lockInput
  const noGrab = !!shape.meta?.noGrab
  const selected = useValue(
    'pc-selected',
    () => editor.getSelectedShapeIds().includes(shape.id),
    [editor, shape.id]
  )
  return (
    <HTMLContainer style={cardStyle(shape)}>
      {children}
      {grab && !noGrab && !selected && !blockInput && (
        <div
          // No handler / no stopPropagation: the event bubbles to tldraw, which
          // selects + (on drag) moves the topmost panel — fixing overlap grabs.
          style={{ position: 'absolute', inset: 0, pointerEvents: 'all', cursor: 'grab' }}
        />
      )}
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

// The caption header every panel shows. Part of the card chrome, so a
// frameless panel (meta.noFrame) hides it along with the rest of the card.
// It has no pointerEvents, so on grab-style panels dragging it moves the panel.
function CardLabel({ shape }) {
  if (shape.meta?.noFrame) return null
  return <div style={labelStyle}>{shape.props.label}</div>
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

  // Shared hover/selection outline. Suppressed for frameless panels
  // (meta.noFrame) so nothing draws a rectangle around their bare content, and
  // for non-grabbable panels (meta.noGrab, Python grabable=False): those can't
  // be hovered or selected by the user, so the light-blue edge highlight should
  // never appear on them. Returning null here is the definitive guard — it's the
  // only thing that paints the outline, regardless of any stray hover/select
  // state. (The bridge's page-state filter still keeps them out of selection.)
  indicator(shape) {
    if (shape.meta?.noFrame || shape.meta?.noGrab) return null
    return <rect width={shape.props.w} height={shape.props.h} rx={8} />
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
// A manual number-entry box that mirrors the slider value. It keeps a local
// draft while typing (so partial entries like "-" or "1." aren't fought by the
// controlled value) and commits a clamped, step-rounded number on blur/Enter.
function SliderNumberEntry({ value, min, max, step, onCommit }) {
  const [draft, setDraft] = useState(null)
  const text = draft !== null ? draft : String(value)
  const commit = () => {
    if (draft === null) return
    const v = Number(draft)
    if (Number.isFinite(v) && draft.trim() !== '') {
      onCommit(Math.min(max, Math.max(min, v)))
    }
    setDraft(null) // fall back to the shape's value (rejects bad input)
  }
  return (
    <input
      type="number"
      min={min}
      max={max}
      step={step}
      value={text}
      style={{
        width: 72,
        marginTop: 4,
        fontSize: 16,
        fontWeight: 600,
        color: 'var(--pc-text)',
        background: 'var(--pc-bg)',
        border: '1px solid var(--pc-border, #ccc)',
        borderRadius: 4,
        padding: '2px 4px',
        pointerEvents: 'all',
      }}
      onPointerDown={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.target.blur()
      }}
    />
  )
}

// The interactive body of a slider panel: the range thumb + the manual entry
// box. The shape's value is always updated locally as the thumb moves, so it
// tracks the cursor live regardless of mode. `onRelease` only governs *when
// Python hears about it*: when false (default) every change is sent; when true
// the drag stays silent and a single value is sent once the gesture ends
// (pointer release, key release, or blur), so a frantic drag can't flood a slow
// on_change handler.
function SliderControl({ shape, editor, onRelease }) {
  const { min, max, step, value } = shape.props
  const id = componentIdOf(shape.id)
  const pending = useRef(null) // value awaiting an on-release commit

  // Reflect a value on the panel immediately (responsive thumb + number box).
  const setLocal = (v) =>
    editor.updateShape({ id: shape.id, type: shape.type, props: { value: v } })

  // Send the value held back during an on-release drag (no-op otherwise).
  const commit = () => {
    if (pending.current === null) return
    sendInput(id, { value: pending.current })
    pending.current = null
  }

  const apply = (v) => {
    setLocal(v)
    if (onRelease) pending.current = v
    else sendInput(id, { value: v })
  }

  // Don't strand an un-committed value if the panel unmounts mid-drag.
  useEffect(() => commit, [])

  return (
    <>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        // pointerEvents:'all' lets the input receive clicks; stopPropagation
        // on pointerdown keeps tldraw from starting a drag on the shape.
        style={{ width: '100%', pointerEvents: 'all', cursor: 'pointer' }}
        onPointerDown={(e) => e.stopPropagation()}
        onChange={(e) => apply(Number(e.target.value))}
        // In on-release mode the value is sent only when the gesture ends —
        // covering mouse/touch (pointerUp), keyboard (keyUp), and focus loss.
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
      <SliderNumberEntry
        value={value}
        min={min}
        max={max}
        step={step}
        // A typed entry is a single deliberate value — send it straight through.
        onCommit={(v) => { setLocal(v); sendInput(id, { value: v }) }}
      />
    </>
  )
}

export class SliderShapeUtil extends PcShapeUtil {
  static type = 'pcSlider'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    min: T.number,
    max: T.number,
    step: T.number,
    on_release: T.boolean,
    value: T.number,
  }

  getDefaultProps() {
    return { w: 240, h: 96, label: 'slider', min: 0, max: 100, step: 1, on_release: false, value: 50 }
  }

  component(shape) {
    return (
      <Card shape={shape}>
        <CardLabel shape={shape} />
        <SliderControl shape={shape} editor={this.editor} onRelease={shape.props.on_release} />
      </Card>
    )
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
        <CardLabel shape={shape} />
        <div style={{ fontSize: 20, fontWeight: 600, color: 'var(--pc-text)' }}>{value}</div>
      </Card>
    )
  }

}

// --- VideoFeed --------------------------------------------------------------
// Frames arrive as binary WebSocket payloads on the live-data channel (no
// base64, no shape-prop churn). Each is wrapped in a Blob and shown via an
// object URL; the previous URL is revoked once the next frame paints so the
// stream doesn't leak memory.
function VideoView({ shape }) {
  const imgRef = useRef(null)
  const urlRef = useRef(null)
  const id = componentIdOf(shape.id)
  const [live, setLive] = useState(false)

  useEffect(() => {
    const show = (payload) => {
      const el = imgRef.current
      if (!el) return
      const url = URL.createObjectURL(new Blob([payload], { type: 'image/jpeg' }))
      const prev = urlRef.current
      // Revoke the prior frame's URL only after the new one has painted.
      el.onload = () => {
        if (prev) URL.revokeObjectURL(prev)
      }
      urlRef.current = url
      el.src = url
      setLive(true) // React bails if already true, so this is cheap per frame
    }
    registerLive(id, show)
    return () => {
      unregisterLive(id)
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current)
        urlRef.current = null
      }
    }
  }, [id])

  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        // Frameless: transparent letterbox bars instead of a dark slab.
        background: shape.meta?.noFrame ? 'transparent' : 'var(--pc-video-bg)',
        borderRadius: 4,
        overflow: 'hidden',
      }}
    >
      <img
        ref={imgRef}
        draggable={false}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'contain',
          pointerEvents: 'none',
          display: live ? 'block' : 'none',
        }}
      />
      {!live && <span style={{ color: 'var(--pc-muted)', fontSize: 13 }}>no signal</span>}
    </div>
  )
}

export class VideoShapeUtil extends PcShapeUtil {
  static type = 'pcVideo'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
  }

  getDefaultProps() {
    return { w: 340, h: 280, label: 'video' }
  }

  component(shape) {
    return (
      <Card shape={shape}>
        <CardLabel shape={shape} />
        <VideoView shape={shape} />
      </Card>
    )
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
    // Prose panels (Markdown) set this so CustomView blends the iframe into the
    // canvas theme instead of showing it as a white notebook document.
    themed: T.boolean,
  }

  getDefaultProps() {
    return { w: 380, h: 320, label: 'custom', html: '', themed: false }
  }

  component(shape) {
    return (
      <Card shape={shape} grab>
        {/* Header has no pointerEvents, so dragging it moves the panel. */}
        <CardLabel shape={shape} />
        <CustomView shape={shape} />
      </Card>
    )
  }

}

// The sandboxed iframe that hosts a Custom panel's HTML. Beyond rendering the
// HTML, it subscribes to the bridge's live-data channel: Python ``push()`` calls
// arrive here and are forwarded into the iframe via postMessage (as a `message`
// event whose `data.__pycanvas` is the payload), so live data can stream in
// *without* replacing srcDoc and reloading the frame. That keeps the iframe's
// focus and listeners intact — essential for streaming + interactive panels.
function CustomView({ shape }) {
  const ref = useRef(null)
  const id = componentIdOf(shape.id)
  const editor = useEditor()
  // A `themed` panel (Markdown) blends into the canvas: its document body is
  // transparent so the panel's --pc-bg shows through, and we hand the iframe a
  // `color-scheme` matching tldraw's theme so the doc's prefers-color-scheme
  // query (which colours its text) tracks the dark/light toggle, not the OS.
  const dark = useValue('pc-dark', () => editor.user.getIsDarkMode(), [editor])

  useEffect(() => {
    const post = (data) => {
      const el = ref.current
      if (el && el.contentWindow) {
        el.contentWindow.postMessage({ __pycanvas: data }, '*')
      }
    }
    registerLive(id, post)
    return () => unregisterLive(id)
  }, [id])

  return (
    <iframe
      ref={ref}
      title={shape.props.label}
      srcDoc={shape.props.html}
      // allow-scripts lets interactive content (e.g. Plotly) run.
      // No allow-same-origin keeps the user HTML sandboxed from the app.
      sandbox="allow-scripts allow-popups allow-forms"
      style={{
        flex: 1,
        width: '100%',
        border: 'none',
        borderRadius: 4,
        // A frameless panel keeps the iframe transparent too, so user HTML
        // with a transparent body floats directly on the canvas.
        background: shape.meta?.noFrame ? 'transparent' : 'var(--pc-bg)',
        // Themed (Markdown) panels propagate the canvas theme into the sandboxed
        // doc; everything else renders on its own (light) document.
        colorScheme: shape.props.themed ? (dark ? 'dark' : 'light') : undefined,
        pointerEvents: 'all',
      }}
      // Keep tldraw from hijacking drags/zoom meant for the iframe content.
      onPointerDown={(e) => e.stopPropagation()}
    />
  )
}

// --- React (user-authored React component, rendered natively) ---------------
// The native counterpart to Custom: instead of sandboxed HTML in an iframe, the
// panel hosts a user React component compiled at runtime from JSX source. It
// renders as an ordinary React subtree inside the Card, so it inherits the theme
// and selection chrome and can talk to the bridge directly (no postMessage
// hop). The heavy compile path lives in the lazily-loaded ReactHost chunk.
export class ReactShapeUtil extends PcShapeUtil {
  static type = 'pcReact'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    source: T.string, // JSX defining `function Component(...)`
    data: T.string, // JSON props from Python (update()/props=)
  }

  getDefaultProps() {
    return { w: 380, h: 320, label: 'react', source: '', data: '{}' }
  }

  component(shape) {
    return (
      <Card shape={shape} grab>
        {/* Header has no pointerEvents, so dragging it moves the panel. */}
        <CardLabel shape={shape} />
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
              }}
            >
              compiling…
            </div>
          }
        >
          <ReactHost shape={shape} />
        </Suspense>
      </Card>
    )
  }

}

// --- WebView (an external URL in a same-origin iframe) ----------------------
// Distinct from Custom: it loads a real URL via `src` (not srcDoc) and grants
// `allow-same-origin`, so embeds that need their own origin to run — YouTube's
// player, maps, web apps — work instead of rendering blank. Safe because the
// frame loads a third-party site at its own origin, not app-authored HTML, so
// same-origin gives it access to *itself*, not to this app.
export class WebViewShapeUtil extends PcShapeUtil {
  static type = 'pcWebView'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    url: T.string,
  }

  getDefaultProps() {
    return { w: 800, h: 600, label: 'web', url: '' }
  }

  component(shape) {
    return (
      <Card shape={shape} grab>
        {/* Header has no pointerEvents, so dragging it moves the panel. */}
        <CardLabel shape={shape} />
        <iframe
          title={shape.props.label}
          src={shape.props.url}
          sandbox="allow-scripts allow-same-origin allow-popups allow-forms allow-presentation"
          allow="fullscreen; autoplay; encrypted-media; picture-in-picture"
          // Match YouTube's own oembed iframe: VEVO/music videos check the
          // referrer and show "Video unavailable" if none is sent.
          referrerPolicy="strict-origin-when-cross-origin"
          style={{
            flex: 1,
            width: '100%',
            border: 'none',
            borderRadius: 4,
            // Frameless: don't paint behind the page while it loads (external
            // sites usually bring their own background anyway).
            background: shape.meta?.noFrame ? 'transparent' : 'var(--pc-bg)',
            pointerEvents: 'all',
          }}
          // Keep tldraw from hijacking drags/zoom meant for the iframe content.
          onPointerDown={(e) => e.stopPropagation()}
        />
      </Card>
    )
  }

}

// --- Button (a momentary action trigger) ------------------------------------
export class ButtonShapeUtil extends PcShapeUtil {
  static type = 'pcButton'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    text: T.string,
  }

  getDefaultProps() {
    return { w: 200, h: 84, label: 'button', text: 'Button' }
  }

  component(shape) {
    const { label, text } = shape.props
    const id = componentIdOf(shape.id)
    return (
      <Card shape={shape}>
        <CardLabel shape={shape} />
        <button
          style={{
            alignSelf: 'flex-start',
            padding: '8px 16px',
            border: 'none',
            borderRadius: 6,
            fontSize: 14,
            fontWeight: 600,
            cursor: 'pointer',
            background: 'var(--pc-accent)',
            color: 'var(--pc-accent-text)',
            pointerEvents: 'all',
          }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => sendInput(id, { click: true })}
        >
          {text}
        </button>
      </Card>
    )
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
        <CardLabel shape={shape} />
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
      <Card shape={shape} grab>
        <CardLabel shape={shape} />
        <LivePlotView shape={shape} />
      </Card>
    )
  }

}

// --- AudioFeed (streaming PCM played through the Web Audio API) -------------
// Python pushes base64 int16 PCM chunks over the live-data channel; we decode
// each into an AudioBuffer and schedule it back-to-back on the AudioContext
// clock so they play as one continuous stream. The browser autoplay policy
// blocks sound until a user gesture, so playback only starts once the listener
// clicks Enable (which creates/resumes the context).
function decodeChunk(ctx, data, sampleRate, channels) {
  let n = data.byteLength
  n -= n % 2 // int16 samples are 2 bytes; ignore a stray trailing byte
  // `data` is a fresh ArrayBuffer sliced at offset 0 (see handleBinary), so it's
  // 2-byte aligned and can back an Int16Array directly — no copy, no base64.
  const pcm = new Int16Array(data, 0, n / 2)
  const frames = Math.floor(pcm.length / channels)
  if (frames === 0) return null
  // Carry the source sample rate on the buffer; the context resamples on
  // playback if its own rate differs, so capture rate need not match hardware.
  const buf = ctx.createBuffer(channels, frames, sampleRate)
  for (let ch = 0; ch < channels; ch++) {
    const out = buf.getChannelData(ch)
    for (let i = 0; i < frames; i++) out[i] = pcm[i * channels + ch] / 32768
  }
  return buf
}

function AudioView({ shape }) {
  const id = componentIdOf(shape.id)
  const sampleRate = shape.props.sampleRate || 16000
  const channels = shape.props.channels || 1
  const [on, setOn] = useState(false)
  const ctxRef = useRef(null)
  const nextRef = useRef(0) // AudioContext-time the next chunk should start at
  // Read the latest on/off inside the (stable) live handler without
  // resubscribing each toggle.
  const onRef = useRef(false)
  useEffect(() => {
    onRef.current = on
  }, [on])

  useEffect(() => {
    // Small scheduling lead so chunks queue slightly ahead of the play head,
    // absorbing network jitter; if we ever fall behind, reprime from now.
    const LEAD = 0.12
    const play = (payload) => {
      const ctx = ctxRef.current
      if (!onRef.current || !ctx) return
      let buf
      try {
        buf = decodeChunk(ctx, payload, sampleRate, channels)
      } catch {
        return
      }
      if (!buf) return
      const src = ctx.createBufferSource()
      src.buffer = buf
      src.connect(ctx.destination)
      const now = ctx.currentTime
      let start = nextRef.current
      if (start < now + 0.01) start = now + LEAD
      src.start(start)
      nextRef.current = start + buf.duration
    }
    registerLive(id, play)
    return () => unregisterLive(id)
  }, [id, sampleRate, channels])

  // Stop the context when the panel goes away so it doesn't leak.
  useEffect(() => {
    return () => {
      const ctx = ctxRef.current
      if (ctx) ctx.close().catch(() => {})
      ctxRef.current = null
    }
  }, [])

  const toggle = async () => {
    if (!on) {
      let ctx = ctxRef.current
      if (!ctx) {
        const AC = window.AudioContext || window.webkitAudioContext
        ctx = new AC({ sampleRate })
        ctxRef.current = ctx
      }
      try {
        await ctx.resume()
      } catch {
        // ignore — resume can reject if no gesture, but this is one
      }
      nextRef.current = ctx.currentTime + 0.12
      setOn(true)
    } else {
      setOn(false)
      const ctx = ctxRef.current
      if (ctx) ctx.suspend().catch(() => {})
    }
  }

  return (
    <div
      style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 8 }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <button
        onClick={toggle}
        style={{
          alignSelf: 'flex-start',
          padding: '6px 12px',
          border: 'none',
          borderRadius: 6,
          fontSize: 14,
          fontWeight: 600,
          cursor: 'pointer',
          background: on ? 'var(--pc-accent)' : 'var(--pc-off-bg)',
          color: on ? 'var(--pc-accent-text)' : 'var(--pc-off-text)',
          pointerEvents: 'all',
        }}
      >
        {on ? '🔊 Audio on' : '🔈 Enable audio'}
      </button>
      <div style={{ fontSize: 12, color: 'var(--pc-muted)' }}>
        {sampleRate} Hz · {channels === 1 ? 'mono' : `${channels} ch`}
      </div>
    </div>
  )
}

export class AudioShapeUtil extends PcShapeUtil {
  static type = 'pcAudio'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
    sampleRate: T.number,
    channels: T.number,
  }

  getDefaultProps() {
    return { w: 260, h: 120, label: 'audio', sampleRate: 16000, channels: 1 }
  }

  component(shape) {
    return (
      <Card shape={shape}>
        <CardLabel shape={shape} />
        <AudioView shape={shape} />
      </Card>
    )
  }

}

// --- Chat (shared room for everyone viewing the canvas) ---------------------
// Chat isn't Python state: the server relays lines between viewers and stamps
// each with the sender's identity. This panel is a window onto that room, so it
// subscribes to the bridge's global chat/identity channels rather than to
// component updates. Every Chat panel shows the same conversation.
function ChatView({ shape }) {
  const [me, setMe] = useState(null)
  const [messages, setMessages] = useState(() => [...getChatLog()])
  const [draft, setDraft] = useState('')
  const [nameDraft, setNameDraft] = useState('')
  const [editingName, setEditingName] = useState(false)
  const listRef = useRef(null)

  useEffect(() => subscribeIdentity(setMe), [])

  // Backfill from the retained log, then append each new line (deduped by id so
  // the snapshot/subscribe gap can't double-post).
  useEffect(() => {
    setMessages([...getChatLog()])
    return subscribeChat((entry) =>
      setMessages((m) => (m.some((x) => x.msgId === entry.msgId) ? m : [...m, entry]))
    )
  }, [])

  // While not actively editing, keep the name field showing our current name.
  useEffect(() => {
    if (me && !editingName) setNameDraft(me.name)
  }, [me, editingName])

  // Keep the newest message in view.
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  const send = () => {
    const t = draft.trim()
    if (!t) return
    sendChat(t)
    setDraft('')
  }

  const commitName = () => {
    setEditingName(false)
    const n = nameDraft.trim()
    if (n && me && n !== me.name) setMyName(n)
    else if (me) setNameDraft(me.name)
  }

  const fieldStyle = {
    fontSize: 13,
    padding: '5px 8px',
    border: '1px solid var(--pc-border-mid)',
    borderRadius: 6,
    background: 'var(--pc-input-bg)',
    color: 'var(--pc-text)',
    pointerEvents: 'all',
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, gap: 6 }}>
      <div
        ref={listRef}
        style={{ flex: 1, minHeight: 0, overflow: 'auto', pointerEvents: 'all' }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        {messages.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--pc-faint2)', fontStyle: 'italic', padding: 4 }}>
            no messages yet — say hello
          </div>
        ) : (
          messages.map((m) => (
            <div key={m.msgId} style={{ fontSize: 13, lineHeight: 1.4, marginBottom: 3, wordBreak: 'break-word' }}>
              <span style={{ fontWeight: 700, color: m.color || 'var(--pc-text)' }}>
                {m.name}
                {me && m.id === me.id ? ' (you)' : ''}:
              </span>{' '}
              <span style={{ color: 'var(--pc-text)' }}>{m.text}</span>
            </div>
          ))
        )}
      </div>

      <div
        style={{ display: 'flex', gap: 6, alignItems: 'center' }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <span style={{ fontSize: 11, color: 'var(--pc-muted)' }}>name</span>
        <input
          value={nameDraft}
          onChange={(e) => setNameDraft(e.target.value)}
          onFocus={() => setEditingName(true)}
          onBlur={commitName}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.currentTarget.blur()
          }}
          maxLength={24}
          style={{ ...fieldStyle, flex: 1, minWidth: 0 }}
          title="your display name — edit and press Enter"
        />
      </div>

      <div
        style={{ display: 'flex', gap: 6 }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') send()
          }}
          placeholder="message…"
          style={{ ...fieldStyle, flex: 1, minWidth: 0 }}
        />
        <button
          onClick={send}
          style={{
            padding: '5px 12px',
            border: 'none',
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 600,
            cursor: 'pointer',
            background: 'var(--pc-accent)',
            color: 'var(--pc-accent-text)',
            pointerEvents: 'all',
          }}
        >
          Send
        </button>
      </div>
    </div>
  )
}

export class ChatShapeUtil extends PcShapeUtil {
  static type = 'pcChat'
  static props = {
    w: T.number,
    h: T.number,
    label: T.string,
  }

  getDefaultProps() {
    return { w: 320, h: 400, label: 'chat' }
  }

  component(shape) {
    return (
      <Card shape={shape} grab>
        <CardLabel shape={shape} />
        <ChatView shape={shape} />
      </Card>
    )
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
      <Card shape={shape} grab>
        <CardLabel shape={shape} />
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

}

// --- Inspector (live table of canvas components or kernel globals) ----------
const INSPECTOR_COLS = ['name', 'type', 'value', 'x', 'y', 'w', 'h']

// A live readout of the current viewport: the canvas point at screen centre
// and the zoom level — exactly the x/y/zoom that serve(view=...) / set_view()
// take, so a user can pan/zoom to a framing they like and copy the numbers to
// pin it as a fixed view. useValue tracks the camera, so it updates live.
function ViewReadout() {
  const editor = useEditor()
  const view = useValue(
    'pc-view-readout',
    () => {
      const c = editor.getViewportPageBounds().center
      return { x: Math.round(c.x), y: Math.round(c.y), zoom: editor.getZoomLevel() }
    },
    [editor]
  )
  return (
    <div
      style={{
        marginTop: 6,
        fontSize: 11,
        fontFamily: 'ui-monospace, monospace',
        color: 'var(--pc-muted)',
        userSelect: 'text',
        WebkitUserSelect: 'text',
        cursor: 'text',
        pointerEvents: 'all',
      }}
      onPointerDown={(e) => e.stopPropagation()}
      title="current viewport — pass these to serve(view=...) or canvas.set_view() to fix this view"
    >
      view: x={view.x} y={view.y} zoom={view.zoom.toFixed(2)}
    </div>
  )
}

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
      <ViewReadout />
    </>
  )
}

// Drill-down: an object's type/repr header plus a field/type/value table.
function DetailView({ selected, detail, onBack, onRefresh, controlStyle }) {
  // Field-level search + type filter, mirroring the table view. Both filter the
  // already-sent fields client-side, so typing stays instant and live updates
  // keep flowing underneath the filter.
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  // Reset the filters whenever we drill into a different object -- its field
  // names and the set of types are unrelated to the previous one's.
  useEffect(() => {
    setQuery('')
    setTypeFilter('all')
  }, [selected])

  const allFields = detail && Array.isArray(detail.fields) ? detail.fields : []
  const types = ['all', ...Array.from(new Set(allFields.map((f) => f.type))).sort()]
  // tldraw sets `user-select: none` on shape bodies (so dragging never starts a
  // text selection). Re-enable selection on the read-only detail cells/repr so
  // their field names and values can be highlighted and copied.
  const selectable = { userSelect: 'text', WebkitUserSelect: 'text', cursor: 'text' }
  const q = query.toLowerCase()
  const fields = allFields.filter(
    (f) =>
      (typeFilter === 'all' || f.type === typeFilter) &&
      (!q || String(f.field ?? '').toLowerCase().includes(q))
  )
  const filtered = q !== '' || typeFilter !== 'all'
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
      {detail && !detail.missing && allFields.length > 0 && (
        <div
          style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center' }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <input
            placeholder="search field…"
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
        </div>
      )}
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
                ...selectable,
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
                      {filtered ? 'no matching fields' : 'no fields — see repr above'}
                    </td>
                  </tr>
                ) : (
                  fields.map((f, i) => (
                    <tr key={i}>
                      <td style={{ padding: '2px 6px', borderBottom: '1px solid var(--pc-border-soft)', ...selectable }}>
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
                          ...selectable,
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
      <Card shape={shape} grab>
        <CardLabel shape={shape} />
        <InspectorView shape={shape} />
      </Card>
    )
  }

}

// Map of the `component` string sent by Python -> tldraw shape type.
export const COMPONENT_TO_SHAPE = {
  Slider: 'pcSlider',
  Label: 'pcLabel',
  VideoFeed: 'pcVideo',
  AudioFeed: 'pcAudio',
  Chat: 'pcChat',
  Custom: 'pcHtml',
  React: 'pcReact',
  WebView: 'pcWebView',
  Toggle: 'pcToggle',
  Button: 'pcButton',
  LivePlot: 'pcLivePlot',
  Repl: 'pcRepl',
  Inspector: 'pcInspector',
}

export const shapeUtils = [
  SliderShapeUtil,
  LabelShapeUtil,
  VideoShapeUtil,
  AudioShapeUtil,
  ChatShapeUtil,
  HtmlShapeUtil,
  ReactShapeUtil,
  WebViewShapeUtil,
  ToggleShapeUtil,
  ButtonShapeUtil,
  LivePlotShapeUtil,
  ReplShapeUtil,
  InspectorShapeUtil,
]
