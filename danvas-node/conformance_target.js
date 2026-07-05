// The Node target for the source-SDK conformance suite
// (tests/test_sdk_conformance.py; the normative behavior table lives in
// tests/sdk_conformance_target.py). Run as:
//
//   node danvas-node/conformance_target.js <broker_port>

import { connect } from './index.js'

const port = process.argv[2]
if (!port) {
  console.error('usage: node conformance_target.js <port>')
  process.exit(2)
}

const c = await connect(`127.0.0.1:${port}`, 'ctn')

c.registerTemplate('lbl', 'label', { data: { text: 'hello' }, x: 10, y: 10 })

c.registerTemplate('sld', 'slider',
                   { data: { min: 0, max: 100, value: 10 }, x: 10, y: 110 })
c.onInput('sld', (p) => {
  c.update('lbl', 'post', `v=${Math.round(p.value ?? 0)}`)
})

c.registerTemplate('ask', 'button', { x: 10, y: 210 })
c.onRequest('ask', (data) => ({ pong: ((data && data.ping) || 0) + 1 }))

c.registerTemplate('dl', 'download', { x: 10, y: 310 })
c.onDownload('dl', () => ['hello.txt',
                          new TextEncoder().encode('conformance-bytes\n')])

c.registerTemplate('up', 'upload', { x: 10, y: 410 })
c.onUpload('up', (f) => c.update('lbl', 'post', `up=${f.name}:${f.size}`))

c.register('bin', 'Custom', { html: '<b>bin</b>', w: 240, h: 160 },
           { x: 10, y: 510 })
c.onBinary('bin', (bytes) => {
  c.update('lbl', 'post', `bin=${bytes.length}`)
  c.sendMedia(3, 'bin', bytes) // CUSTOM: echo the bytes back
})

c.registerTemplate('cam', 'video', { x: 10, y: 610 })
c.registerTemplate('ctl', 'button', { x: 10, y: 710 })
c.onInput('ctl', () => {
  const jpeg = new Uint8Array([0xff, 0xd8,
    ...new TextEncoder().encode('conformance-jpeg')])
  c.sendVideo('cam', jpeg)
})

console.log(`[ctn] conformance target live on :${port}`)
// keep the process alive (the socket + heartbeat timer already do)
