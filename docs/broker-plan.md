# Roadmap: the shared-canvas architecture

Where things stand (2026-07-03): the protocol is frozen (PROTOCOL.md v1), the
SQLite ledger and hub retention shipped, and the whole peer story landed —
dial-in sources, the shared property plane (`set_props`/`subscribe`),
`danvas.connect()` (the native Canvas API over a socket), cross-process names
and `owner`/`sources`, content-verb parity, cross-source arrows. **Dial-in is
the primary protocol role**: SDKs implement it; dialed-out serving remains the
composition feature for canvases that are themselves products.

## Next steps, in order

**1. Real-browser smoke test (~an hour).** Everything above shipped against
frame-level and test-socket assertions; nobody has *looked* at it. Verify on
a real canvas (LAN + phone): the retention freeze (dim + offline dot) when a
source dies and heals on re-dial; the plain-tab disconnect banner; a
`danvas.connect()` process putting a slider on a served canvas, editing a
Python panel's props by name, and reacting to a Python button. One thing to
check deliberately: raw `SourceClient.register("x", "Slider", ...)` sends
`component: "Slider"`, but the frontend mounts built-ins as React panels with
baked props — the raw client's panels may not render without React-shaped
registration. `danvas.connect()` is immune (real component classes build its
frames); PROTOCOL.md must state what a non-Python SDK actually has to send.

**2. The Rust source SDK (`danvas-source` crate) — weeks, not months.** A
transliteration of `danvas/source.py` (the executable spec) against protocol
v1: dial in (`?source=1&label=`), register/update/remove + replay-on-
reconnect, heartbeats, input/layout callbacks, `set_props`/`subscribe`, the
panels mirror with name lookup. Ergonomics goal: feel like `danvas.connect()`
does in Python. Deliverable: the two-languages-one-canvas demo — a Rust
process putting live panels on a Python canvas, retuning a Python slider,
reacting to a Python button. This is broker-plan phase 4 pulled forward,
because it needs no broker: the Python hub already speaks the role.

**3. Phase 0 conformance harness (1–2 weeks, pure Python).** Protocol-level
tests that drive *any* hub over real sockets (spawn → connect fake source +
fake browser → assert frame sequences), proven against the Python hub first.
Keeps the Rust SDK honest now and becomes the Rust broker's definition of
done later. Can run alongside step 2.

**4. The binary broker — STARTED 2026-07-03.** Phase 0 and the phase-1 relay
core shipped the same day the protocol froze: `tests/test_conformance.py` is
the hub-agnostic contract (10 assertions over real sockets — welcome/version,
namespacing+identity, replay, input routing, subscribe, set_props, retention
+ re-dial, cross-source arrows, embedded frontend at `GET /`, and the
merge-panel roster incl. offline retention), and `broker/` is `danvasd`, an
axum/tokio relay with the built `dist/` compiled in that **passes all 10** —
a browser can point straight at it, no Python on the box:

```bash
python -m pytest tests/test_conformance.py                    # vs the Python hub
DANVAS_HUB_CMD="<abs>/broker/target/debug/danvasd.exe|--port|{port}" \
  python -m pytest tests/test_conformance.py                  # vs danvasd
```

Done since (harness now 21, both hubs green on every row): drawings relay,
offsets (`merge_offset`), roster, fresh-register replay folding, **auth**
(`--password` + the `/__auth__` cookie flow, on the Python hub too), and
**heartbeat reaping** (`DANVAS_HEARTBEAT_TIMEOUT` overridable on both hubs),
and **dialed-out sources** (`merge_add`/`merge_remove` compose served
canvases by URL; danvasd dials as a retrying ws client through the same
ingest path as dial-ins — per-connection scoping remains unpinned).
**Wire-behavior parity is COMPLETE** (harness at 28, both hubs green,
release binary 6.1 MB): binary media relay (which LIFTED the documented
merge limitation), `/__describe__` (composed inventory on standing hubs —
both), the hub ledger (`DANVAS_LEDGER=<path.db>`, `_ledger.py` schema via
rusqlite), and protected-source `merge_auth` (danvasd probes, runs the
target's `/__auth__`, dials with the cookie; minimal HTTP client — no TLS,
so tunneled protected sources are a documented gap).

**Since parity: templates + the transplant landed.** The built-in panels'
register shapes are a language-neutral asset (`danvas/templates/
components.json`, generator + freshness test; `SourceClient.
register_template` is the reference move — any language authors native
panels). And `serve(broker=True)` works end-to-end: danvasd owns the port,
the existing bridge class-swaps onto the socket, the UI survives the script.

