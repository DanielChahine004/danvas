// Runtime host for user-authored React components (the `React` Python panel).
//
// This is the native counterpart to the sandboxed-iframe Custom panel: the user
// ships JSX *source* from Python, and here — in the main page, with full theme
// and bridge access — we compile it with Babel and mount the result as an
// ordinary React subtree inside the panel's Card. Babel is ~3 MB, so this whole
// module is lazily imported (see ReactShapeUtil) and only loaded the first time a
// React panel appears, exactly like the Monaco-backed Repl.
//
// The user writes a component named `Component`; it receives three props:
//   canvas  - { send(data), onFrame(cb), chat }: panel -> Python (send, routed
//             to @on handlers), a no-re-render subscription to the push() stream
//             (onFrame) for high-rate/binary data the component paints itself,
//             and `chat` — the canvas-wide shared room (send/setName/history/
//             subscribe/identity) that powers the Chat panel
//   value   - the latest push()ed data  : Python -> panel, no prop churn / reload
//             (skipped while an onFrame subscriber is active — pick one channel)
//   props   - the dict from update()/props=  : Python -> panel, replayed on reconnect
// React (with hooks) is in scope as `React`; any libraries requested via Python
// `scope=[...]` are in scope as `libs` (e.g. `const d3 = libs.d3`).
import * as Babel from '@babel/standalone'
import React from 'react'
import { sendInput, registerLive, unregisterLive, componentIdOf, fitNative } from './bridge'
import { subscribeChat, getChatLog, sendChat, setMyName, subscribeIdentity } from './bridge'

// Compile a source string into a *factory* — `(React, libs) => Component` —
// memoised by source so a re-render (or many panels sharing one source) runs
// Babel only once. The factory is invoked per-render with the live `React` and
// the loaded `libs` bundle (see useLibs); binding is cheap, so libraries can
// arrive after the first compile without recompiling. The source is transformed
// from JSX, then evaluated with `React` and `libs` in scope; it must define a
// function named `Component`.
const cache = new Map() // source -> { factory } | { error }

function compile(source) {
  if (cache.has(source)) return cache.get(source)
  let entry
  try {
    const code = Babel.transform(source, {
      presets: [['react', { runtime: 'classic' }]],
    }).code
    // eslint-disable-next-line no-new-func
    const factory = new Function('React', 'libs', `${code}\n; return Component;`)
    entry = { factory }
  } catch (error) {
    entry = { error }
  }
  cache.set(source, entry)
  return entry
}

// --- optional third-party libraries (Python `scope=[...]`) ------------------
// A panel can ask for libraries by name; we fetch them as ESM from a CDN on
// demand and hand them to the component as the `libs` global. Nothing is
// bundled — the cost (a network fetch) is paid only by panels that opt in — so
// the common case (no scope) loads nothing and behaves exactly as before.
//
// Friendly names map to pinned, React-externalised URLs so React-dependent libs
// (framer-motion, lucide) share this app's single React instance instead of
// pulling their own (which breaks hooks). Any other name is passed straight to
// esm.sh, still externalising react/react-dom.
const LIB_URLS = {
  d3: 'https://esm.sh/d3@7',
  lodash: 'https://esm.sh/lodash-es@4',
  'date-fns': 'https://esm.sh/date-fns@4',
  motion: 'https://esm.sh/framer-motion@11?external=react,react-dom',
  'framer-motion': 'https://esm.sh/framer-motion@11?external=react,react-dom',
  lucide: 'https://esm.sh/lucide-react@0.460.0?external=react',
  'lucide-react': 'https://esm.sh/lucide-react@0.460.0?external=react',
}

function libUrl(name) {
  return LIB_URLS[name] || `https://esm.sh/${name}?external=react,react-dom`
}

// One in-flight/resolved promise per URL, shared across panels and reconnects.
const moduleCache = new Map() // url -> Promise<module namespace>

function loadLib(name) {
  const url = libUrl(name)
  if (!moduleCache.has(url)) {
    // @vite-ignore: a runtime URL, not a build-time import to pre-bundle.
    moduleCache.set(url, import(/* @vite-ignore */ url))
  }
  return moduleCache.get(url)
}

// Resolve a list of names into a `{ name: export }` bundle. A module that only
// carries a default export (e.g. lodash) is unwrapped to that default; one with
// named exports (d3, framer-motion) is handed over as its namespace, so the
// component can `const d3 = libs.d3` or `const { motion } = libs['framer-motion']`.
function pickExport(mod) {
  const keys = Object.keys(mod)
  if (keys.length === 1 && keys[0] === 'default') return mod.default
  return mod
}

function useLibs(names) {
  const key = names.join(',')
  const [state, setState] = React.useState(() =>
    names.length ? { ready: false, libs: {}, error: null } : { ready: true, libs: {}, error: null }
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

// Keep a thrown render error inside the panel instead of letting it unmount the
// whole tldraw canvas. Resets when the compiled component identity changes.
class Boundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  componentDidUpdate(prev) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }
  render() {
    if (this.state.error) return <ErrorBox error={this.state.error} />
    return this.props.children
  }
}

