import { useEffect, useReducer, useState } from 'react'
import { Tldraw, createShapeId, useEditor, useValue } from 'tldraw'
import 'tldraw/tldraw.css'
import './theme.css' // danvas panel theme vars (after tldraw.css so they win)
import { shapeUtils } from './canvas'
import { setEditor, subscribePresence, subscribeUiInspector, toggleUiInspector, subscribeViewConfig, subscribePeerCursors, getEditor, subscribeAuth, signOut, subscribeGraveyard, toggleGraveyard, sendRestore } from './bridge'

export default function App() {
  // `hideUi` is a <Tldraw> prop (decided before/at render), unlike the camera
  // settings which are applied to the editor in bridge.js. Default to showing
  // the UI until/unless the server's view config asks to hide it.
  const [hideUi, setHideUi] = useState(false)
  useEffect(
    () => subscribeViewConfig((view) => setHideUi(view?.ui === false)),
    []
  )
  return (
    <div style={{ position: 'fixed', inset: 0 }}>
      <PresenceBadge />
      <CursorLayer />
      {/* Sign-out is an auth escape hatch, not app chrome, so it shows whenever
          the canvas is password-protected — even under a `ui: false` kiosk view —
          so any viewer can switch accounts. */}
      <SignOutButton />
      {/* The Inspector and Graveyard buttons are part of the app's UI chrome, so
          a `ui: false` view (chrome-free surface) hides them alongside tldraw's
          own toolbars. */}
      {!hideUi && <InspectorButton />}
      {!hideUi && <GraveyardButton />}
      <Tldraw
        hideUi={hideUi}
        shapeUtils={shapeUtils}
        onMount={(editor) => {
          // ?demo seeds sample shapes so the frontend can be checked
          // standalone (vite dev) without a running Python backend.
          if (location.search.includes('demo')) {
            seedDemo(editor)
          }
          // Default to dark mode on first load, but only once: tldraw persists
          // the color scheme, so after the first visit we leave it alone and the
          // user's menu choice (Preferences -> Dark mode) sticks across reloads.
          try {
            if (!localStorage.getItem('pc-theme-init')) {
              editor.user.updateUserPreferences({ colorScheme: 'dark' })
              localStorage.setItem('pc-theme-init', '1')
            }
          } catch {
            editor.user.updateUserPreferences({ colorScheme: 'dark' })
          }
          // Camera controls: mouse-wheel zooms, trackpad two-finger scroll pans,
          // trackpad pinch zooms, right-click-drag pans. enableSmartScroll owns
          // every wheel gesture in a capture-phase listener so it behaves the same
          // over the empty canvas and over interactive panels (which scroll via
          // their scrollbar, not the wheel).
          const cleanupPan = enableRightDragPan(editor)
          const cleanupScroll = enableSmartScroll(editor)
          setEditor(editor)
          return () => { cleanupPan(); cleanupScroll() }
        }}
      >
        {hideUi && <KioskHandTool />}
      </Tldraw>
    </div>
  )
}

// Under a `ui: false` view there's no toolbar, so a touch user is stuck on the
// select tool — one finger selects/drags the topmost panel instead of panning
// the canvas. This keeps the hand tool armed on touch devices so a single finger
// pans (tap and pinch-zoom still work). Rendered as a <Tldraw> child for editor
// context; it watches the tool reactively and re-applies hand whenever tldraw
// bounces it back to select (which it does during init and on cancel). It uses a
// useValue read + an effect — the same pattern the panels use — not a raw
// reactor (a raw `react()` here crashed tldraw's effect scheduler). Touch is
// `(pointer: coarse)`: true only when the *primary* input is touch (a phone), so
// a mouse-driven touchscreen laptop (maxTouchPoints>0 but a fine pointer) keeps
// the normal select tool, and desktop Chrome (`'ontouchstart' in window`) isn't
// wrongly treated as touch.
function KioskHandTool() {
  const editor = useEditor()
  const toolId = useValue('kiosk-tool', () => editor.getCurrentToolId(), [editor])
  useEffect(() => {
    const isPhone = !!window.matchMedia?.('(pointer: coarse)').matches
    if (isPhone && toolId !== 'hand') editor.setCurrentTool('hand')
  }, [editor, toolId])
  return null
}