**The declared goal is broker-by-default.** The gate is canvas-surface
parity, harness-pinned like everything else. Rows still to cross the hub
before the default flips (harness at 36 — shapes, request/response,
presence, chat, `set_view`, shared assets, graveyard, and **roles** all
landed; roles = multi-password login + wire-declared allowlists + hub-side
egress/ingress enforcement, the row that mattered most for the declared
endgame): **THE PARITY BOARD IS CLEAR** (harness at 38):
uploads AND downloads cross both hubs (file_pull/file_push + the FILE
envelope; owners hold the bytes; role-gated tokens/endpoints fail closed
over a hub). **THE DEFAULT IS FLIPPED**: plain
`serve()` now prefers danvasd when the binary is present (embedded-only
features and `broker=False`/`DANVAS_EMBEDDED=1` fall back; `broker=True`
demands it; danvasd grew `--host` for LAN binds). What remains: per-role
prop OVERLAYS (deferred), `persist=`/hot-reload re-scope, and
DISTRIBUTION (CI wheels bundling the binary, TLS in dial-out, release).

**The declared endgame (Daniel, 2026-07-04): the broker is THE
implementation.** Once uploads/downloads land and the default flips, the
Python package's serving half (server.py, the bridge's hub role, merge.py's
hub) becomes legacy behind `broker=False`, then gets removed in a major
version — every fix/feature lands once in danvasd for all languages, and
Python's dependency list shrinks to the websocket client. The bridge's SDK
half (components, handler dispatch) stays: that IS the Python binding.
Prereq for full removal: TLS in danvasd's dial-out (tunneled sources). Then: cross-platform release builds in CI, `pip install
danvas[broker]` wheels bundling the binary, flip `broker=` default with an
escape hatch (`broker=False`), a bare-binary GitHub release. Known
unpinned semantics: per-connection vs canvas-wide merge_add scoping;
hub-side stream conflation (queue="latest" mid-hub).

---

# The broker plan (`danvasd`)

Status: **phase 1 relay core in progress** — conformance-green on the day-one
scope above.

## What it is

A standalone, pre-compiled binary that does what the Python merge hub does
today — serve the frontend, hold the composed canvas, fan out to browsers,
route interactions to owning sources, retain dead sources, write the ledger —
with no Python runtime in the serving path. User scripts (Python, and later
Rust/C++/anything) connect to it **as sources**, exactly as they connect to
`python -m danvas.merge` now.

```
browsers ──ws──▶ ┌──────────────────┐ ◀──ws── python script (source)
browsers ──ws──▶ │  danvasd (Rust)  │ ◀──ws── rust/c++ process (source)
                 │  replay · fanout │
                 │  retain · ledger │──▶ board.canvas.db
                 └──────────────────┘
