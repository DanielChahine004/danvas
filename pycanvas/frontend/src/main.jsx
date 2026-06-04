import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'

// No StrictMode: it double-invokes onMount in dev, which would open the
// WebSocket twice. State integrity is handled in Python regardless.
ReactDOM.createRoot(document.getElementById('root')).render(<App />)
