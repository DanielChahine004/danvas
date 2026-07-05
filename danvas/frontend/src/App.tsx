// App root. Sets the initial theme (default dark, like the old build), opens the
// WebSocket, and renders the panel layer plus the presence badge. The
// engine + PanelLayer are the whole canvas surface.
import { useEffect, useState } from 'preact/hooks'
import { useValue } from './react/EngineContext'
import { PanelLayer } from './react/PanelLayer'
import { CursorLayer, InspectorButton, GraveyardButton, SignOutButton, KioskHandTool, UndoRedoButtons, StyleToggle, SettingsButton, MergeHostPanel, MergeLaunchButton, MergeOriginDot, HostingButton } from './react/overlays'
import { Toolbar } from './react/Toolbar'
import { StylePanel } from './react/StylePanel'
import { TextEditor } from './react/TextEditor'
import { ContextMenu } from './react/ContextMenu'
import { connect, subscribePresence, subscribeViewConfig, _debug } from './bridge'
import { editor } from './engine/editor'
import { store } from './engine/store'
import * as camera from './engine/camera'
import './theme.css'

let connected = false

export function App() {
  // `ui: false` (a kiosk view) hides the app's own chrome (Inspector/Graveyard
  // buttons), but never the sign-out escape hatch.
  const [hideUi, setHideUi] = useState(false)
  useEffect(() => subscribeViewConfig((view: any) => setHideUi(view?.ui === false)), [])
  // chrome (toolbar/style panel/buttons) follows the canvas theme via CSS vars on
  // the root — see `.pc-ui` / `.pc-ui-light` in theme.css.
  const dark = useValue('app-dark', () => store.instance().darkMode, [])

  useEffect(() => {
    // Dark by default on first visit; otherwise honour the saved choice.
    try {
      const saved = localStorage.getItem('pc-theme') as 'dark' | 'light' | null
      editor.user.updateUserPreferences({ colorScheme: saved || 'dark' })
    } catch {
      editor.user.updateUserPreferences({ colorScheme: 'dark' })
    }
    // Inspection hook — the browser smoke suite (tests/test_frontend_smoke.py)
    // asserts against the store of the REAL shipped build, so this is no
    // longer dev-gated. It exposes nothing a same-origin console can't
    // already reach, and it's handy when debugging the engine live.
    ;(window as any).__danvas = { store, editor, camera, bridge: _debug }
    if (!connected) {
      connected = true
      connect()
    }
  }, [])

  return (
    <div class={dark ? 'pc-ui' : 'pc-ui pc-ui-light'} style={{ position: 'fixed', inset: 0 }}>
      <PresenceBadge />
      <CursorLayer />
      <SignOutButton />
      {!hideUi && <InspectorButton />}
      {!hideUi && <GraveyardButton />}
      {!hideUi && <MergeHostPanel />}
      {!hideUi && <HostingButton />}
      {!hideUi && <MergeLaunchButton />}
      {!hideUi && <MergeOriginDot />}
      {hideUi && <KioskHandTool />}
      <PanelLayer />
      {!hideUi && <SettingsButton />}
      {!hideUi && <UndoRedoButtons />}
      {!hideUi && <Toolbar />}
      {!hideUi && <StylePanel />}
      {!hideUi && <StyleToggle />}
      <TextEditor />
      {!hideUi && <ContextMenu />}
    </div>
  )
}

// A small live-viewer badge floated over the canvas (port of App.jsx). Colours
// are self-contained (it sits outside .tl-container, so --pc-* wouldn't resolve).
function PresenceBadge() {
  const [count, setCount] = useState(0)
  useEffect(() => subscribePresence(setCount), [])
  if (count < 1) return null
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
        background: 'var(--ui-bg)',
        color: '#16a34a',
        border: '1px solid var(--ui-border)',
        boxShadow: 'var(--ui-shadow-sm)',
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
