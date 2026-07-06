# danvas-node

The Node SDK for danvas: dial into a running canvas (`danvasd` or any hub) as
a **source** — register native panels, stream state, react to what browsers
do, serve downloads and receive uploads. Zero dependencies: Node ≥ 22's own
WebSocket is the transport, and the event loop is the ordered dispatcher.

```js
import { serve } from 'danvas-node'

// The default move: own the canvas — spawn danvasd on the port (or attach to
// one already serving it), dial in, open the browser. Use connect(url) when
// the canvas is served elsewhere.
const c = await serve(8000, 'telemetry')
c.registerTemplate('temp', 'slider', {
  data: { min: 0, max: 100, value: 20 }, x: 40, y: 40,
})
c.onInput('temp', (p) => console.log('browser set', p.value))
setInterval(() => c.update('temp', 'post', readSensor()), 500)
```

This SDK is also the polyglot architecture's proof: it was written against
[`PROTOCOL.md`](../PROTOCOL.md) and the per-template `contract` blocks in
[`components.json`](../danvas/templates/components.json) — not against the
Python or Rust source — and it passes the full source-SDK conformance suite:

```
pytest tests/test_sdk_conformance.py -k node
```

Covered: template registration (incl. `rel` relative placement), update
streaming, input/layout/request handling, replay-on-reconnect with folded
browser layouts, binary media envelopes in and out, and the file-transfer
dance (serveBytes/onDownload, uploadEndpoint/onUpload) with the decline-fast
rule. `conformance_target.js` is the executable behavior script the suite
drives.
