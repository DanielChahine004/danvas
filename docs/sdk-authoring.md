# Writing a danvas SDK

The whole process, in three steps:

1. **Read [PROTOCOL.md](../PROTOCOL.md)** — the wire contract. Consult the
   per-panel `contract` blocks in `danvas/templates/components.json` (also
   served by every hub at `GET /__templates__`) as reference while wiring
   panels; they are the normative statement of each panel's data fields,
   update keys, and events.
2. **Implement the fixed behavior script** in
   [`tests/sdk_conformance_target.py`](../tests/sdk_conformance_target.py)'s
   table — it is the executable spec. The Rust
   (`danvas-rust/examples/conformance_target.rs`) and Node
   (`danvas-node/conformance_target.js`) targets are worked examples.
3. **Run the suite until green.** It is the definition of done; there is no
   step four:

   ```
   DANVAS_SDK_CMD="./my_target|{port}" pytest tests/test_sdk_conformance.py
   ```

Existence proof: the Node SDK (`danvas-node/`, ~450 lines, zero dependencies)
was written from these documents alone and passed on its first run.

## The required floor — eight pieces

Each maps to conformance scenarios that fail without it. Approximate sizes
are from the Node SDK.

| # | build | wire surface | ~lines |
|---|---|---|---|
| 1 | **WebSocket loop** | connect `ws://host:port/ws?source=1&label=<name>`; `{"type":"heartbeat"}` every ~10 s; reconnect after ~1 s | 60 |
| 2 | **Replay cache** | keep registers (in order) + accumulated update payloads + subscriptions; re-send ALL of it on every (re)connect. The one architectural obligation: the hub replays to browsers, you replay to the hub | 50 |
| 3 | **Inbound routing** | `input`→handlers; `request`→handler, reply `{"type":"response","reqId",...}`; `layout`→fold x/y/w/h/rotation into the replay cache | 50 |
| 4 | **Binary envelope** | `[code u8][idLen u8][id utf-8][payload]`; codes in PROTOCOL.md. Media out (VIDEO 1 / AUDIO 2 / CUSTOM 3 / REACT 4), opaque INPUT 5 in | 20 |
| 5 | **File transfer** | mint unguessable tokens (`serve_bytes` → `/__download__/<token>`, upload endpoints → `/__upload__/<token>`); answer `file_pull` with `file_meta` + a FILE(6) envelope; deliver `file_push`+FILE and `file_ack`. **Decline-fast is a MUST**: answer every broadcast, `ok:false` for tokens that aren't yours | 80 |
| 6 | **`set_props` fold** | apply a routed peer write on your panel: placement keys fold like a layout; the rest merges into the data blob; echo a `data_patch` update — your echoed state is canonical | 30 |
| 7 | **Templates** | merge user data over a template's `data`, JSON-encode into `props.data`, send the register (carry `rel` through for relative placement). Fetch the asset from `/__templates__` — no need to ship it | 40 |
| 8 | **`serve()`** | the entry-point convention: probe the port → spawn `danvasd` ($DANVASD, PATH, checkout `broker/target/`) or ATTACH to a hub already there → dial in → open the browser only when you spawned. `connect(url)` is the explicit dial-only opt-out | 60 |

## What you do NOT build

No rendering (the hub serves the shared frontend). No widget code (20 native
panels come from the templates). No placement or cascade logic (`rel` is
resolved and maintained browser-side). No iframe shim or theme math (the
frontend injects/derives both — send one `frameColor`). No server, no auth
plumbing beyond the optional flow below. No per-panel special cases beyond
reading its contract.

## The optional tier — add on demand

None of these are conformance-gated; the Rust SDK (`danvas-rust/`) implements
all of them and is the reference.

| capability | wire surface |
|---|---|
| password canvases | `POST /__auth__` (form `password=`) → `pc_session` cookie on the WS handshake |
| managed shapes & arrows | `shape` / `shape_update` / `arrow` frames (replayed like registers) |
| canvas state | `view`, `shared` (define/style), `order`, `draw`/`on_draw` |
| snapshot round-trips | `get_snapshot` / `get_image` by `reqId` |
| streaming-figure feeds | `plot` / `plot_extend` update keys — keep the buffer owner-side and fold a full figure for replay (hubs fold deltas into their cache; see PROTOCOL.md) |
| media encoding | your language's JPEG/PCM story; the wire takes bytes |

## Conventions that bite if missed

- **Panel ids are yours to mint per run**; the `name` field is the stable
  cross-process identity — resolve peers' panels by name, expect yours to be
  namespaced (`s0:...`) on the wire.
- **Identity is the label**: re-dialing under the same label replaces the
  previous life's panels; distinct cohabiting programs need distinct labels.
- A dead source's panels stay **frozen (retention)** until the label returns —
  clients probing liveness must retry against the *latest* register.
- Ignore unknown frame types and fields (the compatibility rule); the
  `welcome` frame's `features` list advertises hub capabilities (`"rel"`).
