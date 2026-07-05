// Runtime host for user-authored React components (the `React` Python panel,
// which is now nearly every built-in: Slider, Label, Plot, Table, ...). Faithful
// port of the old ReactHost.jsx, with two swaps: `react` -> `preact/compat`
// (so the Python-shipped React-hook JSX runs unchanged) and the original
// editor/useEditor/useValue -> the engine shims. The JSX is transformed by
// Sucrase (classic runtime -> React.createElement) and evaluated with `React`
// (= preact/compat) and `libs` in scope; it must define a function `Component`.
import { transform as transformJsx } from 'sucrase'
import React from 'preact/compat'
import { useEditor, useValue } from './EngineContext'
import { paintFrameStream, type PaintFrameOpts } from './frame'
import { navMode } from '../engine/camera'
import {
  sendInput,
  sendBinary,
  requestData,
  registerLive,
  unregisterLive,
  registerStyle,
  unregisterStyle,
  componentIdOf,
  fitNative,
  applyCameraFrom,
  sendPanelError,
  subscribeChat,
  getChatLog,
  sendChat,
  setMyName,
  subscribeIdentity,
  getSharedComponents,
  getSharedVersion,
  subscribeShared,
} from '../bridge'

// Compile a source string into a factory (React, libs) => Component, memoised by
// source. The transform runs once per distinct source; binding is per-render.
const cache = new Map<string, { factory?: any; error?: any }>()

function compile(source: string): { factory?: any; error?: any } {
  if (cache.has(source)) return cache.get(source)!
  let entry: { factory?: any; error?: any }
  try {
    const { code } = transformJsx(source, {
      transforms: ['jsx'],
      jsxRuntime: 'classic',
      production: true,
    })
    // eslint-disable-next-line no-new-func
    const factory = new Function('React', 'libs', `${code}\n; return Component;`)
    entry = { factory }
  } catch (error) {
    entry = { error }
  }
  cache.set(source, entry)
  return entry
}

// --- optional third-party libraries (Python scope=[...]) ---------------------
const LIB_URLS: Record<string, string> = {
  d3: 'https://esm.sh/d3@7',
  lodash: 'https://esm.sh/lodash-es@4',
  'date-fns': 'https://esm.sh/date-fns@4',
  motion: 'https://esm.sh/framer-motion@11?external=react,react-dom',
  'framer-motion': 'https://esm.sh/framer-motion@11?external=react,react-dom',
  lucide: 'https://esm.sh/lucide-react@0.460.0?external=react',
  'lucide-react': 'https://esm.sh/lucide-react@0.460.0?external=react',
}
function libUrl(name: string): string {
  return LIB_URLS[name] || `https://esm.sh/${name}?external=react,react-dom`
}

const moduleCache = new Map<string, Promise<any>>()
const wasmCache = new Map<string, Promise<any>>()

// CDN libraries (scope=[...]) are fetched from esm.sh at runtime. Without a
// ceiling a slow/unreachable CDN — a baked desktop app offline, esm.sh down, a
// LAN with no internet — leaves the panel stuck on "loading libraries…" forever.
// Reject after this long so the failure surfaces as an ErrorBox (and a
// panel_error) instead of hanging.
const LIB_TIMEOUT_MS = 15000

function withTimeout<T>(p: Promise<T>, ms: number, name: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`timed out loading "${name}" after ${ms / 1000}s — is the network reachable?`)),
      ms,
    )
    p.then(
      (v) => {
        clearTimeout(timer)
        resolve(v)
      },
      (e) => {
        clearTimeout(timer)
        reject(e)
      },
    )
  })
}

function loadWasm(b64: string): Promise<any> {
  if (!wasmCache.has(b64)) {
    wasmCache.set(
      b64,
      (async () => {
        const binary = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
        const { instance } = await WebAssembly.instantiate(binary)
        return instance.exports
      })(),
    )
  }
  return wasmCache.get(b64)!
}

