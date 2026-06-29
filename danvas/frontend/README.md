# my_danvas — a tldraw-free danvas frontend

A drop-in replacement for `danvas/frontend`, rebuilt without tldraw. It speaks
danvas's existing wire protocol **unchanged**, so the Python package (`Canvas`,
`components/`, `server.py`, `bridge.py`) works with zero changes.

- **Engine:** framework-free TypeScript — a signals-backed store
  ([alien-signals](https://github.com/stackblitz/alien-signals)), a camera, and a
  bridge that adapts the WebSocket protocol.
- **Panel content:** [Preact](https://preactjs.com) + `preact/compat`, aliased to
  `react`/`react-dom` so the React JSX that danvas ships from Python (Slider,
  Plot, Table, …) compiles and runs **unchanged** (Sucrase, in-browser). The old
  build's `ReactHost` / `Card` are ported, not rewritten.

See [PLAN.md](PLAN.md) for the full reconciled architecture and milestone plan.

## Status

| Milestone | State |
|---|---|
| M0 — scaffold (Vite + TS + Preact, proxy) | ✅ |
| M1 — store + bridge (welcome/register/update/remove) + ReactHost + panel layer | ✅ verified against `examples/hello_world.py` |
| M2 — camera (pan/zoom/fit/scroll-modes) + masonry/container/reflow layout + culling | ✅ verified against a 48-panel grid |
| M3 — select / move / resize + z-order + geometry read-back (one `layout`, no echo) | ✅ verified (move/resize/autoH-lock/z-order) |
| M4 — screenshot (`get_image`) + `set_view` + `get_snapshot`/`load_snapshot` | ✅ screenshot verified via `/__screenshot__.png` (framed to bounds) |
| M5 — presence + peer cursors + delete/graveyard/restore + Inspector/SignOut chrome + kiosk hand | ✅ verified (cursors, graveyard, inspector) |
| M6 — Custom (iframe) panels + postMessage channels (send/push/binary/fit/wheel/camera/mic) | ✅ verified (custom() send+push round-trip) |
| M7 — ~~Yjs swap~~ → local undo/redo (move/resize/delete, Ctrl+Z/Y) | ✅ verified (coalesced drag, Python informed) |
| M8 — drawing layer: Python-managed shapes (geo/text/line/frame) + connector arrows (reroute on move) | ✅ verified (SVG render + arrow reroute) |

(M7 reconciled: danvas is server-relayed, not P2P-CRDT, so Yjs's merge is never
exercised by the protocol — it would only have bought undo/redo. Delivered that
directly on the existing store; the `Store` interface stays Yjs-ready.)

**M8 scope:** Python-created shapes (`canvas.geo`/`text`/`line`/`frame`) and
connector arrows (`canvas.connect`) render on an SVG layer below the panels and
reroute as their endpoints move. **Deferred** (need a toolbar + perfect-freehand,
and aren't used by the acceptance examples): user-drawn ink, drawing *tools*,
peer `draw`-sync, and free-form snapshot content.

**Known upstream quirk:** the Inspector toolbar button never shows its open/closed
(✕) state — Python assigns panels UUID ids, but the frontend (both this build and
the original tldraw one) checks for the name `__ui_inspector__`, which never
appears on the wire. The toggle itself works; only the button indicator is inert.
A Python-side fix (send the reserved name, or id-by-name) would let it reflect state.

## Acceptance & performance

Acceptance was run with the **real danvas server serving this build** (monkeypatch
`danvas.server.DIST_DIR` → `my_danvas/dist`), examples **unchanged**. All pass with
**zero console errors and zero panel error-boxes**:

| Example | Rendered |
|---|---|
| `hello_world` | slider + label, live round-trip ✅ |
| `container_layout` | 12 panels, nested layout ✅ |
| `custom_html` | Custom iframe panel ✅ |
| `react_component` | 5 React panels ✅ |
| `plotly_panel` | interactive Plotly chart ✅ |
| `show_anything` | 25 panels (tables/images/plots/json/iframes) ✅ |
| `catalogue` | full component catalogue: 20 panels incl. 6 Plotly charts, tables, image, WebView ✅ |
| `action_routing` | event routing ✅ |

**Performance vs the original tldraw build** (same 100/200-panel backend; old =
shipped dist, new = this build):

| Metric | OLD (tldraw) | NEW (Preact) |
|---|---|---|
| Initial JS transferred | **459 KB** | **68 KB** (~6.7× smaller) |
| Time-to-first-card (100 panels, localhost) | 1035 ms | 1114 ms |
| Zoom FPS @ 100 panels | 60 | 61 |
| Zoom @ 200 panels (avg fps / p95 frame / jank) | 60 / 17 ms / 0 | 60 / 17 ms / 0 |

- **Bundle is the dominant win** (~6.7× less initial JS; plotly is lazy in both) —
  decisive over a real network / tunnel where download time matters.
- **Runtime smoothness is identical** — camera zoom is GPU-composited on both, so
  both hold a locked 60 fps with no jank even at 200 panels.
- **Trade-off:** at very high panel counts the new build's first paint is a bit
  slower (1.57 s vs 1.14 s @ 200 panels) — the content-fit + masonry relayout for
  many auto-sized panels costs some mount time. Not load-bound on localhost.

## Develop & test (non-destructive)

The dev loop points this frontend at a **running danvas Python server** via a Vite
proxy (`/ws` and `/__*__` → `localhost:8000`). The shipped
`danvas/frontend/dist` is never touched.

```bash
npm install

# 1) start any danvas example on port 8000 (no tunnel needed), e.g.:
#    python -c "import danvas; c=danvas.Canvas(); c.slider('s'); c.serve(port=8000, tunnel=False)"
# 2) start the dev server:
npm run dev          # http://localhost:5173

# production build (emits dist/ + .gz/.br, ready to drop into danvas/frontend/dist)
npm run build
npm run typecheck
```

### Automated end-to-end check

`scripts/verify-e2e.mjs` drives headless Chrome against the dev server + a running
backend and asserts the panels render and the input→Python→update round-trip
closes. It needs `puppeteer-core` (not a saved dependency):

```bash
npm install puppeteer-core --no-save
# with the danvas backend on :8000 and `npm run dev` running:
node scripts/verify-e2e.mjs
```

## Acceptance gate

danvas's existing Python examples must run **unchanged** against this frontend.
M1 clears the first one: `examples/hello_world.py` renders its slider + label and
the label updates live as the slider moves.
