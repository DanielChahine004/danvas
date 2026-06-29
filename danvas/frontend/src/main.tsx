import { render } from 'preact'
import '@fontsource-variable/inter' // self-hosted (bundled) UI/canvas font
import { App } from './App'
import './index.css'

// No StrictMode double-invoke concern (that's React-dev-only); Preact mounts
// once. State integrity is handled in Python regardless. render() into #root
// replaces the pre-JS splash markup on first paint.
render(<App />, document.getElementById('root')!)