// Libraries bundled locally so panels can use them without a CDN fetch. Each is
// a thunk returning a dynamic import (its own lazy chunk), keyed separately from
// CDN URLs so the namespaces never collide. Plot/LivePlot/Histogram request
// scope=["plotly"].
// The cartesian dist (not basic): Histogram renders `heatmap` traces, which
// basic lacks — Plotly silently degrades them to a meaningless scatter line.
const LOCAL_MODULES: Record<string, () => Promise<any>> = {
  plotly: () => import('plotly.js-cartesian-dist-min'),
}

function loadLib(name: string): Promise<any> {
  if (name in LOCAL_MODULES) {
    const key = `__local__${name}`
    if (!moduleCache.has(key)) moduleCache.set(key, LOCAL_MODULES[name]())
    return moduleCache.get(key)!
  }
  const url = libUrl(name)
  if (!moduleCache.has(url)) {
    // Evict a failed/timed-out load from the cache so a later re-mount retries
    // instead of replaying the same rejected promise forever.
    const p = withTimeout(import(/* @vite-ignore */ url), LIB_TIMEOUT_MS, name).catch((e) => {
      moduleCache.delete(url)
      throw e
    })
    moduleCache.set(url, p)
  }
  return moduleCache.get(url)!
}

function pickExport(mod: any): any {
  const keys = Object.keys(mod)
  if (keys.length === 1 && keys[0] === 'default') return mod.default
  return mod
}

