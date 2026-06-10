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
//   canvas  - { send(data) }            : panel -> Python (routed to @on handlers)
//   value   - the latest push()ed data  : Python -> panel, no prop churn / reload
//   props   - the dict from update()/props=  : Python -> panel, replayed on reconnect
// React (with hooks) is in scope as `React`.
import * as Babel from '@babel/standalone'
import React from 'react'
import { sendInput, registerLive, unregisterLive, componentIdOf } from './bridge'

// Compile + evaluate a source string into a component function, memoised so a
// re-render (or many panels sharing one source) compiles only once. The source
// is transformed from JSX, then evaluated with `React` in scope; it must define
// a function named `Component`.
const cache = new Map() // source -> { Comp } | { error }

function build(source) {
  if (cache.has(source)) return cache.get(source)
  let entry
  try {
    const code = Babel.transform(source, {
      presets: [['react', { runtime: 'classic' }]],
    }).code
    // eslint-disable-next-line no-new-func
    const factory = new Function('React', `${code}\n; return Component;`)
    const Comp = factory(React)
    if (typeof Comp !== 'function') {
      throw new Error('source must define a function named `Component`')
    }
    entry = { Comp }
  } catch (error) {
    entry = { error }
  }
  cache.set(source, entry)
  return entry
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

  React.useEffect(() => {
    const onPush = (data) => setStreamed(data)
    registerLive(id, onPush)
    return () => unregisterLive(id)
  }, [id])

  // Stable bridge handle so the user component can post back to Python.
  const canvas = React.useMemo(() => ({ send: (data) => sendInput(id, data) }), [id])

  // Props from Python (update()/initial props=), carried as a JSON string prop so
  // they persist in the shape and replay on reconnect.
  let userProps = {}
  try {
    userProps = JSON.parse(shape.props.data || '{}')
  } catch {
    userProps = {}
  }

  const entry = build(shape.props.source || '')
  if (entry.error) return <ErrorBox error={entry.error} />
  const Comp = entry.Comp
  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <Boundary resetKey={Comp}>
        <Comp canvas={canvas} value={streamed} props={userProps} />
      </Boundary>
    </div>
  )
}
