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

## Frontend — all permissive (no proprietary licence, no watermark, no key)

danvas's canvas frontend is a custom, framework-free renderer built on **Preact**
and a small set of **permissively licensed (MIT)** packages. There is **no
proprietary component, no licence key, and no watermark** — running danvas in
development or production requires no third-party frontend licence.

Notable bundled packages (all **MIT** unless noted), from
`danvas/frontend/package.json`:

- [Preact](https://github.com/preactjs/preact) — MIT (aliased to `react`/`react-dom`
  so Python-shipped React panels run unchanged)
- [alien-signals](https://github.com/stackblitz/alien-signals) — MIT (reactivity)
- [Plotly.js](https://github.com/plotly/plotly.js) (basic dist) — MIT
  (loaded lazily, only when a plot panel first appears)
- [Sucrase](https://github.com/alangpierce/sucrase) — MIT (compiles React panel JSX in-browser)
- [perfect-freehand](https://github.com/steveruizok/perfect-freehand) — MIT (ink strokes)
- [rbush](https://github.com/mourner/rbush) — MIT (spatial index)
- [fractional-indexing](https://github.com/rocicorp/fractional-indexing) — MIT (z-order keys)
- [@use-gesture](https://github.com/pmndrs/use-gesture) — MIT (pointer gestures)
- [modern-screenshot](https://github.com/qq15725/modern-screenshot) — MIT (PNG/SVG export)
- [Inter](https://github.com/rsms/inter) via
  [@fontsource-variable/inter](https://github.com/fontsource/font-files) — the
  **Inter** typeface is licensed under the **SIL Open Font License 1.1** (OFL-1.1);
  the npm packaging is MIT.
- [Vite](https://github.com/vitejs/vite) (build tooling, not shipped in the bundle) — MIT

These licences are permissive and impose no copyleft or watermark obligation;
their notices are preserved in the respective upstream projects. This list is
non-exhaustive — see `danvas/frontend/package.json` and its lockfile for the full
dependency set and each package's own licence for authoritative terms. (To
regenerate a complete, audited notice file, run a tool such as `license-checker`
over `danvas/frontend/node_modules`.)

## History

Earlier danvas releases bundled [tldraw](https://tldraw.dev) under the proprietary
"tldraw licence" (which required a production licence key and a "made with tldraw"
watermark on the free tier). The frontend has since been rewritten to be
tldraw-free; that obligation no longer applies. The tldraw-based frontend is
preserved separately in the private `danvas-tldraw` repository.