function useLibs(names: string[]): { ready: boolean; libs: any; error: any } {
  const key = names.join(',')
  const [state, setState] = React.useState(() =>
    names.length ? { ready: false, libs: {}, error: null } : { ready: true, libs: {}, error: null },
  )
  React.useEffect(() => {
    if (!names.length) {
      setState({ ready: true, libs: {}, error: null })
      return
    }
    let cancelled = false
    setState({ ready: false, libs: {}, error: null })
    Promise.all(names.map((n) => loadLib(n).then((mod) => [n, pickExport(mod)])))
      .then((entries) => {
        if (cancelled) return
        setState({ ready: true, libs: Object.fromEntries(entries), error: null })
      })
      .catch((error) => {
        if (!cancelled) setState({ ready: false, libs: {}, error })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return state
}

class Boundary extends React.Component<any, { error: any }> {
  constructor(props: any) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error: any) {
    return { error }
  }
  componentDidCatch(error: any) {
    if (this.props.onError) this.props.onError(error)
  }
  componentDidUpdate(prev: any) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }
  render() {
    if (this.state.error) return <ErrorBox error={this.state.error} />
    return this.props.children
  }
}

function ErrorBox({ error }: { error: any }) {
  return (
    <div
      style={{
        flex: 1,
        overflow: 'auto',
        padding: 8,
        fontFamily: 'ui-monospace, monospace',
        fontSize: 12,
        color: 'var(--pc-detail-text, #b91c1c)',
        background: 'var(--pc-detail-bg, #fef2f2)',
        border: '1px solid var(--pc-detail-border, #fecaca)',
        borderRadius: 4,
        whiteSpace: 'pre-wrap',
      }}
    >
      {String((error && error.message) || error)}
    </div>
  )
}

export default function ReactHost({ shape }: { shape: any }) {
  const id = componentIdOf(shape.id)
  const editor = useEditor()
  const [streamed, setStreamed] = React.useState<any>(undefined)
  const [liveStyle, setLiveStyle] = React.useState<any>(null)

  const [sharedV, setSharedV] = React.useState(getSharedVersion())
  React.useEffect(() => subscribeShared(setSharedV), [])

  const framesRef = React.useRef<Set<(d: any) => void>>(new Set())

  React.useEffect(() => {
    registerStyle(id, setLiveStyle)
    return () => unregisterStyle(id)
  }, [id])

  const wasmPromise = shape.props.wasm ? loadWasm(shape.props.wasm) : null
  const canvas = React.useMemo(
    () => ({
      send: (data: any) => sendInput(id, data),
      sendBinary: (buf: any) => sendBinary(id, buf instanceof ArrayBuffer ? buf : buf.buffer || buf),
      request: (data: any) => requestData(id, data),
      onFrame: (cb: (d: any) => void) => {
        framesRef.current.add(cb)
        return () => framesRef.current.delete(cb)
      },
      // Off-main-thread image-frame rendering: decode each binary frame via
      // createImageBitmap and blit it to `target` (a <canvas>), coalescing bursts.
      // Built-in VideoFeed uses this; any custom panel streaming image bytes can too.
      paintFrame: (target: HTMLCanvasElement, paintOpts?: PaintFrameOpts) =>
        paintFrameStream(
          (cb) => {
            framesRef.current.add(cb)
            return () => framesRef.current.delete(cb)
          },
          target,
          paintOpts || {},
        ),
      chat: {
        send: (text: string) => sendChat(text),
        setName: (name: string) => setMyName(name),
        history: () => getChatLog(),
        subscribe: (cb: any) => subscribeChat(cb),
        identity: (cb: any) => subscribeIdentity(cb),
      },
      viewport: (cb: (v: any) => void) => {
        const read = () => {
          const c = editor.getViewportPageBounds().center
          return { x: Math.round(c.x), y: Math.round(c.y), zoom: editor.getZoomLevel() }
        }
        let last: any = null
        const emit = () => {
          const v = read()
          if (last && v.x === last.x && v.y === last.y && v.zoom === last.zoom) return
          last = v
          cb(v)
        }
        emit()
        return editor.store.listen(emit, { scope: 'session' })
      },
      setView: (v: any) => applyCameraFrom(v || {}),
      wasm: wasmPromise,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, editor, wasmPromise],
  )

  let userProps: any = {}
  try {
    userProps = JSON.parse(shape.props.data || '{}')
  } catch {
    userProps = {}
  }
  if (liveStyle !== null) userProps = { ...userProps, _th: liveStyle }

  const libNames = React.useMemo(() => {
    try {
      const v = JSON.parse(shape.props.libs || '[]')
      return Array.isArray(v) ? v.filter((n: any) => typeof n === 'string') : []
    } catch {
      return []
    }
  }, [shape.props.libs])
  const { libs, ready: libsReady, error: libsError } = useLibs(libNames)

  React.useEffect(() => {
    const onPush = (data: any) => {
      if (framesRef.current.size) {
        for (const cb of framesRef.current) cb(data)
      } else {
        setStreamed(data)
      }
    }
    registerLive(id, onPush)
    return () => unregisterLive(id)
  }, [id, libsReady])

  // h="auto" / w="auto": fit the panel to the rendered content.
  const autoH = !!shape.props.autoH
  const autoW = !!shape.props.autoW
  const hostRef = React.useRef<HTMLDivElement>(null)
  const contentRef = React.useRef<HTMLDivElement>(null)
  React.useEffect(() => {
    if (!autoH && !autoW) return
    const host = hostRef.current
    const content = contentRef.current
    if (!host || !content) return
    const measure = () =>
      fitNative(id, host, {
        h: autoH ? content.scrollHeight : undefined,
        w: autoW ? content.scrollWidth : undefined,
      })
    measure()
    let ro: ResizeObserver | undefined
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(measure)
      ro.observe(content)
      ro.observe(host)
    }
    return () => ro && ro.disconnect()
  }, [autoH, autoW, id, shape.props.source, shape.props.data, libsReady])

  const sharedSrc = React.useMemo(() => Object.values(getSharedComponents()).join('\n\n'), [sharedV])
  const fullSource = sharedSrc ? `${sharedSrc}\n\n${shape.props.source || ''}` : shape.props.source || ''

  const compiled = compile(fullSource)
  const bound = React.useMemo(() => {
    if (compiled.error) return { error: compiled.error }
    try {
      const Comp = compiled.factory(React, libs)
      if (typeof Comp !== 'function') {
        throw new Error('source must define a function named `Component`')
      }
      return { Comp }
    } catch (error) {
      return { error }
    }
  }, [compiled, libs])

  React.useEffect(() => {
    if ((bound as any).error) sendPanelError(id, (bound as any).error.message || String((bound as any).error))
  }, [id, (bound as any).error])

  React.useEffect(() => {
    if (libsError) sendPanelError(id, `failed to load libraries: ${(libsError as any).message || libsError}`)
  }, [id, libsError])

  // These reactive reads MUST run unconditionally before any early return.
  const ghost = !!shape.meta?.noGrab && !!shape.meta?.lockInput && !shape.isLocked
  const toolIsSelect = useValue('pc-tool', () => editor.getCurrentToolId() === 'select', [editor])
  const handMode = useValue('pc-hand-tool', () => editor.getCurrentToolId() === 'hand', [editor])
  // In a scroll/document mode panels stay fully interactive (no pc-hand): the
  // webpage-style scroll rule lives in input.ts — a touch that starts on interactive
  // content gives that content the gesture, otherwise the page scrolls — so nothing
  // here needs to be made touch-transparent. (Draw tools still pass through to ink.)
  const scrollDoc = useValue('pc-scrolldoc', () => navMode() !== 'free', [])
  const nonPanelHovered = useValue(
    'pc-hover',
    () => {
      const hid = editor.getHoveredShapeId()
      if (!hid) return false
      const s: any = editor.getShape(hid)
      return !!s && !String(s.shapeType || '').startsWith('pc')
    },
    [editor],
  )

  if (libsError) {
    return <ErrorBox error={new Error(`failed to load libraries: ${(libsError as any).message || libsError}`)} />
  }
  if (!libsReady) {
    return (
      <div
        data-pc-loading=""
        style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--pc-faint)', fontSize: 13 }}
      >
        loading libraries…
      </div>
    )
  }
  if ((bound as any).error) return <ErrorBox error={(bound as any).error} />
  const Comp = (bound as any).Comp
  return (
    <div
      ref={hostRef}
      className={
        ghost
          ? undefined
          : scrollDoc
            ? toolIsSelect || handMode
              ? undefined // document mode: panels interactive (scroll rule is in input.ts)
              : 'pc-draw-passthrough' // a draw tool still passes through to the ink layer
            : handMode
              ? 'pc-hand'
              : !toolIsSelect || nonPanelHovered
                ? 'pc-draw-passthrough'
                : undefined
      }
      style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', pointerEvents: ghost ? 'none' : 'all' }}
      onMouseDownCapture={
        ghost
          ? undefined
          : (e: any) => {
              if (e.button !== 0) e.stopPropagation()
            }
      }
      onPointerDown={
        ghost
          ? undefined
          : (e: any) => {
              if (e.button !== 0) return
              if (handMode) {
                e.stopPropagation()
                return
              }
              if (!toolIsSelect) return
              const pt = editor.screenToPage({ x: e.clientX, y: e.clientY })
              const top: any = editor.getShapeAtPoint(pt, { hitInside: true })
              if (top && !String(top.shapeType || '').startsWith('pc')) return
              e.stopPropagation()
            }
      }
      onTouchStart={ghost ? undefined : (e: any) => e.stopPropagation()}
      onTouchEnd={ghost ? undefined : (e: any) => e.stopPropagation()}
      onDragStart={(e: any) => e.preventDefault()}
    >
      <div
        ref={contentRef}
        style={
          autoH || autoW
            ? {
                flex: '0 0 auto',
                ...(autoW ? { width: 'max-content', maxWidth: 'none', alignSelf: 'flex-start' } : { minWidth: 0 }),
              }
            : { display: 'contents' }
        }
      >
        {shape.props.css ? <style>{shape.props.css}</style> : null}
        <Boundary resetKey={Comp} onError={(e: any) => sendPanelError(id, e.message || String(e))}>
          <Comp canvas={canvas} value={streamed} props={userProps} />
        </Boundary>
      </div>
    </div>
  )
}
