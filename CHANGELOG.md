# Changelog

All notable changes to PyCanvas are recorded here. This project aims to follow
[Semantic Versioning](https://semver.org/); while pre-1.0, minor versions may
carry breaking changes (called out below).

## Unreleased

### Added
- **Stale tabs heal on reconnect.** Every server run is stamped with a run id
  (sent in the WebSocket `welcome` frame). A browser tab left open from an
  earlier run reconnects, sees the run change, and clears that run's dead
  panels before the new run's are replayed — so re-running a script no longer
  leaves stale, unresponsive duplicates stacked on top of the live panels.
  (Previously this cleanup only happened under `hot_reload=True`.)
- **Wire debugging.** `serve(debug=True)` logs every WebSocket frame to the
  console (`->` Python→browser, `<-` browser→Python) with component names
  resolved. Programmatic equivalent: `canvas.on_frame(fn)` /
  `canvas.off_frame(fn)` — a decorator-friendly observer called as
  `fn(direction, msg)` for every frame (binary media as a small summary,
  heartbeats skipped). Taps are reentrancy-guarded, so a tap may drive panels
  without looping back into itself. A connection line — `viewer 'X' connected
  (replayed N panels, M arrows)` / `disconnected` — is always printed.
- **Auto height.** Custom-based panels (`markdown`, `custom`, `table`,
  `image`, `label`, …) accept `h="auto"`: the panel's height fits its rendered
  content, re-fits when the content reflows (narrowing the panel, `update()`),
  and the fitted height is reported back so `comp.h` stays in sync.
- **New example.** `examples/frontend_backend_tour.py` — an interactive tour of
  how the frontend talks to the backend, mirroring the live protocol frames
  onto a wire-tap panel (via `canvas.on_frame`) while you interact.
- **Relative placement.** `insert()` and every factory accept `below=` /
  `above=` / `right_of=` / `left_of=` (an already-placed component or its name)
  plus `gap=` (pixels, default 16), deriving `x`/`y` from the anchor's live
  geometry — no more hand-computing dashboard coordinates. Vertical anchors
  align left edges, horizontal ones align top edges; two anchors set one axis
  each, and an explicit `x`/`y` overrides the derived coordinate.
- **`queue=` at creation.** The send-queue policy can now be passed to
  `insert()` and every factory (`canvas.image(fig, queue="latest")`) instead of
  only being set afterwards via the `comp.queue` property.

### Changed
- **`Label` now supports `h="auto"`.** Labels render inside the same sandboxed
  `Custom` iframe as `markdown`/`table`/`image`, so they fit their height to the
  text (`canvas.label("status", "…", h="auto")`) and re-fit when the value
  changes. The value is HTML-escaped and shown as plain text — use `markdown`
  for formatting. Live `update()`s stream into the iframe without reloading it,
  so a per-loop status line stays flicker-free. On the wire a Label now
  registers as a `Custom` panel and its updates carry a `post` payload.

### Fixed
- **Matplotlib figures no longer accumulate in pyplot's registry.** `Image`
  releases a figure from `matplotlib.pyplot` after rasterizing it, so
  `img.update(fig)` with a fresh figure per loop iteration (e.g. redrawing on a
  slider tick) no longer leaks; manual `plt.close()` is unnecessary. The figure
  object itself stays usable.

### Docs
- README and GUIDE now state the factory signature convention explicitly:
  input/interactive panels take `name` first; content panels (`image`, `table`,
  `markdown`, `custom`, `react`, `webview`, `show`) take the content first with
  `name=` as a keyword.

## 0.2.0

### Fixed
- **The built frontend now ships in the wheel.** Previously `pip install pycanvas`
  packaged the Python but dropped `pycanvas/frontend/dist/assets/*` (all the
  JavaScript and CSS), so an installed canvas served a blank page. The bundle is
  now declared as package data (`pyproject.toml` + `MANIFEST.in`) and a CI job
  builds the wheel, installs it clean, and asserts the page and its assets serve.

### Changed (breaking)
- **`grabable` → `grabbable`** (correct spelling) everywhere it's public: the
  `insert()` / factory keyword, the `component.grabbable` property, and
  `set_layout(grabbable=...)`. The wire/protocol key is unchanged.
- **Panel sizing is now `w` / `h`, not `width` / `height`.** Component
  constructors and the `canvas.<factory>()` helpers no longer accept
  `width=`/`height=`; pass `w=`/`h=` (the same names already used by `resize()`,
  `component.w`, and placement). Each component carries `default_w`/`default_h`
  for its default size.
- **NumPy is no longer a hard dependency.** It moved to the `[audio]` extra and
  is imported lazily inside `AudioFeed`, so a sliders/plots install is ~60 MB
  lighter. Callers streaming raw int16 bytes never import it at all.

### Added
- **`canvas.show()` now inspects strings, paths, and bytes instead of always
  showing them verbatim.** A string is routed by what it contains: an existing
  **file path** renders by extension (image / CSV→table / Markdown / JSON / HTML
  / text), an **image URL or `data:` URI** becomes an image, a bare **web URL**
  becomes a clickable link, literal **HTML** renders as HTML, and **Markdown**
  syntax renders as Markdown even when short (previously only multi-line or long
  strings did). `bytes` carrying an image (PNG/JPEG/GIF/WebP/BMP/SVG) render as
  that image, and `pathlib.Path` is accepted anywhere a path string is. Plain
  one-liners still render as a bold `Label`. No new dependencies (CSV uses the
  stdlib `csv` module).
- **`Table` (and `canvas.show(df)`) is now interactive in the browser.** Click a
  header to sort (numeric columns sort numerically; cycles asc → desc →
  original), filter rows with the search box, and toggle a per-column
  distribution chart — a histogram for numeric columns, a top-values bar chart
  for categorical ones — drawn as inline SVG. All client-side inside the existing
  sandboxed iframe, no new dependencies. Large tables render the first 2,000 rows
  (distributions still computed over the full data). pandas `Series` now render
  too, and a non-trivial DataFrame index shows as a leading column.
- `canvas.slider(...)` now exposes `step` and `on_release` directly.
- `insert()` warns when a component name shadows a `Canvas` attribute (e.g.
  `save`, `components`); reach such a panel via `canvas["<name>"]`.

### Internal
- A single `pycanvas/_flags.py` table is now the source of truth for the six
  lock/chrome flags, driving the `BaseComponent` properties, `set_layout`, the
  bridge register message, and `Canvas.insert`/save-load. Adding a flag is one
  entry. `_layout()` now also persists `operable`.
- The `Arrow` class and the hot-reload monitor moved out of `canvas.py` into
  `arrow.py` and `hotreload.py`.
- Re-inserting under an existing name is silent for same-type swaps (the
  intended cell-rerun path) and only warns when the object kind changes.
