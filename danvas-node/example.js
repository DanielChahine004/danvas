// A danvas canvas from Node — the hello-world of danvas-node. Start a hub
// (danvasd) and dial in:
//
//   danvasd --port 8000            # or: any Python canvas.serve()
//   node danvas-node/example.js 8000
//
// A slider drives a label from a Node handler; a button asks Node for the
// time; a live plot streams a sine wave. Panels chain below one another via
// the register frame's `rel` (the hub's frontend places and re-settles them).

import { connect } from './index.js'

const port = process.argv[2] || '8000'
const c = await connect(`127.0.0.1:${port}`, 'node-demo')

c.registerTemplate('title', 'label', {
  data: { text: 'Hello from Node.js' }, x: 80, y: 80,
})
c.registerTemplate('speed', 'slider', {
  data: { min: 0, max: 100, value: 25 },
  rel: { kind: 'below', anchor: 'title', gap: 16 },
})
c.onInput('speed', (p) => {
  c.update('title', 'post', `speed set to ${Math.round(p.value)} from the browser`)
})

c.registerTemplate('clock', 'button', {
  props: {}, label: 'What time is it?',
  rel: { kind: 'below', anchor: 'speed', gap: 16 },
})
c.onInput('clock', () => {
  c.update('title', 'post', `Node says it's ${new Date().toLocaleTimeString()}`)
})

c.registerTemplate('wave', 'live_plot', {
  rel: { kind: 'below', anchor: 'clock', gap: 16 },
})
let t = 0
const fig = { data: [{ x: [], y: [], name: 'sine', mode: 'lines', type: 'scatter' }],
              layout: { margin: { l: 40, r: 15, t: 15, b: 30 } } }
setInterval(() => {
  t += 0.1
  fig.data[0].x.push(t)
  fig.data[0].y.push(Math.sin(t))
  if (fig.data[0].x.length > 200) { fig.data[0].x.shift(); fig.data[0].y.shift() }
  c.update('wave', 'plot', fig)
}, 100)

console.log(`node demo live — open http://127.0.0.1:${port}`)