// A small live-viewer badge floated over the canvas. Subscribes to the bridge's
// presence count (broadcast by the server on every join/leave). pointerEvents is
// off so it never intercepts canvas gestures, and it sits top-center to clear
// tldraw's own menus (top-left) and the selection style panel (top-right).
//
// Colors are self-contained (a dark translucent pill with light text) rather
// than the --pc-* theme vars: those are scoped to tldraw's `.tl-container`, and
// this badge renders as a sibling *outside* it, so the vars wouldn't resolve.
// The chosen palette reads clearly over both the light and dark canvas.
function PresenceBadge() {
  const [count, setCount] = useState(0)
  useEffect(() => subscribePresence(setCount), [])
  if (count < 1) return null // nothing meaningful to show before the socket reports
  return (
    <div
      style={{
        position: 'absolute',
        top: 8,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 300,
        pointerEvents: 'none',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 10px',
        borderRadius: 999,
        background: 'rgba(20, 20, 22, 0.82)',
        color: '#22c55e',
        border: '1px solid rgba(255, 255, 255, 0.15)',
        boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)',
        fontFamily: 'system-ui, sans-serif',
        fontSize: 12,
        fontWeight: 600,
        userSelect: 'none',
      }}
      title="people connected to this canvas"
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: '#22c55e',
          boxShadow: '0 0 0 2px rgba(34, 197, 94, 0.3)',
        }}
      />
      {count} {count === 1 ? 'viewer' : 'viewers'}
    </div>
  )
}

