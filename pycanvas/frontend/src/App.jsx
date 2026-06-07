import { useEffect, useState } from 'react'
import { Tldraw, createShapeId } from 'tldraw'
import 'tldraw/tldraw.css'
import './theme.css' // PyCanvas panel theme vars (after tldraw.css so they win)
import { shapeUtils } from './canvas'
import { setEditor, subscribePresence } from './bridge'

export default function App() {
  return (
    <div style={{ position: 'fixed', inset: 0 }}>
      <PresenceBadge />
      <Tldraw
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
          setEditor(editor)
        }}
      />
    </div>
  )
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

function seedDemo(editor) {
  editor.createShape({
    id: createShapeId('demo_slider'),
    type: 'pcSlider',
    x: 80,
    y: 80,
    props: { label: 'servo_1', min: 0, max: 180, value: 90 },
  })
  editor.createShape({
    id: createShapeId('demo_label'),
    type: 'pcLabel',
    x: 360,
    y: 80,
    props: { label: 'status', value: 'idle' },
  })
  editor.createShape({
    id: createShapeId('demo_video'),
    type: 'pcVideo',
    x: 80,
    y: 220,
    props: { label: 'camera' },
  })
}