function ErrorBox({ error }) {
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

export default function ReactHost({ shape }) {
  const id = componentIdOf(shape.id)
  // Latest value streamed via push() (Custom's `post` live channel, reused).
  const [streamed, setStreamed] = React.useState(undefined)

  // Imperative push subscribers (canvas.onFrame). A component that paints a
  // high-rate stream itself — to a <canvas>/<img>, with zero-copy binary —
  // registers here instead of reading the `value` prop, so each frame skips a
  // React re-render of the whole component.
  const framesRef = React.useRef(new Set())

  React.useEffect(() => {
    const onPush = (data) => {
      // When the component drives its own painting via onFrame, deliver straight
      // to those callbacks and skip setStreamed entirely (no re-render). The two
      // are mutually exclusive: read `value` for declarative/low-rate updates, or
      // subscribe onFrame for high-rate streams — not both.
      if (framesRef.current.size) {
        for (const cb of framesRef.current) cb(data)
      } else {
        setStreamed(data)
      }
    }
    registerLive(id, onPush)
    return () => unregisterLive(id)
  }, [id])

  // Stable bridge handle so the user component can post back to Python and
  // subscribe to the raw push() stream imperatively.
  const canvas = React.useMemo(
    () => ({
      send: (data) => sendInput(id, data),
      // Subscribe to every push() without re-rendering; returns an unsubscribe.
      // The payload is whatever Python pushed (an ArrayBuffer for binary, handed
      // over zero-copy). Call from a useEffect and return its result to clean up.
      onFrame: (cb) => {
        framesRef.current.add(cb)
        return () => framesRef.current.delete(cb)
      },
      // The shared chat room (server-stamped identity + cross-viewer relay), the
      // one thing that isn't per-component state. Unlike send/onFrame this isn't
      // routed to this panel's @on handlers — it's the canvas-wide channel every
      // viewer shares, exposed so a React panel (the Chat component) can be a
      // window onto it. `subscribe`/`identity` return an unsubscribe; `send` is
      // stamped with the viewer's identity by the server, not this component's id.
      chat: {
        send: (text) => sendChat(text),
        setName: (name) => setMyName(name),
        history: () => getChatLog(),
        subscribe: (cb) => subscribeChat(cb),
        identity: (cb) => subscribeIdentity(cb),
      },
    }),
    [id]
  )

  // Props from Python (update()/initial props=), carried as a JSON string prop so
  // they persist in the shape and replay on reconnect.
  let userProps = {}
  try {
    userProps = JSON.parse(shape.props.data || '{}')
  } catch {
    userProps = {}
  }

  // Optional libraries the panel asked for (Python `scope=[...]`), loaded as ESM
  // from a CDN and handed to the component as the `libs` global. Empty by
  // default, so the common case loads nothing.
  const libNames = React.useMemo(() => {
    try {
      const v = JSON.parse(shape.props.libs || '[]')
      return Array.isArray(v) ? v.filter((n) => typeof n === 'string') : []
    } catch {
      return []
    }
  }, [shape.props.libs])
  const { libs, ready: libsReady, error: libsError } = useLibs(libNames)

  // h="auto" / w="auto": fit the panel height/width to the rendered content. The
  // content sits in its own box (sized to content) so we can measure it; the host
  // fills the card body so its offset* gives the chrome overhead (see fitNative).
  // For width-fit the content box is laid out at `max-content` (see below), so
  // scrollWidth is the content's *natural* width, independent of the card width —
  // which keeps the fit from oscillating as the panel resizes to match it.
  const autoH = !!shape.props.autoH
  const autoW = !!shape.props.autoW
  const hostRef = React.useRef(null)
  const contentRef = React.useRef(null)
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
    let ro
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(measure)
      ro.observe(content) // content size changes
      ro.observe(host) // card-size changes that reflow content
    }
    return () => ro && ro.disconnect()
    // libsReady: re-measure once libraries load and the content reflows.
  }, [autoH, autoW, id, shape.props.source, shape.props.data, libsReady])

  // Compile (memoised by source), then bind the factory with React + the loaded
  // libs. Binding runs the user's module-level code and can throw (or omit
  // `Component`), so it's guarded; it re-binds when libs arrive. All hooks above
  // run unconditionally — only the render result branches below.
  const compiled = compile(shape.props.source || '')
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

  if (libsError) {
    return <ErrorBox error={new Error(`failed to load libraries: ${libsError.message || libsError}`)} />
  }
  if (!libsReady) {
    return (
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
        loading libraries…
      </div>
    )
  }
  if (bound.error) return <ErrorBox error={bound.error} />
  const Comp = bound.Comp
  // Decorative panels (grabbable=False + operable=False) are click-through: the
  // host takes no pointer, so clicks pass to whatever sits underneath on the
  // canvas. Mirrors the Custom iframe's `ghost`; see Card's `ghostable`.
  const ghost = !!shape.meta?.noGrab && !!shape.meta?.lockInput && !shape.isLocked
  return (
    // pointerEvents:'all' + stopPropagation claim the pointer for the hosted
    // component; without this tldraw treats a press as a move/resize of the
    // panel and the component never sees the click. The Card header (the label)
    // keeps no pointerEvents, so it stays the panel's drag handle. A ghost panel
    // wants the opposite — let the pointer fall through to the canvas entirely.
    <div
      ref={hostRef}
      style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', pointerEvents: ghost ? 'none' : 'all' }}
      onPointerDown={ghost ? undefined : (e) => e.stopPropagation()}
    >
      {/* When fitting either axis the content sizes to itself (measurable);
          otherwise it's a transparent pass-through (display:contents) so existing
          React panels that fill 100% height/width behave exactly as before. For
          width-fit the box is laid out at `max-content` and left-aligned, so its
          scrollWidth is the content's natural width (see the measure effect). */}
      <div
        ref={contentRef}
        style={
          autoH || autoW
            ? {
                flex: '0 0 auto',
                ...(autoW
                  ? { width: 'max-content', maxWidth: 'none', alignSelf: 'flex-start' }
                  : { minWidth: 0 }),
              }
            : { display: 'contents' }
        }
      >
        <Boundary resetKey={Comp}>
          <Comp canvas={canvas} value={streamed} props={userProps} />
        </Boundary>
      </div>
    </div>
  )
}