// Renders other viewers' live cursors. Peer positions arrive (in page coords)
// over the bridge; we map each to screen coords via the editor and draw a small
// coloured pointer + name. A rAF loop re-renders while any cursor is present so
// the markers track the camera as the local viewer pans/zooms. pointerEvents is
// off throughout, so it never intercepts canvas gestures. The local viewer's own
// cursor is not echoed back, so only *other* people show up here.
function CursorLayer() {
  const [cursors, setCursors] = useState([])
  const [, rerender] = useReducer((n) => n + 1, 0)
  useEffect(() => subscribePeerCursors(setCursors), [])
  // Follow the camera: re-read page->screen each frame while cursors exist.
  useEffect(() => {
    if (!cursors.length) return
    let raf = requestAnimationFrame(function loop() {
      rerender()
      raf = requestAnimationFrame(loop)
    })
    return () => cancelAnimationFrame(raf)
  }, [cursors.length])

  const editor = getEditor()
  if (!editor || !cursors.length) return null
  return (
    <div style={{ position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 400 }}>
      {cursors.map((c) => {
        const p = editor.pageToScreen({ x: c.x, y: c.y })
        return (
          <div
            key={c.id}
            style={{
              position: 'absolute',
              left: p.x,
              top: p.y,
              transform: 'translate(-2px, -2px)',
              display: 'flex',
              alignItems: 'flex-start',
              gap: 4,
              whiteSpace: 'nowrap',
            }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24"
                 style={{ filter: 'drop-shadow(0 1px 1.5px rgba(0,0,0,0.45))' }}>
              <path d="M4 2 L4 19 L9 14.5 L12.3 21 L15 19.7 L11.8 13.5 L18.5 13 Z"
                    fill={c.color || '#3b82f6'} stroke="white" strokeWidth="1.3"
                    strokeLinejoin="round" />
            </svg>
            <span
              style={{
                marginTop: 12,
                background: c.color || '#3b82f6',
                color: 'white',
                fontSize: 11,
                fontWeight: 600,
                fontFamily: 'system-ui, sans-serif',
                padding: '1px 6px',
                borderRadius: 6,
                boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
              }}
            >
              {c.name}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// A floating toolbar button to spawn/remove an ephemeral Inspector panel, for
// poking at panel state (or kernel globals) on demand without writing code.
// Only shown when the server permits it (local bind by default; see
// Canvas.serve ui_inspector). Sits bottom-left, above tldraw's zoom menu, and
// highlights while an inspector is open. Rendered as a sibling outside tldraw's
// container, so it uses self-contained colors rather than the --pc-* theme vars.
function InspectorButton() {
  const [state, setState] = useState({ enabled: false, open: false })
  useEffect(() => subscribeUiInspector(setState), [])
  if (!state.enabled) return null
  return (
    <button
      onClick={toggleUiInspector}
      title={state.open ? 'Remove the inspector panel' : 'Add an inspector panel to query the canvas'}
      style={{
        position: 'absolute',
        bottom: 54,
        left: 8,
        zIndex: 300,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 12px',
        borderRadius: 8,
        cursor: 'pointer',
        background: state.open ? '#2563eb' : 'rgba(20, 20, 22, 0.82)',
        color: '#fff',
        border: '1px solid rgba(255, 255, 255, 0.18)',
        boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)',
        fontFamily: 'system-ui, sans-serif',
        fontSize: 12,
        fontWeight: 600,
        userSelect: 'none',
      }}
    >
      <span style={{ fontSize: 14, lineHeight: 1 }}>🔍</span>
      {state.open ? 'Inspector ✕' : 'Inspector'}
    </button>
  )
}

// A floating sign-out button, shown only on a password-protected canvas
// (welcome.auth). Clicking it navigates to /__logout__, where the server clears
// the session cookie and the login page returns — so a viewer can re-log in as a
// different role. Sits bottom-right (clear of the presence badge top-center, the
// Inspector button bottom-left, and tldraw's menus); rendered outside tldraw's
// container, so it uses self-contained colours rather than the --pc-* theme vars.
function SignOutButton() {
  const [enabled, setEnabled] = useState(false)
  useEffect(() => subscribeAuth(setEnabled), [])
  if (!enabled) return null
  return (
    <button
      onClick={signOut}
      title="Sign out and return to the password page"
      style={{
        // Above tldraw's bottom-right help menu (shown when ui chrome is on),
        // mirroring how InspectorButton clears the bottom-left zoom menu.
        position: 'absolute',
        bottom: 54,
        right: 8,
        zIndex: 300,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 12px',
        borderRadius: 8,
        cursor: 'pointer',
        background: 'rgba(20, 20, 22, 0.82)',
        color: '#f87171',
        border: '1px solid rgba(255, 255, 255, 0.18)',
        boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)',
        fontFamily: 'system-ui, sans-serif',
        fontSize: 12,
        fontWeight: 600,
        userSelect: 'none',
      }}
    >
      <span style={{ fontSize: 14, lineHeight: 1 }}>🔓</span>
      Sign out
    </button>
  )
}

// A floating toolbar button that opens a panel listing panels the user deleted
// in tldraw. Panels are never destroyed in Python — their callbacks and state
// stay live — so clicking Restore re-registers them on the canvas without
// restarting the script. Only shown when the server permits it (local bind by
// default; see Canvas.serve ui_graveyard=). Sits bottom-left, above the
// Inspector button.
function GraveyardButton() {
  const [state, setState] = useState({ enabled: false, open: false, items: [] })
  useEffect(() => subscribeGraveyard(setState), [])
  if (!state.enabled) return null
  const count = state.items.length
  return (
    <>
      <button
        onClick={toggleGraveyard}
        title={state.open ? 'Close the graveyard panel' : 'Show panels deleted from the canvas'}
        style={{
          position: 'absolute',
          bottom: 88,
          left: 8,
          zIndex: 300,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '6px 12px',
          borderRadius: 8,
          cursor: 'pointer',
          background: state.open ? '#2563eb' : 'rgba(20, 20, 22, 0.82)',
          color: '#fff',
          border: '1px solid rgba(255, 255, 255, 0.18)',
          boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)',
          fontFamily: 'system-ui, sans-serif',
          fontSize: 12,
          fontWeight: 600,
          userSelect: 'none',
        }}
      >
        <span style={{ fontSize: 14, lineHeight: 1 }}>🗑️</span>
        Graveyard{count > 0 ? ` (${count})` : ''}
        {state.open ? ' ✕' : ''}
      </button>
      {state.open && <GraveyardPanel items={state.items} />}
    </>
  )
}

function GraveyardPanel({ items }) {
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 130,
        left: 8,
        zIndex: 300,
        width: 280,
        maxHeight: 320,
        overflowY: 'auto',
        background: 'rgba(20, 20, 22, 0.95)',
        border: '1px solid rgba(255, 255, 255, 0.18)',
        borderRadius: 10,
        boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5)',
        fontFamily: 'system-ui, sans-serif',
        color: '#fff',
      }}
    >
      <div style={{
        padding: '8px 12px 6px',
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        color: 'rgba(255,255,255,0.45)',
        borderBottom: '1px solid rgba(255,255,255,0.1)',
      }}>
        Deleted panels
      </div>
      {items.length === 0 ? (
        <div style={{ padding: '10px 12px', fontSize: 12, color: 'rgba(255,255,255,0.45)', fontStyle: 'italic' }}>
          No deleted panels
        </div>
      ) : items.map((item) => (
        <div
          key={item.id}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '7px 12px',
            borderBottom: '1px solid rgba(255,255,255,0.07)',
          }}
        >
          <span style={{
            flex: 1,
            fontSize: 12,
            fontFamily: 'ui-monospace, SFMono-Regular, monospace',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {item.label || item.id}
          </span>
          <button
            onClick={() => sendRestore(item.id)}
            style={{
              flexShrink: 0,
              padding: '3px 10px',
              fontSize: 11,
              fontWeight: 600,
              cursor: 'pointer',
              background: '#2563eb',
              color: '#fff',
              border: 'none',
              borderRadius: 5,
              fontFamily: 'system-ui, sans-serif',
            }}
          >
            Restore
          </button>
        </div>
      ))}
    </div>
  )
}

