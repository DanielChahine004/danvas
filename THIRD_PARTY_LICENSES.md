# Third-party licences

The danvas distribution (the pip wheel and sdist) bundles a **pre-built frontend**
in `danvas/frontend/dist/` — a compiled JavaScript/CSS/font bundle. That bundle
is produced from third-party packages, and **those packages' own licences govern
the bundle**, not danvas's AGPL-3.0 licence. danvas's AGPL covers danvas's own
source code; it does not (and cannot) relicense the third-party code shipped
inside the frontend bundle.

This file lists the notable components compiled into that bundle. It is a
good-faith summary for downstream users — the authoritative terms are each
project's own licence, linked below.

## tldraw — proprietary "tldraw licence" (NOT open source, NOT AGPL)

danvas's canvas is built on [tldraw](https://tldraw.dev), which is **not** under
a permissive or copyleft open-source licence. It is distributed under the
**tldraw licence** — references:
https://github.com/tldraw/tldraw/blob/main/LICENSE.md and
https://tldraw.dev/community/license.

**You (the danvas user) need your own tldraw licence to run in production.**
tldraw's model is:

- **Development is free** and needs no licence key — running danvas locally to
  build and test is fine as-is.
- **Production requires a tldraw licence key.** Per tldraw: *"The tldraw SDK
  will not work in production without a valid license key."* There are two:
  - a **hobby** licence — free, for **non-commercial** production, requested via
    a form at https://tldraw.dev/community/license; it requires the "made with
    tldraw" watermark to stay visible;
  - a **commercial** licence — paid (sales@tldraw.com), for commercial
    production; removes the watermark.
- Downstream users of a tldraw-based library are covered the same way: *"you and
  your down-stream users will require their own trial, commercial, or hobby
  license in order to use the SDK in production."*

**Supplying your key to danvas:** pass it to `serve(...)` or set the env var, and
danvas injects it into the page as the `<Tldraw licenseKey=…>` prop:

```python
canvas.serve(tldraw_license_key="tldraw-...")   # or: export TLDRAW_LICENSE_KEY=tldraw-...
```

**Important for danvas users and commercial licensees:** a danvas licence (AGPL
*or* a commercial danvas licence) grants rights to **danvas only**. It does
**not** grant or include any tldraw licence. Whoever runs a danvas app in
production is independently responsible for obtaining their own tldraw hobby or
commercial licence. A commercial danvas licence does not waive, sublicense, or
alter the tldraw licence in any way.

## Other bundled components — permissive (MIT)

The frontend bundle also compiles in, among others:

- [React / React DOM](https://github.com/facebook/react) — MIT
- [Monaco Editor](https://github.com/microsoft/monaco-editor) — MIT
  (loaded only when a `Repl` panel first appears)
- [Plotly.js](https://github.com/plotly/plotly.js) — MIT
- [Vite](https://github.com/vitejs/vite) (build tooling) — MIT

These are permissive (MIT) and impose no copyleft or watermark obligation;
their notices are preserved in the respective upstream projects. This list is
non-exhaustive — see `danvas/frontend/package.json` and its lockfile for the
full dependency set and each package's own licence for authoritative terms.
