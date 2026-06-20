import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { BaseBoxShapeUtil, HTMLContainer, T, useEditor, useValue } from 'tldraw'
import Plotly from 'plotly.js-basic-dist-min'
import {
  sendInput,
  componentIdOf,
  registerLive,
  unregisterLive,
  requestCompletions,
} from './bridge'

// Monaco is heavy, so the Repl editor is code-split into its own chunk that
// only loads when a Repl panel is shown (see MonacoRepl.jsx).
const MonacoRepl = lazy(() => import('./MonacoRepl'))

// The React-panel host bundles a JSX compiler (Sucrase, ~200 kB), so it too is
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
// A small grip that gives a guaranteed drag/select point for a body-interactive
// panel (React) without covering its body. It carries no pointer handler and
// never stopPropagation, so a press/drag bubbles to tldraw, which selects and
// (on drag) moves the topmost panel — exactly like the `grab` cover, but
// confined to its own corner so the rest of the body stays live (hover, cursor,
// controls work from the first pointer-over). Hidden until the panel is hovered
// (see .pc-drag-handle in theme.css). Sits in the top-right so it clears the
// header label in the top-left.
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

// ``handle``: like ``grab`` but instead of a full-body cover lay only a small
// hover-revealed grip (DragHandle). Use it for body-interactive native panels
// whose content should stay live from the first pointer-over (no click-to-arm
// cover) — currently the React host. The cover and the handle are mutually
// exclusive; pick one per panel.
function Card({ shape, children, grab = false, ghostable = false, handle = false }) {
  const editor = useEditor()
  const fullyLocked = shape.isLocked
  const blockInput = fullyLocked || shape.meta?.lockInput
  const noGrab = !!shape.meta?.noGrab
  // A panel that can neither be selected (noGrab) nor operated (lockInput, from
  // operable=False) is purely decorative: it should be click-through so the
  // pointer reaches whatever sits underneath it on the canvas. A full lock
  // (isLocked) is excluded — that deliberately swallows the event instead. Only
  // `ghostable` panels qualify: those whose content we also make pointer-inert
  // (the Custom iframe). For everything else the input overlay must keep
  // catching the pointer, or operable=False would stop blocking its controls.
  const ghost = ghostable && noGrab && !!shape.meta?.lockInput && !fullyLocked
  const selected = useValue(
    'pc-selected',
    () => editor.getSelectedShapeIds().includes(shape.id),
    [editor, shape.id]
  )
  return (
    // `pointer-events: all` overrides tl-html-container's CSS `pointer-events:
    // none`, so empty card space (no interactive child at that coordinate) is
    // captured by the card rather than falling through to an interactive element
    // in a panel stacked below. The event is not stopped, so it still bubbles to
    // tldraw for correct shape selection. Ghost panels opt out (they are purely
    // decorative and intentionally click-through).
    <HTMLContainer className="pc-card" style={ghost ? cardStyle(shape) : { ...cardStyle(shape), pointerEvents: 'all' }}>
      {children}
      {/* A persistent grip (stays up while selected, unlike the grab cover) so a
          body-interactive panel always has a drag/select point even when its
          content claims every pointer. Suppressed when the panel can't be
          moved/selected anyway (noGrab) or its input is locked. */}
      {handle && !noGrab && !blockInput && <DragHandle />}
      {grab && !noGrab && !selected && !blockInput && (
        <div
          // No handler / no stopPropagation: the event bubbles to tldraw, which
          // selects + (on drag) moves the topmost panel — fixing overlap grabs.
          style={{ position: 'absolute', inset: 0, pointerEvents: 'all', cursor: 'grab' }}
        />
      )}
      {blockInput && (
        <div
          // A ghost (decorative) panel takes no pointer at all, so clicks fall
          // through to panels beneath it. Otherwise the overlay catches the
          // pointer: full lock swallows it (stopPropagation); input-only lock
          // lets it bubble so tldraw's move/select/resize still obey the panel's
          // own permissions.
          style={{ position: 'absolute', inset: 0, pointerEvents: ghost ? 'none' : 'all', cursor: 'default' }}
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
    // Semicolon-separated Permissions Policy features for the iframe
    // (e.g. "camera; microphone"). Empty string = no extra permissions.
    permissions: T.string,
  }

  getDefaultProps() {
    return { w: 380, h: 320, label: 'custom', html: '', themed: false, permissions: '' }
  }

  component(shape) {
    return (
      // ghostable: grabbable=False + operable=False makes the iframe (and the
      // input overlay) click-through, so the panel is purely decorative.
      <Card shape={shape} grab ghostable>
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
  // Decorative panels (grabbable=False + operable=False) are click-through: the
  // iframe takes no pointer, so clicks pass to whatever sits underneath on the
  // canvas. See Card's `ghost`.
  const ghost = !!shape.meta?.noGrab && !!shape.meta?.lockInput && !shape.isLocked

  useEffect(() => {
    const post = (data) => {
      const el = ref.current
      if (!el || !el.contentWindow) return
      // A binary push (push_binary) arrives as an ArrayBuffer; transfer it into
      // the iframe (zero-copy) rather than structured-cloning the whole buffer
      // on every frame — the win that keeps binary Custom panels close to the
      // native VideoFeed path. handleBinary slices a fresh buffer per frame and
      // nothing reads it after this single handler, so detaching it is safe. A
      // JSON push() is a plain value with nothing transferable.
      const transfer = data instanceof ArrayBuffer ? [data]
        : ArrayBuffer.isView(data) ? [data.buffer] : []
      el.contentWindow.postMessage({ __pycanvas: data }, '*', transfer)
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
      // allow-popups-to-escape-sandbox lets a link (target=_blank) open as a
      // normal browser tab instead of a sandboxed frame that many sites refuse.
      sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms"
      allow={shape.props.permissions || undefined}
      style={{
        flex: 1,
        // An iframe's intrinsic height is 150px and a flex item defaults to
        // min-height:auto, so without this the frame refuses to shrink below
        // 150px in a shorter panel — it overflows and the card clips its bottom
        // (e.g. a centered image looks top-padded). minHeight:0 lets flex:1 fit
        // the frame to the card body.
        minHeight: 0,
        width: '100%',
        border: 'none',
        borderRadius: 4,
        // A frameless panel keeps the iframe transparent too, so user HTML
        // with a transparent body floats directly on the canvas.
        background: shape.meta?.noFrame ? 'transparent' : 'var(--pc-bg)',
        // Themed (Markdown) panels propagate the canvas theme into the sandboxed
        // doc; everything else renders on its own (light) document.
        colorScheme: shape.props.themed ? (dark ? 'dark' : 'light') : undefined,
        pointerEvents: ghost ? 'none' : 'all',
      }}
      // Keep tldraw from hijacking drags/zoom meant for the iframe content. A
      // ghost panel wants the opposite — let the pointer fall through entirely.
      onPointerDown={ghost ? undefined : (e) => e.stopPropagation()}
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
    css: T.string, // optional stylesheet from Python `css=` (source= panels)
    autoH: T.boolean, // h="auto": fit the panel height to the rendered content
    autoW: T.boolean, // w="auto": fit the panel width to the rendered content
    libs: T.string, // JSON array of library names to load (Python `scope=[...]`)
  }

  getDefaultProps() {
    return { w: 380, h: 320, label: 'react', source: '', data: '{}', css: '', autoH: false, autoW: false, libs: '[]' }
  }

  component(shape) {
    return (
      // handle (not grab): a hover-revealed grip moves/selects the panel, so the
      // hosted component stays interactive from the first pointer-over instead of
      // sitting under a click-to-arm cover. ghostable: grabbable=False +
      // operable=False makes the host (and the input overlay) click-through, so
      // the panel is purely decorative.
      <Card shape={shape} handle ghostable>
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

// --- LivePlot (streaming Plotly, no iframe reload) --------------------------
// Plotly is loaded once with the app bundle; data arrives over the bridge's
// live-data channel and is applied with Plotly.react (an efficient diff).
function LivePlotView({ shape }) {
  const ref = useRef(null)
  const id = componentIdOf(shape.id)

  useEffect(() => {
    const node = ref.current
    // Render on the browser's animation clock, not on every message. The bridge
    // can hand us points faster than Plotly can repaint; calling Plotly inline
    // per message saturates the main thread, which then can't drain the socket,
    // so *everything* on it (labels, slider echo) backs up behind plot redraws.
    // Instead we just stash incoming points (cheap, so messages keep draining)
    // and flush at most once per frame, coalescing whatever arrived in between —
    // the render rate self-limits to what the device can actually do.
    let pendingFull = null // newest full figure (supersedes pending points)
    let pendingExt = null // accumulated extend delta {indices, x, y, max}
    let raf = null

    const mergeExt = (acc, e) => {
      if (!acc) {
        return {
          indices: e.indices.slice(),
          x: e.x.map((a) => a.slice()),
          y: e.y.map((a) => a.slice()),
          max: e.max,
        }
      }
      const pos = new Map(acc.indices.map((ti, k) => [ti, k]))
      e.indices.forEach((ti, j) => {
        if (pos.has(ti)) {
          const k = pos.get(ti)
          acc.x[k] = acc.x[k].concat(e.x[j])
          acc.y[k] = acc.y[k].concat(e.y[j])
        } else {
          pos.set(ti, acc.indices.length)
          acc.indices.push(ti)
          acc.x.push(e.x[j].slice())
          acc.y.push(e.y[j].slice())
        }
      })
      acc.max = e.max
      return acc
    }

    const flush = () => {
      raf = null
      if (!node) return
      if (pendingFull) {
        const p = pendingFull
        pendingFull = null
        pendingExt = null // the full figure already carries everything
        // Hand Plotly its own array copies so a later extendTraces mutating the
        // node can't alias — and double-append to — the bridge's buffer.
        Plotly.react(
          node,
          (p.data || []).map((t) => ({
            ...t,
            x: [...(t.x || [])],
            y: [...(t.y || [])],
          })),
          p.layout || {},
          { responsive: true, displayModeBar: false },
        )
        adapt()
        return
      }
      if (pendingExt) {
        const e = pendingExt
        pendingExt = null
        Plotly.extendTraces(node, { x: e.x, y: e.y }, e.indices, e.max)
      }
    }

    const render = (plot) => {
      if (!node) return
      if (plot && plot.__extend) pendingExt = mergeExt(pendingExt, plot.__extend)
      else {
        pendingFull = plot // full figure supersedes any pending points
        pendingExt = null
      }
      if (raf == null) raf = requestAnimationFrame(flush)
    }
    // Adapt Plotly's fixed-pixel chrome (margins / legend / font) to the panel's
    // real size, so a small panel shows a clean plot instead of being swamped by
    // furniture. Runs after a full (re)render and on every container resize; the
    // normal-sized case keeps the usual layout, so only genuinely small panels
    // (e.g. squeezed onto a phone) drop the legend and tighten margins.
    const adapt = () => {
      if (!node) return
      const w = node.clientWidth || 0
      const h = node.clientHeight || 0
      if (!w || !h) return
      const small = w < 340 || h < 200
      Plotly.relayout(node, {
        margin: small
          ? { l: 30, r: 10, t: 10, b: 24 }
          : { l: 40, r: 15, t: 15, b: 30 },
        showlegend: !small,
        'font.size': small ? 9 : 12,
      }).catch(() => {})
    }

    registerLive(id, render)

    // Keep Plotly synced to its container in *all* cases. Plotly's own
    // responsive: only watches the window, so a container that resizes from a
    // layout/fit/zoom-independent reflow — common on mobile — would otherwise
    // leave the chart drawn at a stale size and looking scaled wrong. A
    // ResizeObserver (debounced to one frame) catches every container size
    // change, including the initial settle and Python-driven resizes.
    let resizeRaf = null
    let ro = null
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => {
        if (resizeRaf != null) return
        resizeRaf = requestAnimationFrame(() => {
          resizeRaf = null
          if (!node) return
          Plotly.Plots.resize(node)
          adapt()
        })
      })
      ro.observe(node)
    }

    return () => {
      unregisterLive(id)
      if (raf != null) cancelAnimationFrame(raf)
      if (resizeRaf != null) cancelAnimationFrame(resizeRaf)
      if (ro) ro.disconnect()
      if (node) Plotly.purge(node)
    }
  }, [id])

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

// Map of the `component` string sent by Python -> tldraw shape type.
export const COMPONENT_TO_SHAPE = {
  Label: 'pcLabel',
  AudioFeed: 'pcAudio',
  Custom: 'pcHtml',
  React: 'pcReact',
  LivePlot: 'pcLivePlot',
  Repl: 'pcRepl',
}

export const shapeUtils = [
  LabelShapeUtil,
  AudioShapeUtil,
  HtmlShapeUtil,
  ReactShapeUtil,
  LivePlotShapeUtil,
  ReplShapeUtil,
]
