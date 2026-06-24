import { lazy, Suspense, useEffect, useRef } from 'react'
import { BaseBoxShapeUtil, HTMLContainer, T, useEditor, useValue } from 'tldraw'

// Returns true when the topmost tldraw shape at the pointer position is a
// non-panel shape (a drawing, arrow, text, image, etc. drawn on the canvas).
// Used to let pointerdown events bubble to tldraw in that case so the user
// can select/drag drawings that overlap a panel, instead of the panel eating
// the event via stopPropagation.
function drawingOnTop(editor, e) {
  const pt = editor.screenToPage({ x: e.clientX, y: e.clientY })
  const top = editor.getShapeAtPoint(pt, { hitInside: true })
  return top != null && !top.type.startsWith('pc')
}
import {
  sendPanelError,
  componentIdOf,
  registerLive,
  unregisterLive,
} from './bridge'

// The React-panel host bundles a JSX compiler (Sucrase, ~200 kB), so it too is
// code-split and only loaded the first time a React panel appears.
const ReactHost = lazy(() => import('./ReactHost'))

// Derive tinted frame CSS variables from an accent hex color so the card
// background, border, and shadow follow the component's color theme.
// isDark mirrors tldraw's dark-mode toggle so light-canvas panels get pale
// tints instead of the dark ones used on a dark canvas.
function deriveFrameVars(fc, isDark) {
  const r = parseInt(fc.slice(1, 3), 16)
  const g = parseInt(fc.slice(3, 5), 16)
  const b = parseInt(fc.slice(5, 7), 16)
  const rn = r / 255, gn = g / 255, bn = b / 255
  const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn), d = max - min
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
  function hsl(h, s, l) {
    const a = s * Math.min(l, 1 - l)
    const k = (n) => { const kv = (n + h / 30) % 12; return l - a * Math.max(-1, Math.min(kv - 3, 9 - kv, 1)) }
    return `rgb(${Math.round(k(0) * 255)},${Math.round(k(8) * 255)},${Math.round(k(4) * 255)})`
  }
  return isDark ? {
    '--pc-bg':     hsl(h, Math.min(s, 0.60), 0.15),
    '--pc-border': hsl(h, Math.min(s, 0.70), 0.27),
    '--pc-shadow': `rgba(${r},${g},${b},0.25)`,
  } : {
    '--pc-bg':     hsl(h, Math.min(s, 0.40), 0.94),
    '--pc-border': hsl(h, Math.min(s, 0.50), 0.78),
    '--pc-shadow': `rgba(${r},${g},${b},0.10)`,
  }
}

// Return a readable label color for fc on a light or dark card background.
function frameLabelColor(fc, isDark) {
  if (isDark) return fc  // accent is typically vivid enough on dark
  // Darken the accent so it reads on a pale tinted background.
  const r = parseInt(fc.slice(1, 3), 16)
  const g = parseInt(fc.slice(3, 5), 16)
  const b = parseInt(fc.slice(5, 7), 16)
  const rn = r / 255, gn = g / 255, bn = b / 255
  const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn), d = max - min
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
  const a = s * Math.min(0.38, 1 - 0.38)
  const k = (n) => { const kv = (n + h / 30) % 12; return 0.38 - a * Math.max(-1, Math.min(kv - 3, 9 - kv, 1)) }
  return `rgb(${Math.round(k(0) * 255)},${Math.round(k(8) * 255)},${Math.round(k(4) * 255)})`
}

