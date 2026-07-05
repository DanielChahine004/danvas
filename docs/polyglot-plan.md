# Polyglot hardening plan

Where this comes from: building the Rust SDK to full parity (July 2026) proved
the wire protocol solid — the broker never changed — but showed that the
**per-component contract is folklore**. Every real bug hit along the way (empty
inspector, un-centred custom panel, masonry racing `below()` chains, the
histogram silently degrading to a line) traced to a contract that lived only in
Python source or template JSX. This plan promotes those implicit contracts into
shared artifacts, shrinks SDKs toward thin wire clients, and puts validation
nets under both, so the *next* SDK (C++/MATLAB/TS) is cheap to write and cheap
to trust.

Principle throughout (established by the tolerant-inspector fix): **the shared
artifact absorbs the variance, not each SDK.**

---

## Phase 1 — The component contract, machine-readable

Each entry in `danvas/templates/components.json` gains a `contract` block:

```json
"contract": {
  "data":     {"min": "number", "max": "number", "value": "number", "...": "..."},
  "updates":  ["data_patch", "post"],          // update payload keys it consumes
  "events":   [{"event": "open", "name": "str"}, {"event": "up"}],   // inputs it emits
  "actions":  [{"action": "refresh"}, {"action": "source", "source": "str"}],
  "requests": {"click": {"returns": {"url": "str", "filename": "str"}}},
  "geometry": {"w": 320, "h": 420, "auto_h": false},
  "encoded":  []                                // legacy string-encoded fields, shrinking to none
}
```

- Source of truth: a small `CONTRACT` declaration on each Python component
  class; `scripts/gen_component_templates.py` emits it. A test fails if any
  template lacks one, or if a component's JSX reads a `props.<field>` /
  `data.<field>` not declared (greppable check).
- `PROTOCOL.md` gains a "component contracts" section: framing stays in the
  protocol doc, per-panel data shapes live in the asset, and the asset is the
  normative reference for SDK authors.
- Rust SDK (and future SDKs): `PanelBuilder::set()` warns in debug builds on a
  key the contract doesn't declare — typo-catching for free.
- Superseding rule: contracts can mark update keys as superseded
  (`"plot" supersedes "plot_extend"`), which Phase 6 uses to fix broker replay.

Acceptance: a new SDK author can implement the table, file browser, and
inspector without reading any Python source.

## Phase 2 — Move owner-side rendering logic into the templates

The frontend is already the shared renderer; make it the shared *component
logic* too, so `helpers.rs`-style transliterations stop being necessary.

- **custom** — SHIPPED: the frontend's `CustomView` injects the canvas-API
  helper + interaction shim (customShim.ts), guarded by the `window.canvas=`
  marker so owner-wrapped documents (older wheels, persisted canvases) pass
  through. The script builders left `custom.py` (only the h/w="auto" fit
  script stays owner-side — it needs the owner's flags and is matched by
  source window); the shim copy left `helpers.rs`. Bonus fix: the injected id
  is the *browser-local composed* id, so `canvas.send()` from an iframe now
  routes correctly through a hub (an owner-baked id loses its namespace tag).
  `forwardWheel` rides register props as the wheel opt-out.
- **theme** — SHIPPED: the frontend derives `_th` from the top-level
  `frameColor` when `data._th` is absent (theme.ts, the port of
  `_theme.derive`); owner-sent `_th`/post_style still wins. Rust's
  `derive_theme` + parity test deleted; `.color()` sends one hex string.
- **histogram / live_plot figure-building — DEFERRED, deliberately.** Moving
  the buffers client-side breaks the replay model (a late-joining browser
  needs the accumulated state, so the owner must hold it anyway), and the
  wire form is already delta-efficient (`plot_extend`). What *could* move is
  only the figure styling (palette/EMA/margins) — low value against the churn
  of new update keys in bridge.ts, both SDKs, and the broker's replay cache.
  Revisit only if a third SDK finds the feeds burdensome; they are ~60 lines
  each and conformance-tested.
- **NOT moved** (by design): file-browser navigation (sandboxing is
  owner-side security), download content resolution (host decides what bytes
  leave the machine).

## Phase 3 — Protocol-ify relative placement

Placement semantics are currently split three ways (browser masonry, SDK
`below=` resolution, SDK cascade) — the source of the masonry race and the
flickery reflow.

- The register frame gains an optional additive field:
  `"rel": {"kind": "below", "anchor": "<id>", "gap": 16}`.
- The **frontend** resolves it: place relative to the anchor's live geometry at
  mount (never entering masonry), and re-settle the chain when an anchor's
  height changes — it already owns repack for containers, so cascade becomes
  one implementation with no round-trips (goodbye debounce, goodbye flicker
  entirely: the shift happens browser-side in the same frame as the resize).
