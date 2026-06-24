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
**tldraw licence**:

- Licence text: https://github.com/tldraw/tldraw/blob/main/LICENSE.md
- Free use is permitted **provided the "made with tldraw" watermark remains
  visible and unobscured.** danvas ships on this free tier and does not remove
  or hide the watermark.
- **Removing the watermark requires a paid tldraw business licence**
  (https://tldraw.dev). danvas does not include one and cannot grant tldraw
  rights to anyone.

**Important for danvas users and commercial licensees:** a danvas licence
(AGPL *or* a commercial danvas licence) grants rights to **danvas only**. It does
**not** grant any rights to tldraw. If you use danvas, you are independently
responsible for your own tldraw compliance — either keep the watermark, or
obtain your own tldraw business licence to remove it. A commercial danvas
licence does not waive, sublicense, or alter the tldraw licence in any way.

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
