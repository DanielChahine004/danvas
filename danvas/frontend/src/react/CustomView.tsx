// The sandboxed iframe that hosts a Custom panel's HTML (port of canvas.jsx's
// CustomView). The iframe's `window.canvas` shim (send/onPush/sendBinary/
// requestCamera/...) is injected Python-side in custom.py, so this component only
// does the parent half: render the srcDoc, push Python `push()` data INTO the
// iframe over postMessage (zero-copy for binary), and relay panel errors back to
// Python. The outbound iframe messages (send/binary/wheel/fit/camera/mic) are
// handled by the global window listener in bridge.ts.
import { useEffect, useRef } from 'preact/hooks'
import { useEditor, useValue } from './EngineContext'
import { sendPanelError, componentIdOf, registerLive, unregisterLive } from '../bridge'

export function CustomView({ shape }: { shape: any }) {
  const ref = useRef<HTMLIFrameElement>(null)
  const id = componentIdOf(shape.id)
  const editor = useEditor()
  const toolIsSelect = useValue('pc-tool', () => editor.getCurrentToolId() === 'select', [editor])
  const dark = useValue('pc-dark', () => editor.user.getIsDarkMode(), [editor])
  // Decorative panels (grabbable=False + operable=False) are click-through.
  const ghost = !!shape.meta?.noGrab && !!shape.meta?.lockInput && !shape.isLocked

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
      // Keep the engine from hijacking drags/zoom meant for the iframe content.
      // (No drawing layer yet, so drawingOnTop is always false.)
      onPointerDown={ghost || !toolIsSelect ? undefined : (e: any) => e.stopPropagation()}
      onDragStart={(e: any) => e.preventDefault()}
    />
  )
}