- Hub: pure pass-through (unknown-field rule). Old brokers keep working.
- SDKs: `below()` just sets `rel`; the Rust resolution/cascade/debounce stays
  as a fallback behind a welcome-advertised feature flag for old frontends,
  then gets deleted. Python's `below=` migrates last (it has extra semantics —
  deferred anchors, `_below_deps` interactions with containers — so it keeps
  its path until the frontend implementation is proven by the SDKs).

Acceptance: the Rust catalogue's chain works with the SDK-side placement code
deleted; resizing a panel reflows the chain with no wire traffic.

## Phase 4 — Source-SDK conformance suite

`tests/test_conformance.py` validates hubs; nothing validates SDKs — every SDK
re-derives reconnect/replay/decline semantics by hand.

- New `tests/sdk_conformance/`: pytest harness that spawns `danvasd` + a
  candidate source process (`DANVAS_SDK_CMD`, same pattern as
  `DANVAS_HUB_CMD`) + a browser-emulating probe (raw WebSocket viewer — the
  July 2026 inspector-verification probe is 80% of it; lift it into a fixture,
  with wall-clock-bounded pumps since media frames never go quiet).
- Scenarios, each asserting on the probe's view: register/update/replay after
  a broker restart; label-reconnect replacing the previous life; input/layout
  route-back; set_props on a peer's panel; subscribe; request→response;
  binary media envelopes; FILE transfer both directions **including the
  decline-fast rule** (unknown token answered, not timed out); layout
  fold-back surviving reconnect.
- Two reference targets in CI: the Python `SourceClient` and a small Rust
  `conformance_target` example. A third SDK passes by implementing one
  command-line entry point.

Acceptance: both existing SDKs pass; the suite is the definition of done for
any new SDK.

## Phase 5 — Renderer capability tests

The plotly-basic/heatmap failure was invisible: no error surfaced anywhere,
the figure just degraded. The renderer's capability set is part of the
contract; test it.

- A node smoke test asserting every Plotly trace type any shipped component
  emits (scatter, bar, heatmap, …) is registered in the bundled dist — cheap,
  no browser.
- A Playwright template smoke test: serve the built dist, mount every template
  from `components.json` with the sample data its Phase-1 contract declares,
  assert no console errors and a non-empty render box. Catches missing bundle
  features, JSX/sucrase breakage, and contract drift in one net.
- Wire into the existing test flow next to `test_protocol_sync.py` (same
  spirit: the two halves of a contract can't drift silently).

## Phase 6 — Known wire/broker artifacts and hardening

- **Broker replay duplicate point**: the cached update map can hold both
  `plot` (full figure) and `plot_extend` (last delta); replay applies both, so
  a reconnecting client double-appends the last sample. Fix: when caching an
  update whose key supersedes another (Phase 1 contract), drop the superseded
  key. Generic, not LivePlot-specific.
- **PROTOCOL.md additions**: the FILE-envelope decline-fast requirement stated
  as MUST (every source answers every broadcast pull/push); a note that the
  FILE envelope id is a reqId, not a panel id, moved from prose into the
  binary-codes table.
- **Persist for non-Python owners**: spec the `persist=` file format
  (currently an undocumented Python-side serialization) in a short appendix so
  any SDK can implement owner-side persist. Implementation optional; the spec
  is the deliverable.
- **danvasd graceful shutdown flag** — DECIDED: non-goal. Retention is a
  feature (a dead source's panels freeze until the label re-dials), and every
  self-serve path already owns its broker's lifetime (Rust `Broker` kills on
  Drop; Python's serve owns the child process).

## Phase 7 (stretch) — Prove it with a third SDK — SHIPPED

`danvas-node/` — ~430 lines, zero dependencies (Node ≥ 22's own WebSocket),
written only from PROTOCOL.md + the contract blocks, no helpers file. It
passed the full conformance suite on its FIRST run (9 passed, 1 xfail — the
same shared-plane xfail as the other SDKs), and is now the suite's third
permanent target (`pytest tests/test_sdk_conformance.py -k node`). The
acceptance metric held: phases 1–4 made a new SDK an afternoon's work.

---

## Ordering and dependencies

1. **Phase 1** first — Phases 2, 4, 5 all consume the contract schema.
2. **Phase 4** early second — it locks current behavior before Phases 2–3
   move logic around (refactor under a net, not before it).
3. **Phase 2**, then **Phase 3** (both change the frontend; ship each with a
   dist rebuild + broker re-embed + the Phase-5 smoke test green).
4. **Phases 5–6** interleave as the nets and fixes for the above.
5. **Phase 7** last, as the acceptance test for the whole plan.

Each phase is independently shippable; nothing bumps the protocol version
(all additive), so old brokers and wheels stay compatible throughout.
