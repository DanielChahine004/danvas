// The sandboxed iframe that hosts a Custom panel's HTML (port of canvas.jsx's
// CustomView). The iframe's `window.canvas` shim (send/onPush/sendBinary/
// requestCamera/...) is injected Python-side in custom.py, so this component only
// does the parent half: render the srcDoc, push Python `push()` data INTO the
// iframe over postMessage (zero-copy for binary), and relay panel errors back to
// Python. The outbound iframe messages (send/binary/wheel/fit/camera/mic) are
// handled by the global window listener in bridge.ts.
import { useCallback, useEffect, useRef } from 'preact/hooks'
import { useEditor, useValue } from './EngineContext'
import { sendPanelError, componentIdOf, registerLive, unregisterLive } from '../bridge'

// The canvas theme variables forwarded into a themed=True iframe so its CSS can
// use var(--pc-*) and track dark mode (a sandboxed iframe can't inherit them).
// Kept in step with theme.css's .tl-container block.
const PC_THEME_VARS = [
  '--pc-bg', '--pc-text', '--pc-muted', '--pc-faint', '--pc-border', '--pc-border-mid',
  '--pc-border-soft', '--pc-shadow', '--pc-accent', '--pc-accent-text', '--pc-input-bg',
  '--pc-code-bg', '--pc-off-bg', '--pc-off-text', '--pc-detail-bg', '--pc-detail-border',
  '--pc-detail-text', '--pc-video-bg',
]

export function CustomView({ shape }: { shape: any }) {
  const ref = useRef<HTMLIFrameElement>(null)
  const id = componentIdOf(shape.id)
  const editor = useEditor()
  const toolIsSelect = useValue('pc-tool', () => editor.getCurrentToolId() === 'select', [editor])
  const dark = useValue('pc-dark', () => editor.user.getIsDarkMode(), [editor])
  // Decorative panels (grabbable=False + operable=False) are click-through.
  const ghost = !!shape.meta?.noGrab && !!shape.meta?.lockInput && !shape.isLocked

  // themed=True: forward the live --pc-* variables + dark flag into the iframe on
  // load and on every theme/dark-mode change, so the panel follows the canvas
  // theme like an inline React panel. (A sandboxed iframe can't read the parent's
  // CSS, so we push the computed values in over postMessage.)
  const themed = !!shape.props.themed
  const postTheme = useCallback(() => {
    if (!themed) return
    const win = ref.current?.contentWindow
    if (!win) return
    const root = document.querySelector('.tl-container') as HTMLElement | null
    const cs = root ? getComputedStyle(root) : null
    const vars: Record<string, string> = {}
    if (cs) for (const name of PC_THEME_VARS) vars[name] = cs.getPropertyValue(name).trim()
    win.postMessage({ __danvas_theme: { vars, dark } }, '*')
  }, [themed, dark])
  useEffect(() => {
    postTheme()
  }, [postTheme, shape.props.html]) // re-push when theme/dark changes or the doc reloads

  // Push Python data into the iframe (the live `post` channel). Binary arrives as
  // an ArrayBuffer and is transferred (zero-copy) rather than structured-cloned.
  useEffect(() => {
    const post = (data: any) => {
      const el = ref.current
      if (!el || !el.contentWindow) return
      const transfer = data instanceof ArrayBuffer ? [data] : ArrayBuffer.isView(data) ? [data.buffer] : []
      el.contentWindow.postMessage({ __danvas: data }, '*', transfer)
    }
    registerLive(id, post)
    return () => unregisterLive(id)
  }, [id])

  // Relay JS errors from inside the iframe back to Python (terminal).
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (!e.data) return
      const err = (e.data as any).__danvas_error
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
      // allow-scripts lets interactive content run; no allow-same-origin keeps the
      // user HTML sandboxed. allow-popups-to-escape-sandbox lets target=_blank
      // links open as normal tabs.
      sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms"
      allow={shape.props.permissions || undefined}
      style={{
        flex: 1,
        minHeight: 0,
        width: '100%',
        border: 'none',
        borderRadius: 4,
        background: shape.meta?.noFrame ? 'transparent' : 'var(--pc-bg)',
        colorScheme: shape.props.themed ? (dark ? 'dark' : 'light') : undefined,
        pointerEvents: ghost || !toolIsSelect ? 'none' : 'all',
      }}
      // Once the document (and its theme listener) is live, push the theme in —
      // the mount-time effect can run before the iframe has parsed its script.
      onLoad={themed ? postTheme : undefined}
      // Keep the engine from hijacking drags/zoom meant for the iframe content.
      // (No drawing layer yet, so drawingOnTop is always false.)
      onPointerDown={ghost || !toolIsSelect ? undefined : (e: any) => e.stopPropagation()}
      onDragStart={(e: any) => e.preventDefault()}
    />
  )
}