// Make right-click-drag pan the camera. tldraw has no built-in option for this
// (right-click is its context menu), so we move the camera from raw pointer
// events on its container. Listeners run in the capture phase and stopPropagation
// on the right button so tldraw never sees the gesture. Camera x/y are page-space,
// so a screen-pixel drag delta is divided by zoom. A locked camera won't move:
// setCamera respects isLocked unless `force` is passed (we don't).
//
// The context menu is suppressed only when the drag actually moved (>4px), so a
// plain right-click still opens tldraw's menu. Returns a cleanup for onMount.
function enableRightDragPan(editor) {
  const el = editor.getContainer()
  let panning = false
  let moved = 0
  let lastX = 0
  let lastY = 0

  const onDown = (e) => {
    if (e.button !== 2) return
    panning = true
    moved = 0
    lastX = e.clientX
    lastY = e.clientY
    try { el.setPointerCapture(e.pointerId) } catch {}
    e.stopPropagation()
  }
  const onMove = (e) => {
    if (!panning) return
    const dx = e.clientX - lastX
    const dy = e.clientY - lastY
    moved += Math.abs(dx) + Math.abs(dy)
    lastX = e.clientX
    lastY = e.clientY
    const cam = editor.getCamera()
    editor.setCamera(
      { x: cam.x + dx / cam.z, y: cam.y + dy / cam.z, z: cam.z },
      { immediate: true }
    )
    e.preventDefault()
    e.stopPropagation()
  }
  const onUp = (e) => {
    if (e.button !== 2 || !panning) return
    panning = false
    try { el.releasePointerCapture(e.pointerId) } catch {}
    e.stopPropagation()
  }
  const onContextMenu = (e) => {
    if (moved > 4) e.preventDefault() // a drag, not a click — swallow the menu
  }

  el.addEventListener('pointerdown', onDown, true)
  el.addEventListener('pointermove', onMove, true)
  el.addEventListener('pointerup', onUp, true)
  el.addEventListener('pointercancel', onUp, true)
  el.addEventListener('contextmenu', onContextMenu, true)
  return () => {
    el.removeEventListener('pointerdown', onDown, true)
    el.removeEventListener('pointermove', onMove, true)
    el.removeEventListener('pointerup', onUp, true)
    el.removeEventListener('pointercancel', onUp, true)
    el.removeEventListener('contextmenu', onContextMenu, true)
  }
}

