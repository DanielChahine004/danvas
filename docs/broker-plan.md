# Plan: the performance binary broker (`danvasd`)

Status: **planned, not started.** This is proposition 4 of the broker
architecture — deliberately last, because the first three (the frozen
protocol, the SQLite ledger, hub retention) define exactly what it must do.

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

**Phase 4 (optional, opens polyglot) — source SDK extraction.** A ~1k-line
`danvas-source` Rust crate (register/update/input dispatch/replay-on-connect)
that a C++/Rust process embeds to *be* a source. This is where "any language
on the canvas" becomes a shipped artifact rather than a spec promise.

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
