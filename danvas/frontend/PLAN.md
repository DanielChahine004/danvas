# danvas frontend rebuild — reconciled build plan

A tldraw-free, drop-in replacement for `danvas/frontend`, built to speak danvas's
existing wire protocol **unchanged**. Python is the source of truth; the frontend
conforms to it. This plan reconciles the original architecture spec against the
*actual* current code in `C:\Users\h\Desktop\danvas`.

---

## 0. What changed vs. the original spec (read this first)

Reading the real sources of truth (`_protocol.py`, `bridge.js`, `canvas.jsx`,
`ReactHost.jsx`, `App.jsx`, `surface.js`, `server.py`, `components/*.py`) surfaced
four facts that materially reshape the spec:

1. **The shape model collapsed to 3 types.** `COMPONENT_TO_SHAPE` in
   [canvas.jsx](../danvas/danvas/frontend/src/canvas.jsx) is now only:
   - `Label → pcLabel` (native DOM)
   - `Custom → pcHtml` (sandboxed iframe)
   - `React → pcReact` (in-browser-compiled React subtree)

   Slider, Toggle, Button, TextField, Markdown, Image, Table, Plot, LivePlot,
   Histogram, VideoFeed, AudioFeed, Chat, FileBrowser, Inspector, etc. **all
   register as `React`** and ship their UI as **React JSX source strings from
   Python** (e.g. `slider.py`'s `_SLIDER_SOURCE`). They are compiled in-browser by
   Sucrase (`jsxRuntime: 'classic'` → `React.createElement`) and mounted by
   `ReactHost`.

2. **Panel content depends on the real React hook API.** Across the Python
   component sources: `React.useState` (36×), `React.useEffect` (21×),
   `React.useRef` (12×), `React.useMemo`, fragments, controlled inputs, and class
   error boundaries (`getDerivedStateFromError`/`componentDidCatch`).

   → The spec's "compile JSX → Solid" pick **breaks the hard constraint**: Solid
   has no `useState`/`useEffect`/`useRef` and compiles JSX to direct DOM ops, not
   `React.createElement`. Running `slider.py`'s source under Solid fails, and
   fixing it would mean rewriting every built-in's JSX *in Python*.

   → **Decision (confirmed with user): Preact + `preact/compat`**, aliased so the
   Python-shipped React JSX runs unchanged. ~4 KB runtime instead of React's
   ~45 KB; the **engine** stays framework-free TypeScript.

3. **Dropping tldraw ≠ rebuilding the panel layer.** `ReactHost`, the `Card`
   chrome, and `App.jsx`'s presence/cursor/toolbar layers are React, **not
   tldraw**. They can be *ported* (swap tldraw's `editor`/`useEditor`/`useValue`
   for the new engine's equivalents). The rebuild concentrates on **engine +
   store + bridge + surface seam + camera input**.

4. **Free-form drawing is opaque to Python.** `draw`/`snapshot`/`load_snapshot`
   currently round-trip **tldraw store diffs** that Python only stores and
   replays. Without tldraw there is no such store. We define our own internal
   drawing/snapshot format; Python doesn't care. **Known incompatibility:**
   free-form drawings saved by the old tldraw build (and old `*.canvas.json`)
   won't load in the new build. Panels/arrows are unaffected — they're recreated
   from Python code every run.

---

## 1. Hard constraint & verification

**Constraint:** `danvas/canvas.py`, `components/`, `server.py`, `bridge.py`,
`_protocol.py` change **zero lines**. The new frontend produces a `dist/` that the
Python server serves verbatim.

**Delivery target (from `server.py`):**
- Static mount serves `os.path.join(__file__/.., "frontend", "dist")` via
  `_FrontendStatic` (precompressed `.br`/`.gz` siblings; `index.html` never
  cached).
- WebSocket at `/ws`. HTTP side-routes: `/__upload__/{token}`,
  `/__download__/{token}`, `/__screenshot__.png`, `/__describe__`, `/__auth__`,
  `/__logout__`, `/__hot_source__`, `/__hot_patch__`.
- `/` route may inject `window.__DANVAS_TLDRAW_LICENSE_KEY__` — **irrelevant to
  us** (no tldraw); we ignore it.

**Test strategy (non-destructive — never clobber the shipped `dist/`):**
- **Dev loop:** Vite dev server with a proxy: `/ws` → `ws://localhost:8000/ws`,
  and `/__*__` HTTP routes → `http://localhost:8000`. Run any
  `examples/*.py` (which call `canvas.serve(port=8000)`) and load the Vite URL.
  The new frontend talks to the real Python backend; their `dist/` is untouched.
- **Acceptance loop:** `vite build` → `my_danvas/dist`, then point the server at
  it without overwriting theirs. Cleanest: a tiny `serve_against.py` test harness
  that monkeypatches `danvas.server.DIST_DIR` to `my_danvas/dist` (or copies into
  a temp package dir). Documented in `my_danvas/README.md` once built.

---

## 2. Architecture (reconciled)

One store, consumers read it; one bridge adapts the wire. The big simplification:
with only 3 shape types and React panels, the "panel renderer" is largely the
**existing React host re-parented onto the new engine**.

```
 Python ──wire(JSON/binary)──► BRIDGE ──put/patch/remove(source:'remote')──► STORE
                              ◄──frames(from source:'local' changes)──┘   │  (signals)
                                                                          │
   CAMERA (one signal) ─────────────────────────────────────────────────┤
                                                                          ├─► PANEL RENDERER  (Preact: Card + ReactHost + CustomView, camera-transformed DOM)
   ENGINE  ── camera / pointer / hit-test(rbush) / select / move-resize / z ─ reads+writes STORE (source:'local')
                                                                          ├─► DRAWING RENDERER (Canvas2D; drawings + arrows)   [later milestone]
                                                                          └─► OVERLAYS (presence/cursors, toolbar buttons — ported from App.jsx)
```

**Reuse map:**

| Old file | Disposition |
|---|---|
| `ReactHost.jsx` | **Port** → Preact. Replace `useEditor`/`useValue`/`editor.*` with engine hooks. Keep compile/libs/wasm/onFrame/viewport/fit logic verbatim. |
| `canvas.jsx` (`Card`, `CardLabel`, `CustomView`, `*Panel`) | **Port** → Preact. Drop `BaseBoxShapeUtil`/`HTMLContainer`/`T`; the shape *props* (`getDefaultProps`) become record defaults in the store. Card chrome/locks/grab logic kept. |
| `App.jsx` presence/cursor/toolbar | **Port** → Preact, swap camera reads. |
| `App.jsx` `enableRightDragPan` / `enableSmartScroll` / `KioskHandTool` | **Reimplement** as engine camera-input (exact math reused). |
| `surface.js` | **Reimplement** as the engine's public seam (expanded — see §5). |
| `bridge.js` | **Rewrite** against `store` + `surface` only (no `tldraw` import). Logic preserved frame-for-frame. |
| `theme.css` / `index.css` | **Reuse** (panel theme vars). Drop `tldraw/tldraw.css`. |
| `protocol.generated.js` | **Reuse as-is** (it's generated from `_protocol.py`; the contract). |

---

## 3. Tech stack (reconciled & locked)

- **Engine core:** TypeScript, **alien-signals** (reactivity), **@use-gesture/vanilla**
  (pointer/camera input), **rbush** (spatial index for hit-test + culling). No UI
  framework in the engine.
- **Store/sync/undo:** **alien-signals** records for v1 (the `Store` interface is
  stable); **Yjs** swap is a later milestone (origins = source tag; y-undo for
  local-only undo). Interface designed so the swap touches only `store/`.
- **Panel content:** **Preact + `preact/compat`** (aliased to `react`/`react-dom`
  in Vite `resolve.alias` + the esm.sh `?external=react` import resolution).
  Sucrase for in-browser JSX (kept). Plotly/Monaco via their JS libs (kept).
- **Drawing:** perfect-freehand + Canvas2D (v1) → PixiJS later. **Deferred.**
- **Export:** **modern-screenshot** (`domToPng`) over the panel container.
- **Build:** Vite. `base: './'`, `outDir: dist`, keep `precompress.mjs` so
  `_FrontendStatic` finds `.br`/`.gz`.

---

## 4. Module layout & contracts

```
my_danvas/
  index.html                      # splash + #root (ported)
  vite.config.ts                  # base './', react→preact alias, /ws + /__*__ proxy
  package.json
  scripts/precompress.mjs         # ported (gzip/brotli dist assets)
  src/
    main.tsx                      # mount <App/> (preact/compat createRoot)
    protocol.generated.js         # copied from danvas (generated contract)
    engine/
      types.ts                    # Id, Record types, Camera, Change, Source
      store.ts                    # Store (signals-backed) — §6
      camera.ts                   # camera signal + screenToPage/pageToScreen + math
      input.ts                    # @use-gesture: right-drag pan, smart-scroll zoom/pan, wheel
      hittest.ts                  # rbush index; getShapeAtPoint, viewport query (culling)
      zorder.ts                   # fractional index helpers (toFront/back/forward/backward)
      selection.ts                # selection + move/resize/rotate gestures → store(local)
      instance.ts                 # darkMode/readOnly/grid/tool/hovered/selected signals
      surface.ts                  # THE seam (§5): camera/shapes/zorder/export/bindings/instance/onChange/container
      bindings.ts                 # arrow↔panel bindings + arrow re-route geometry
      export.ts                   # toImage (modern-screenshot), getContent/putContent (drawings)
    react/                        # Preact panel layer (ported)
      EngineContext.tsx           # useEditor()/useValue() shims over the engine signals
      Card.tsx                    # Card, CardLabel, DragHandle, lock/grab/ghost logic
      panels.tsx                  # LabelPanel, HtmlPanel(CustomView), ReactPanel registry
      ReactHost.tsx               # ported compile/mount host (Preact)
      PanelLayer.tsx              # camera-transformed container; per-record mount + culling
      overlays/                   # PresenceBadge, CursorLayer, Inspector/Graveyard/SignOut buttons
    bridge.ts                     # wire adapter — §7 (talks to store+surface only)
    theme.css, index.css          # ported
```

**Core contracts (TypeScript):**

```ts
type Source = 'local' | 'remote'
type Id = string
type RecordType = 'panel' | 'drawing' | 'arrow' | 'binding'

interface Store {
  get(id: Id): Record | undefined
  ids(type?: RecordType): Iterable<Id>
  transact(source: Source, fn: () => void): void   // batches; tags origin
  put(rec: Record): void                            // inside transact
  patch(id: Id, partial: Partial<Record>): void     // inside transact
  remove(id: Id): void                              // inside transact
  subscribe(cb: (changes: Change[], source: Source) => void): () => void
  snapshot(): Snapshot; load(s: Snapshot): void
  camera: Signal<{ x: number; y: number; z: number }>
  instance: Signal<InstanceState>
}

// Surface = the expanded seam bridge.ts + panels talk to. Full mapping in §5.
interface Surface {
  camera:   { get; set; setOptions; zoomLevel; viewportScreenBounds; viewportPageBounds; currentPageBounds; screenToPage; pageToScreen }
  shapes:   { get; create; update; delete; pageIds }
  zorder:   { toFront; toBack; forward; backward }
  export:   { toImage; getContent; putContent }
  bindings: { create; remove }
  instance: { update; get; isDark; setColorScheme }
  interaction: { tool; setTool; hoveredId; selectedIds; shapeAtPoint }
  remote(fn): void          // mergeRemoteChanges equivalent (read-only lift + remote tag)
  applyDiff(diff): void     // free-form drawing diff (remote)
  onChange(cb): () => void  // emits { source, changes }
  onUserDelete(cb); onUserGeometry(cb); onUserDraw(cb)   // sideEffect equivalents
  container(): HTMLElement
}
```

---

## 5. The Surface seam — full tldraw → engine mapping

Every direct tldraw touch found across the 5 files, with its new home. This table
is the contract that lets `bridge.ts` and the panel layer never import an engine
type directly.

| tldraw call (old) | Used by | New home |
|---|---|---|
| `getCamera` / `setCamera` | surface, App pan/zoom | `camera.get/set` (writes `store.camera` signal; `force`/`immediate` honored) |
| `setCameraOptions` | surface (zoom steps, constraints) | `camera.setOptions` (zoom limits, scroll-mode constraints) |
| `getZoomLevel` | surface, ReactHost | `camera.zoomLevel` = `store.camera.z` |
| `getViewportScreenBounds` | fit math | `camera.viewportScreenBounds` (container rect) |
| `getViewportPageBounds` | fit, inspector centre, ui toggle | `camera.viewportPageBounds` (derived from camera+container) |
| `getCurrentPageBounds` | initial auto-fit | `camera.currentPageBounds` (rbush union of all records) |
| `screenToPage` / `pageToScreen` | cursors, hit routing | `camera.screenToPage` / `pageToScreen` (pure math) |
| `getShape` | everywhere | `store.get(shapeId)` (returns a `panel`/`drawing`/`arrow` record) |
| `createShape` / `updateShape` / `deleteShape` | CRUD | `store.put/patch/remove` inside `transact` |
| `getCurrentPageShapeIds` | snapshot/image | `store.ids()` |
| `bringToFront`/`sendToBack`/`bringForward`/`sendBackward` | order frame | `zorder.*` (fractional-index reorder) |
| `toImage` | screenshot | `export.toImage` (modern-screenshot of panel layer, framed to bounds) |
| `getContentFromCurrentPage`/`putContentOntoCurrentPage` | snapshot load/save | `export.getContent`/`putContent` (our drawing format) |
| `getContainer` | cursor reporting, input | `surface.container()` |
| `store.mergeRemoteChanges(fn)` | `applyRemote` echo-suppression | `surface.remote(fn)` = `store.transact('remote', fn)` + readonly lift |
| `store.listen(cb,{source,scope})` | draw-sync, graveyard-sync, viewport | `surface.onChange`/`onUserDraw`/`onUserDelete` (source-filtered) |
| `store.applyDiff(diff)` | applyDraw | `surface.applyDiff` (remote) |
| `sideEffects.registerAfterChangeHandler('shape')` | geometry read-back | `surface.onUserGeometry` (move/resize/rotate, source:'local' only) |
| `sideEffects.registerBeforeChangeHandler('instance_page_state')` | noGrab selection filter | `selection.ts` filter (drop noGrab from hover/select) |
| `createBindings` / `createBindingId` | arrow↔panel | `bindings.create` |
| `getInstanceState().isReadonly` / `updateInstanceState` | readonly lift, grid | `instance.get`/`instance.update` |
| `user.getIsDarkMode` / `user.updateUserPreferences({colorScheme})` | theme | `instance.isDark` / `instance.setColorScheme` (persist to localStorage) |
| `getCurrentToolId` / `setCurrentTool` | Card pointer routing, kiosk hand | `interaction.tool` / `setTool` |
| `getHoveredShapeId` / `getSelectedShapeIds` | Card chrome | `interaction.hoveredId` / `selectedIds` |
| `getShapeAtPoint(pt,{hitInside})` | drawingOnTop routing | `interaction.shapeAtPoint` (rbush + geometry) |
| `createShapeId(id)` | id mint | `\`shape:${id}\`` string (same scheme; Python-predictable) |
| `useEditor()` / `useValue(key, fn, deps)` | Card/ReactHost/App reactivity | `EngineContext` shims: `useEditor()` returns the surface; `useValue` subscribes a signal selector |
| `BaseBoxShapeUtil` / `HTMLContainer` / `T` | shape classes | Removed. Props schema → store record defaults; `HTMLContainer` → a plain positioned `<div>` in `PanelLayer`. |

**`createShapeId` scheme:** Python mints record ids (uuid/name). The frontend
key is `shape:<id>` (preserved so `componentIdOf` and all of bridge.js's id math
work unchanged). Store accepts external ids; never mints ids Python can't predict.

---

## 6. Store model

Records (all carry `id`, `typeName`, z-`index`):

- **panel** — `{ typeName:'panel', id, shapeType:'pcLabel'|'pcHtml'|'pcReact',
  x, y, rotation, opacity, isLocked, index, props:{...}, meta:{ lockMove,
  lockResize, lockInput, noGrab, noFrame, frameColor } }`. `props` per type =
  the old `getDefaultProps` (Label: `{w,h,label,value}`; Html:
  `{w,h,label,html,themed,permissions}`; React:
  `{w,h,label,source,data,css,autoH,autoW,libs,wasm}`).
- **drawing** — managed `shape` frames (geo/text/note/line/draw/frame/highlight)
  + free-form ink. `{ typeName:'drawing', id, kind, x, y, rotation, opacity,
  index, props }`. *(drawing-layer milestone)*
- **arrow** — `{ typeName:'arrow', id, props, index }` + two **binding** records.
- **binding** — `{ typeName:'binding', id, fromId, toId, props:{ terminal,
  normalizedAnchor, isExact, isPrecise } }`.

**Source tagging (the #1 correctness rule):**
- `transact('remote', …)` → apply + render, **do not** emit to Python, **do not**
  enter undo. (Bridge's inbound path + `surface.remote`.)
- `transact('local', …)` → apply + render + emit layout/draw/graveyard to Python
  + enter undo. (Engine gestures.)
- `store.subscribe` delivers `(changes, source)`; bridge read-back handlers ignore
  `source==='remote'` exactly as `sideEffects … source !== 'user'` does today.
- Read-only **lift** preserved: `surface.remote` clears `isReadonly` for the batch
  so Python-driven updates render under `view={read_only:True}`, then restores it.

`instance` signal: `{ darkMode, readOnly, gridOn, lockedCamera, zoomLimits, tool,
hoveredId, selectedIds }`.

---

## 7. Bridge protocol catalogue

Full vocabulary from `_protocol.py` + `bridge.js` `handle()`/`sendRaw`. Each
inbound frame → store mutation (`remote`) or engine call; each `local` store
change → outbound frame. **Authoritative field shapes are in `bridge.js`.**

### Outbound (Python → browser) — `handle(msg)`
| type | fields | action |
|---|---|---|
| `welcome` | runId, reload, you, uiInspector, uiGraveyard, auth, cursors, view | run-change clears managed shapes; set identity/flags/view |
| `register` | id, component, props, x?, y?, rotation?, opacity?, locked?, movable?, resizable?, interactive?, selectable?, frame?, frameColor? | create panel record; auto-place (masonry) if no x/y; pin via `layout {auto:true}`; schedule initial fit |
| `update` | id, payload | live channels first (`plot`/`plot_extend`/`post_style`/`post` → liveHandlers, bypass store), else patch panel x/y/rot/opacity/locks/props |
| `remove` | id | delete record + teardown live wiring/captures |
| `order` | id, op(front/back/forward/backward) | `zorder.*` (remote) |
| `arrow` | id, start, end, props | create arrow record + 2 bindings (remote) |
| `shape` | id, shapeType, x, y, rotation, opacity, props | create drawing record (remote) |
| `shape_update` | id, x?, y?, rotation?, opacity?, props? | patch drawing record |
| `container_sync` | key, members, mode, gap, w?, h?, x0?, y0?, padding?, fill_w? | store container spec; auto-repack tree |
| `reflow` | ids, kind, x0, y0, gap, key | one-shot group repack + armed re-pack |
| `get_snapshot` | reqId, panelIds | reply `snapshot {reqId, data}` = user (non-panel) content |
| `load_snapshot` | data | additive `putContent` (remote); schedule fit |
| `get_image` | reqId, shapeIds | render → reply `image {reqId, data(b64)|null, error?}` |
| `draw` | diff | `applyDiff` (remote) |
| `presence` | count, viewers | presence badge + roster |
| `cursor` | id, x, y, color, name | peer cursor upsert |
| `cursor_gone` | id | peer cursor remove |
| `view` | view | live camera/instance/nav config merge |
| `graveyard_update` | items | graveyard list |
| `shared` | components, styles | shared React sources + global `<style>` |
| `chat` | (entry) | chat log push |
| `response` | reqId, result, error | resolve `canvas.request` promise |

### Inbound (browser → Python) — `sendRaw`
| type | fields | emitted when |
|---|---|---|
| `heartbeat` | — | every 10 s |
| `layout` | id, x?, y?, rotation?, w?, h?, auto? | user move/resize/rotate; auto-place pin; content-fit; container repack |
| `input` | id, payload | panel control change (`canvas.send`) |
| `request` | id, reqId, data | `canvas.request` (→ `response`) |
| `draw` | diff | user free-form edit (non-managed records) |
| `graveyard` | id | user deletes a managed shape |
| `restore` | id | Graveyard "Restore" |
| `cursor` | x, y | local pointer (page coords), if `cursors` enabled |
| `set_name` | name | rename |
| `chat` | text | chat send |
| `ui` | action:'toggle_inspector', center | inspector toolbar button (sends viewport centre) |
| `panel_error` | id, message | panel JS/compile error |
| `snapshot` | reqId, data | reply to `get_snapshot` |
| `image` | reqId, data, error? | reply to `get_image` |

### Binary frames `[type][idLen][id bytes][payload]`
- **In→browser:** `BIN_VIDEO`(1)/`BIN_AUDIO`(2)/`BIN_CUSTOM`(3)/`BIN_REACT`(4) →
  `liveHandlers.get(id)(payload)`.
- **Browser→Python:** `BIN_INPUT`(5) via `sendBinary` (canvas.sendBinary /
  camera / mic capture).

### iframe ↔ parent `postMessage` (Custom panel host, not wire)
`__danvas` (input), `__danvas_binary`, `__danvas_wheel` (zoom canvas from iframe),
`__danvas_fit` (h/w auto), `__danvas_camera`, `__danvas_mic`. Ported into the
Custom panel host + a window message listener.

---

## 8. Policy decisions (reconciled)

1. **Coordinate system.** One page space; one camera `{x,y,z}`. Apply as a single
   `translate(...) scale(z)` on the panel container; `screenToPage`/`pageToScreen`
   pure-math. Never reposition panels individually on pan/zoom.
2. **Echo suppression.** As §6. The single most important rule; enforced in
   `surface.remote` + the `local`-only read-back handlers.
3. **Z-band (the one compromise).** v1 = **drawings always below panels** (option
   a). Drawing renderer pluggable so a 3-layer `zBand` (below/panels/above) can
   land later without engine changes. Panels interleave among themselves by
   fractional `index`.
4. **Pointer routing.** Port the `drawingOnTop` + `stopPropagation` policy from
   `canvas.jsx`/`ReactHost.jsx` onto `interaction.shapeAtPoint`. **Note:** with no
   tldraw canvas *under* the panels, "drawing on top" only matters once the
   drawing layer exists; until then panels claim the pointer (select tool) or pass
   through (lock/ghost/non-select tool) exactly as today.
5. **Culling.** Panels: `content-visibility:auto` + IntersectionObserver; unmount
   content far outside viewport. Drawings: rbush viewport query.
6. **IDs.** External, Python-minted. `shape:<id>` key scheme preserved.
7. **Camera input.** Reimplement `enableRightDragPan` + `enableSmartScroll`
   (mouse-wheel→zoom-to-cursor, trackpad→eased pan, ctrl→pinch-zoom) with the
   exact constants/formulas from `App.jsx`. Scroll-mode wheel pan from `bridge.js`.

---

## 9. Milestones (revised) + acceptance

> **Re-scoped because of the consolidation:** `hello_world.py` uses a **Slider
> (React panel)** + a Label. So even the first milestone needs a minimal
> Preact `ReactHost` to render the slider — the spec's "panels only, no React"
> M1 is no longer possible. Adjusted below.

- **M0 — Scaffold.** Vite + TS + Preact alias; ported `index.html`/`theme.css`;
  `protocol.generated.js`; Vite proxy to a running danvas server; empty `App`.
  *Test:* dev server boots, proxies `/ws`.

- **M1 — Bridge + Store + Label + minimal ReactHost.** Signals store; bridge
  inbound `welcome`/`register`/`update`/`remove`; `PanelLayer` renders records at
  store coords (no camera yet); `LabelPanel`; ported `ReactHost` (compile+mount,
  `canvas.send`/`value`/`props`, live `post` channel). **✅ when
  `examples/hello_world.py` shows the slider + label and the label updates when
  the slider moves** (input→Python→update round-trip).

- **M2 — Camera + culling.** Camera signal + container transform;
  `screenToPage`; pan/zoom input (ported math); initial auto-fit; masonry
  auto-place + `container_sync`/`reflow` repack; content-fit (`fitNative`,
  `__danvas_fit`); IntersectionObserver culling. *Test:* panel tour example pans/
  zooms at 60 fps with offscreen panels unmounted.

- **M3 — Manipulation + layout frames.** Selection, move/resize/rotate, z-order
  → `layout`/`order` (source-tagged). noGrab/lock/grab/ghost pointer policy.
  **✅ when dragging a panel persists via `serve(persist=)`** and Python sees one
  `layout`, not a feedback storm.

- **M4 — Surface API parity.** Full seam: `get_image` (screenshot via
  modern-screenshot), `view`/`set_view` camera + instance (read_only/grid/nav/
  zoom limits), `get_snapshot`/`load_snapshot`. *Test:* `canvas.screenshot()`
  yields a correct PNG; `set_view` pans/zooms.

- **M5 — Presence / cursors / chrome.** Port `PresenceBadge`, `CursorLayer`,
  Inspector/Graveyard/SignOut buttons, kiosk hand tool; cursor reporting +
  peer cursors; `graveyard`/`restore`. *Test:* two browsers show each other's
  cursors; delete→graveyard→restore works.

- **M6 — Custom (iframe) panels.** Port `CustomView` + all `postMessage`
  channels (push, binary, wheel-zoom, fit, camera, mic). *Test:*
  `examples` with a `custom()`/`webview()` panel work, incl. `push`/`sendBinary`.

- **M7 — Yjs swap.** Move `Store` onto Yjs (origins = source); y-undo local-only;
  re-verify echo suppression. Interface-stable, so only `store/` changes.

- **M8 — Drawing layer (scoped).** perfect-freehand + Canvas2D: managed `shape`
  records (rect/text/line/frame) + free-form ink; `draw`/`snapshot` round-trip
  (our format); arrows + bindings re-route. Defer snapping/guides/multi-transform.

**Global acceptance gate:** the existing Python examples run **unchanged**
(`hello_world`, `frontend_backend_tour`, panel tour, persistence, arrows, an
inspector); no echo loops; 60 fps / ~100 panels with offscreen unmounting;
`screenshot()` correct.

---

## 10. Risks & open questions

- **Preact ↔ esm.sh libs.** `scope=[...]` pulls framer-motion/lucide from esm.sh
  with `?external=react`. Must resolve bare `react`/`react-dom` → `preact/compat`
  at runtime (import map / esm.sh `?alias`). Validate early with a `scope` panel.
- **Plotly under Preact.** Plotly is imperative (its own DOM), so compat risk is
  low, but `LivePlot.extendTraces`/onFrame path must be exercised (M1/M6).
- **`useValue` semantics.** tldraw's `useValue` is a memoised reactive selector;
  the shim must re-render only when the selected value changes (the Inspector
  viewport read depends on this). Build it on alien-signals `effect` + Preact
  `useState`.
- **Screenshot fidelity.** modern-screenshot can't capture cross-origin iframes
  (`Custom`/`WebView`) or mid-stream video — same limitation the README already
  documents for the tldraw build. Acceptable.
- **Drawing format break.** Old `*.canvas.json` free-form saves won't load
  (panels/arrows unaffected). Confirm this is acceptable for v1. *(Assumed yes.)*
- **Fractional indexing.** Need a z-index scheme matching Python's
  front/back/forward/backward expectations; use the `fractional-indexing` lib.

---

### Immediate next step (on approval)
Build **M0 + M1**: scaffold the Vite/Preact/TS project, port `index.html`/
`theme.css`/`protocol.generated.js`, stand up the signals `Store` + `bridge.ts`
(`welcome`/`register`/`update`/`remove`) + `PanelLayer` + `LabelPanel` + a minimal
Preact `ReactHost`, and drive it with `examples/hello_world.py` through the Vite
proxy.