// The canvas owns every wheel gesture. We handle zoom AND pan ourselves in a
// capture-phase listener on the editor container, which fires before the event
// reaches whatever's under the cursor — so the behaviour is identical over the
// empty canvas and over an interactive panel. Panels no longer eat the wheel to
// scroll their own content (they scroll via their scrollbar instead), which is
// what made the cursor get "stuck" unable to zoom while hovering a panel.
//
//   physical mouse wheel  → zoom toward the cursor
//   trackpad pinch (ctrl) → zoom toward the cursor
//   trackpad two-finger   → pan the canvas
//
// Mouse-wheel detection (most-reliable first):
//   deltaMode !== 0        → line/page mode = Firefox mouse wheel.
//   wheelDeltaY % 120 === 0 → Chrome/Safari/Edge: physical wheels always emit in
//                             exact ±120 multiples; trackpads never do.
//   fallback               → large vertical-only delta (Firefox pixel mode).
//
// Zoom keeps the page point under the cursor fixed (same formula as
// enableRightDragPan); pan shifts the camera by the scroll delta in page units
// (client px / zoom), matching tldraw's own pan direction.
// ponytail: zoom rates are the tuning knobs — pinch is fine-grained, the wheel
// coarser; nudge if zoom feels too fast/slow on your device.
const WHEEL_ZOOM_RATE = 0.002
const PINCH_ZOOM_RATE = 0.01
// Pan smoothing: each animation frame the camera moves this fraction of the way
// to the outstanding scroll, so it eases toward the target instead of jumping on
// every wheel event. 1 = no smoothing (snappy); lower = smoother/floatier. This
// is the knob for how hard touchpad pan snaps to pure horizontal/vertical: the
// OS axis-locks the raw deltas, and easing the response rounds the abrupt H↔V
// transitions into a smoother diagonal. (The OS lock itself can't be undone in
// JS — we only get the deltas it reports.)
const PAN_SMOOTHING = 0.2

function enableSmartScroll(editor) {
  const el = editor.getContainer()

  // Outstanding pan (client px) not yet folded into the camera, drained by an
  // rAF loop so quick axis-locked bursts blend into a smooth glide. The total is
  // always fully applied (the per-frame fractions sum to it) — only spread out.
  let panX = 0
  let panY = 0
  let raf = 0
  const flushPan = () => {
    const ax = panX * PAN_SMOOTHING
    const ay = panY * PAN_SMOOTHING
    panX -= ax
    panY -= ay
    const cam = editor.getCamera()
    editor.setCamera({ x: cam.x - ax / cam.z, y: cam.y - ay / cam.z, z: cam.z }, { immediate: true })
    if (Math.abs(panX) > 0.1 || Math.abs(panY) > 0.1) {
      raf = requestAnimationFrame(flushPan)
    } else {
      raf = 0
      panX = 0
      panY = 0
    }
  }

  const onWheel = (e) => {
    // Identify physical mouse wheel events (vs trackpad).
    let isMouse = false
    if (e.deltaMode !== 0) {
      isMouse = true  // Firefox line/page mode — always a mouse wheel
    } else if (typeof e.wheelDeltaY === 'number' && e.wheelDeltaY !== 0) {
      isMouse = e.wheelDeltaY % 120 === 0  // Chrome/Safari/Edge ±120-multiple test
    } else {
      isMouse = Math.abs(e.deltaY) >= 40 && e.deltaX === 0  // Firefox pixel fallback
    }

    // Claim the gesture before the panel (or native scroll) can.
    e.preventDefault()
    e.stopPropagation()

    // Normalize line/page mode (Firefox) to pixels.
    const dx = e.deltaMode === 1 ? e.deltaX * 40 : e.deltaX
    const dy = e.deltaMode === 1 ? e.deltaY * 40 : e.deltaY

    if (e.ctrlKey || isMouse) {
      // Zoom toward the cursor, keeping the page point under it fixed. Applied
      // immediately — only pan is eased (zoom-to-cursor needs the live position).
      const cam = editor.getCamera()
      const rate = e.ctrlKey ? PINCH_ZOOM_RATE : WHEEL_ZOOM_RATE
      const newZ = Math.max(0.1, Math.min(8, cam.z * Math.exp(-dy * rate)))
      const newX = e.clientX / newZ - e.clientX / cam.z + cam.x
      const newY = e.clientY / newZ - e.clientY / cam.z + cam.y
      editor.setCamera({ x: newX, y: newY, z: newZ }, { immediate: true })
    } else {
      // Trackpad two-finger scroll → pan, eased. Accumulate the delta (client px,
      // minus matches tldraw: scroll down reveals lower content); flushPan drains
      // it to the camera over the next few frames.
      panX += dx
      panY += dy
      if (!raf) raf = requestAnimationFrame(flushPan)
    }
  }

  el.addEventListener('wheel', onWheel, { capture: true, passive: false })
  return () => {
    el.removeEventListener('wheel', onWheel, { capture: true, passive: false })
    if (raf) cancelAnimationFrame(raf)
  }
}

function seedDemo(editor) {
  editor.createShape({
    id: createShapeId('demo_label'),
    type: 'pcLabel',
    x: 360,
    y: 80,
    props: { label: 'status', value: 'idle' },
  })
}