```

**The load-bearing decision: the broker is a hub, not a canvas.** It runs no
component logic, holds no variables, evaluates no callbacks — Python keeps
owning *behavior*; the broker owns *state, fan-out, and durability*. This is
why the project is tractable: the Python `_MergeHost` (~1k lines) is the
behavioral spec, not the 12k-line package.

## Why (and why not yet)

| Gain | Ballpark |
|---|---|
| Fan-out throughput | Python asyncio+orjson ceiling ~10⁴–10⁵ small frames/s/core → **5–20×** |
| Tail latency | no GC/GIL pauses → per-frame jitter drops an order of magnitude |
| Footprint | ~30–60 MB RSS → **~5–15 MB**; cold start ~1 s → ~10 ms |
| Deployment | UI survives *any* script, no Python required on the box |

A 20-panel dashboard at 30 Hz is ~600 frames/s — **1–2% of the Python
ceiling**. So the go/no-go trigger is honest need: build it when a real
workload sits within ~10× of the Python hub's ceiling, when a Python-free
deployment target appears, or when the polyglot SDK story needs a neutral
standing broker to sell. Until then this document is the plan of record and
nothing more.

## What already pins the design

- **PROTOCOL.md (v1)** — the wire contract the broker speaks on both faces
  (browser-facing and source-facing). Breaking changes bump the version; the
  broker targets a version, not the Python implementation.
- **`_MergeHost` semantics** — namespacing (`s<N>:<id>`), per-connection
  source sets, upstream pooling by `(uri, cookie)`, offset translation,
  input-echo suppression, retention freeze
  (`{operable: false, opacity: 0.45}`), teardown-then-replay on reconnect.
  `tests/test_merge.py` + `tests/test_merge_retain.py` are the executable
  spec — port the *assertions* before the code.
- **`_ledger.py` schema** — `snapshots(seq, ts, state)` +
  `events(seq, ts, type, comp, payload)`, WAL. The broker writes the same
  schema so `canvas.ledger`-style tooling reads either producer.

## Technology

- **Rust**, tokio runtime. WebSockets: `tokio-tungstenite` (client + server).
  HTTP + static frontend: `axum`. JSON: `serde_json` (frames are handled as
  semi-opaque `Value`s — the broker rewrites `id`/`x`/`y` and routes; it does
  not model every component's payload, which is what keeps it small and
  protocol-stable). Ledger: `rusqlite` (bundled SQLite, WAL).
- **Frame handling principle:** parse the envelope, not the world. The broker
  needs `type`, `id`, `x`, `y`, `start`, `end`, and the draw-diff shape;
  everything else passes through untouched. Binary frames need only the
  2-byte header. This is why a protocol-vNext panel type works through an old
  broker unchanged.
- **Frontend:** the existing built `dist/` embedded via `include_dir!` — the
  broker serves the same bundle the Python package ships.

## Phases

**Phase 0 — conformance harness (1–2 weeks, pure Python, do this first).**
Extract the merge test suite into protocol-level conformance tests that drive
*any* hub implementation over real sockets (spawn process → connect fake
source + fake browser → assert frame sequences). Run them against the Python
hub to prove the harness. This is the contract the Rust broker must pass, and
it de-risks everything after it.

**Phase 1 — relay core (3–5 weeks).** `danvasd serve --port 8080` +
`danvasd add :8001`-equivalent seeding. Browser connections, welcome/replay,
source pool, namespacing, fan-out, input/layout routing, retention,
reconnect-teardown, heartbeats/reaping. Exit: passes the phase-0 harness;
soak test: 50 browsers × 5 sources × 1 kHz updates overnight.

**Phase 2 — parity (3–4 weeks).** Auth (`/__auth__` flow against protected
sources + its own password gate with the signed-cookie scheme), drawings
relay + the hub's own annotation layer, offsets/`merge_offset`, roster
messages, ledger writing, `/__describe__`. Exit: a browser can't tell
`danvasd` from `python -m danvas.merge` on the full example set.

**Phase 3 — distribution (2–3 weeks).** `pip install danvas[broker]` ships
the platform wheel (maturin/cibuildwheel; ruff-style per-platform binaries);
`canvas.serve(broker=True)` spawns/attaches to a local `danvasd` and serves
*through* it — single-script UX, broker durability. Also a bare-binary GitHub
release for the no-Python box. Exit: `serve(broker=True)` runs the README
hello world on Win/mac/Linux CI.

**Phase 4 — source SDK extraction.** *Pulled forward: this is step 2 of the
roadmap above, buildable today against the Python hub with no broker.* A
~1k-line `danvas-source` Rust crate that a C++/Rust process embeds to *be* a
source — "any language on the canvas" as a shipped artifact rather than a
spec promise.

Realistic solo total for phases 0–3: **~3–4 months** at current pace,
assuming no protocol changes mid-flight (that's what the freeze is for).

## Non-goals

- **Not a Python replacement.** Components, callbacks, layout, `show()`,
  bake, hot reload stay in the Python package, unchanged.
- **No behavior in the broker.** No expression language, no server-side
  callbacks — behavior stays in owner processes, full stop.
- **No CRDTs.** Single-writer-per-panel stands; the broker is a sequencer.
- **No protocol v2.** The broker is a consumer of v1. Any change it needs is
  a red flag on the design, not a version bump.

## Risks

| Risk | Mitigation |
|---|---|
| Behavioral drift vs Python hub | Phase-0 harness is the spec; run it against both in CI forever |
| Frontend coupling (subtle welcome/replay ordering) | Ordering is documented in PROTOCOL.md §lifecycle; harness asserts it |
| Windows CI for wheels | cibuildwheel matrix from day one of phase 3, not the end |
| Scope creep toward "broker as canvas" | The non-goals section above is the answer; anything needing component knowledge belongs in a source |
| Maintenance drag on daily Python velocity | Broker only touches frozen-protocol surface; Python work proceeds independently |