// Shared card styling for all danvas component shapes. The card is pinned to
// the shape's exact w/h (not 100% of an ancestor) so it tracks resizing
// continuously and lines up with tldraw's selection box. A frameless panel
// (meta.noFrame, Python `frame=False`) keeps the same flex box but drops every
// visible piece of chrome — background, border, shadow, padding — so its
// content appears to sit directly on the canvas.
function cardStyle(shape, isDark = false) {
  const noFrame = !!shape.meta?.noFrame
  const fc = shape.meta?.frameColor
  const frameVars = (!noFrame && fc) ? deriveFrameVars(fc, isDark) : {}
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
    ...frameVars,
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
  const isDark = useValue('pc-dark', () => editor.user.getIsDarkMode(), [editor])
  // When the user has a drawing/text/arrow/etc. tool active, panels must be
  // pointer-transparent so tldraw receives the strokes — not the panel HTML.
  const toolIsSelect = useValue('pc-tool', () => editor.getCurrentToolId() === 'select', [editor])
  // When a non-panel tldraw shape (drawing, arrow, text…) is hovered, the panel
  // must also be fully pointer-transparent so tldraw can select/drag it. tldraw's
  // own hover detection already identifies the correct shape; we piggyback on
  // that instead of re-implementing hit-testing. Bubbling events from inside
  // HTMLContainers don't trigger tldraw's non-panel shape selection path, so
  // simply not-stopping-propagation isn't enough — we need the panel out of the
  // pointer event path entirely.
  const nonPanelHovered = useValue('pc-hover', () => {
    const hid = editor.getHoveredShapeId()
    if (!hid) return false
    const s = editor.getShape(hid)
    return !!s && !s.type.startsWith('pc')
  }, [editor])
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
    <HTMLContainer
      // pc-draw-passthrough forces pointer-events:none on this element and all
      // descendants (including SVG children that ignore normal CSS cascade) so
      // tldraw can fully own the pointer when a drawing/arrow/text shape is hovered.
      className={nonPanelHovered ? 'pc-card pc-draw-passthrough' : 'pc-card'}
      style={ghost || !toolIsSelect || nonPanelHovered ? cardStyle(shape, isDark) : { ...cardStyle(shape, isDark), pointerEvents: 'all' }}
    >
      {children}
      {/* A persistent grip (stays up while selected, unlike the grab cover) so a
          body-interactive panel always has a drag/select point even when its
          content claims every pointer. Suppressed when the panel can't be
          moved/selected anyway (noGrab) or its input is locked. */}
      {toolIsSelect && handle && !noGrab && !blockInput && <DragHandle />}
      {toolIsSelect && grab && !noGrab && !selected && !blockInput && (
        <div
          // No handler / no stopPropagation: the event bubbles to tldraw, which
          // selects + (on drag) moves the topmost panel — fixing overlap grabs.
          style={{ position: 'absolute', inset: 0, pointerEvents: 'all', cursor: 'grab' }}
        />
      )}
      {toolIsSelect && blockInput && (
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
  const editor = useEditor()
  const isDark = useValue('pc-dark', () => editor.user.getIsDarkMode(), [editor])
  if (shape.meta?.noFrame) return null
  const fc = shape.meta?.frameColor
  const style = fc ? { ...labelStyle, color: frameLabelColor(fc, isDark) } : labelStyle
  return <div style={style}>{shape.props.label}</div>
}

// Shared base for every danvas panel. It reads two per-shape flags from the
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
function LabelPanel({ shape }) {
  return (
    <Card shape={shape}>
      <CardLabel shape={shape} />
      <div style={{ fontSize: 20, fontWeight: 600, color: 'var(--pc-text)' }}>{shape.props.value}</div>
    </Card>
  )
}

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

  component(shape) { return <LabelPanel shape={shape} /> }
}

// --- Custom (arbitrary HTML in a sandboxed iframe) --------------------------
function HtmlPanel({ shape }) {
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

  component(shape) { return <HtmlPanel shape={shape} /> }
}

// The sandboxed iframe that hosts a Custom panel's HTML. Beyond rendering the
// HTML, it subscribes to the bridge's live-data channel: Python ``push()`` calls
// arrive here and are forwarded into the iframe via postMessage (as a `message`
// event whose `data.__danvas` is the payload), so live data can stream in
// *without* replacing srcDoc and reloading the frame. That keeps the iframe's
// focus and listeners intact — essential for streaming + interactive panels.
function CustomView({ shape }) {
  const ref = useRef(null)
  const id = componentIdOf(shape.id)
  const editor = useEditor()
  const toolIsSelect = useValue('pc-tool', () => editor.getCurrentToolId() === 'select', [editor])
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
      el.contentWindow.postMessage({ __danvas: data }, '*', transfer)
    }
    registerLive(id, post)
    return () => unregisterLive(id)
  }, [id])

  useEffect(() => {
    const onMessage = (e) => {
      if (!e.data) return
      const err = e.data.__danvas_error
      if (err && err.id === id) sendPanelError(id, err.msg)
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
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
        pointerEvents: (ghost || !toolIsSelect) ? 'none' : 'all',
      }}
      // Keep tldraw from hijacking drags/zoom meant for the iframe content. A
      // ghost panel or active drawing tool wants the pointer to fall through.
      onPointerDown={(ghost || !toolIsSelect) ? undefined : (e) => { if (!drawingOnTop(editor, e)) e.stopPropagation() }}
      onDragStart={(e) => e.preventDefault()}
    />
  )
}

// --- React (user-authored React component, rendered natively) ---------------
function ReactPanel({ shape }) {
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
            data-pc-compiling=""
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
    wasm: T.string, // base64-encoded .wasm binary (Python `wasm=` / `wasm_path=`)
  }

  getDefaultProps() {
    return { w: 380, h: 320, label: 'react', source: '', data: '{}', css: '', autoH: false, autoW: false, libs: '[]', wasm: '' }
  }

  component(shape) { return <ReactPanel shape={shape} /> }
}

// Map of the `component` string sent by Python -> tldraw shape type.
// AudioFeed, LivePlot, Plot, and Histogram now all inherit from React and
// register as 'React' component type, so they use pcReact and ReactShapeUtil.
export const COMPONENT_TO_SHAPE = {
  Label: 'pcLabel',
  Custom: 'pcHtml',
  React: 'pcReact',
}

export const shapeUtils = [
  LabelShapeUtil,
  HtmlShapeUtil,
  ReactShapeUtil,
]