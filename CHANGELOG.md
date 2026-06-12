# Changelog

All notable changes to PyCanvas are recorded here. This project aims to follow
[Semantic Versioning](https://semver.org/); while pre-1.0, minor versions may
carry breaking changes (called out below).

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
