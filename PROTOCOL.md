# The danvas wire protocol (v1)

This document freezes the danvas wire contract: the frames a danvas server and
its clients exchange over one WebSocket. It exists so that **any process — a
browser, a merge hub, or an SDK in another language — can participate in a
canvas by speaking these frames**, without importing the Python library.

The machine-readable half of this contract lives in
[`danvas/_protocol.py`](danvas/_protocol.py) (binary codes, flag wire keys, the
frame-type vocabulary) and is rendered to the frontend as
`danvas/frontend/src/protocol.generated.js` by `scripts/gen_protocol.py`;
`tests/test_protocol_sync.py` fails if either side drifts. This file is the
human-readable half: framing, semantics, and the compatibility policy.

## Versioning policy

The current version is **1**. The server advertises it in the `welcome` frame
as `"protocol": 1`.

- **Additive changes** — a new frame type, a new optional field on an existing
  frame — do **not** bump the version. Every client MUST ignore unknown frame
  types and unknown fields.
- **Breaking changes** — removing or renaming a frame type or field, changing a
  binary type code, or changing the binary envelope — bump the version. A
  client seeing an unexpected version should warn and may refuse to proceed.

## Transport

One WebSocket at `ws(s)://host:port/ws`. Two frame kinds:

- **Text frames**: one JSON object per frame, discriminated by `"type"`.
- **Binary frames**: high-rate media, envelope below. Everything else is JSON.

If the canvas is password-protected, obtain a session first: `POST /__auth__`
with form body `password=...`; a correct password 303-redirects with a
`Set-Cookie: pc_session=<token>` — send that cookie on the WebSocket handshake.

Reconnecting clients may pass `?vid=&vname=&vcolor=` query params to keep a
stable viewer identity. A relaying proxy (e.g. a merge hub) passes `?proxy=1`
so the server includes it in its own input echoes.

## Connection lifecycle

On connect the server sends, in order:

1. `welcome` — who you are (`you`), the protocol version (`protocol`), UI
   flags, the initial `view`, and `runId` (panel ids are minted per run; a
   client holding panels from another `runId` must drop them).
2. Shared React assets (`shared`), then recent `chat` history.
3. **Full state replay**, filtered by your role: each panel's `register` +
   current `update`, then shapes (`shape`), arrows (`arrow`), drawings
   (`draw`), z-order (`order`), and presence (`presence`).

The client holds no source of truth: on any reconnect it receives the full
replay again and must rebuild from it.

## Server → client frames (`MESSAGE_TYPES_OUT`)

| type | meaning |
|---|---|
| `register` | a panel exists: `{id, component, props, x, y, w, h, ...flags}`; may carry `rel` (relative placement, below) |
| `update` | partial state: `{id, payload: {...}}` — merge into the panel |
| `remove` | `{id}` — the panel is gone |
| `arrow` / `shape` / `shape_update` | connector arrows and managed shapes |
| `order` | z-order list |
| `draw` | free-form ink diff: `{diff: {added, updated, removed}}` |
| `view` | camera / chrome change |
| `welcome` / `shared` / `chat` / `presence` / `cursor` / `cursor_gone` | session plumbing |
| `response` | reply to a client `request` (`{req, ...}`) |
| `container_sync` / `reflow` / `graveyard_update` | layout machinery |
| `get_snapshot` / `get_image` / `load_snapshot` | drawing snapshot round-trips |

## Client → server frames (`MESSAGE_TYPES_IN`)

| type | meaning |
|---|---|
| `heartbeat` | keep-alive; send every ~10 s (silent > 30 s is reaped) |
| `input` | user operated a panel: `{id, payload}` — fires Python handlers |
| `layout` | user moved/resized: `{id, x, y, w, h, rotation, ...}` |
| `request` | awaitable ask: `{id, req, payload}` → a `response` frame |
| `draw` | ink diff drawn by this client |
| `chat` / `set_name` / `cursor` / `ui` | session plumbing |
| `graveyard` / `restore` | delete / undelete a panel |
| `snapshot` / `image` / `panel_error` | replies + error reports |
| `set_props` | write any panel's properties: `{id, props: {...}}` (shared plane, below) |
| `subscribe` / `unsubscribe` | receive a panel's `input` events without owning it |

The server enforces authorization on ingress: an `input`/`request`/binary
frame for a panel that is `locked`, non-`operable`, role-hidden, or
`lock_for`-locked for this viewer is dropped before any handler runs.

## Binary envelope

`[type: u8][idLen: u8][id: idLen bytes, utf-8][payload]`

| code | name | direction | payload |
|---|---|---|---|
| 1 | VIDEO | server → client | JPEG frame bytes |
| 2 | AUDIO | server → client | little-endian int16 PCM |
| 3 | CUSTOM | server → client | opaque bytes → Custom panel `onPush` |
| 4 | REACT | server → client | opaque bytes → React panel `onFrame` |
| 5 | INPUT | client → server | opaque bytes → `@panel.on_binary` |
| 6 | FILE | hub ↔ owner | file-transfer bytes; the envelope id is a **reqId**, not a panel |

**File transfer (downloads through a hub):** the owner process holds download
bytes, so a browser's `GET /__download__/<token>` at the hub triggers a pull:
hub → sources `{"type": "file_pull", "token", "reqId"}` (broadcast — tokens
are opaque); the owner replies `{"type": "file_meta", "reqId", "ok": true,
"filename"}` followed by a FILE envelope carrying the bytes; **every other
source MUST reply `ok: false`** — a source that stays silent leaves the hub
waiting out its full 15 s deadline before it can 404, so decline-fast is a
conformance requirement (tests/test_sdk_conformance.py asserts it). First
success streams out as the HTTP response; all-declined or 15 s → 404. Role-gated tokens are declined over a hub (fail closed).

**Uploads** mirror it: the hub's `POST /__upload__/<token>?name=...` (raw
body) broadcasts `{"type": "file_push", "token", "reqId", "name",
"content_type"}` followed by a FILE envelope with the bytes; the owning
source delivers to its endpoint handler and replies `{"type": "file_ack",
"reqId", "ok": true, "name", "size"}` (**non-owners MUST ack `ok: false`**,
same decline-fast rule as pulls); the hub
answers the HTTP request from the ack. Owner-side `max_size` and role gates
apply at the owner (role-gated endpoints fail closed over a hub).

## The merge control plane

A merge hub speaks the base protocol to each source (as a `?proxy=1` client)
and adds a small control vocabulary with its own browsers: `merge_add`,
`merge_auth`, `merge_remove`, `merge_offset` up; `merge_sources`,
`merge_auth_required`, `merge_auth_failed` down. Panel ids are namespaced
`s<N>:<id>` per source; interactions on a namespaced id route back to the
owning source with the prefix stripped.

## Dial-in sources (the polyglot on-ramp)

A process can own panels on a running canvas **without serving anything**:
connect to the canvas's own WebSocket with `?source=1&label=<name>`:

```
ws://host:8000/ws?source=1&label=telemetry
```

Every served canvas is a hub by default, so this works against a plain
`canvas.serve()`. On such a connection:

- **Down** (hub → source): the normal subscriber stream — `welcome`, then the
  full state replay of the hub's canvas (read access), plus `input` / `layout`
  frames for the panels this source registered (its callbacks), with the
  `s<N>:` namespace already stripped.
- **Up** (source → hub): `register` / `update` / `remove` / `arrow` / `draw`
  declare and mutate this source's own panels — the hub namespaces, caches,
  and fans them out to every browser. Anything else (`heartbeat`, an `input`
  on a hub panel it observed) is treated as ordinary viewer traffic: a source
  is also a subscriber that may petition.
- **Identity is the label.** Reconnecting under the same label replaces the
  previous life's panels; the client must re-send its registers + current
  state on every (re)connect, exactly like the hub replays to browsers.
- **Liveness**: send `heartbeat` every ~10 s. On disconnect the hub applies
  its offline policy (default: panels held frozen and dimmed until the label
  returns).
- **Auth**: for a protected canvas, run `POST /__auth__` first and connect
  with the `pc_session` cookie; the source then sees and may operate exactly
  what that role allows.

`danvas/source.py` (`danvas.SourceClient`) is the reference implementation —
~200 lines, and the executable spec for an SDK in any language.

**`serve_config` (optional, source → hub):** an owner dialing into a broker it
did not spawn (e.g. the hot-reload monitor started danvasd before the script
ran) may deliver its resolved UI-affordance gating:
`{"type": "serve_config", "uiInspector": bool, "uiGraveyard": bool,
"cursors": bool, "uiHosting": bool}` (all fields optional; absent fields keep
the hub's current value). The hub folds the flags into every subsequent
browser welcome **and relays the frame to already-connected browsers**, which
apply it live (a hot-reload browser outlives the worker, so its welcome
predates the flags). A hub MAY ignore it from untrusted sources.

**Authoring native panels:** register-frame props are opaque to the protocol,
but the built-in panels only render when the frame carries the React-shaped
props the frontend mounts. Those shapes ship as a language-neutral asset —
`danvas/templates/components.json` (regenerated by
`scripts/gen_component_templates.py`) — so an SDK merges user kwargs over a
template's `data`, JSON-encodes it into `props.data`, and sends the register.
`SourceClient.register_template` is the reference. An SDK need not even ship
the asset: hubs serve their own copy at `GET /__templates__`, version-matched
to the frontend they embed by construction — fetch it after connecting
(`danvas-node` does this when no local copy exists).

## Relative placement (`rel`)

A register frame may carry an optional additive field:

```json
"rel": {"kind": "below", "anchor": "<panel id, owner-side>", "gap": 16}
```

(`kind` ∈ `below | above | right_of | left_of`; `gap` in px, default 16.)
A hub advertising `"features": ["rel"]` in its `welcome` ships a frontend that
**resolves and maintains** it: when the frame has no explicit `x`/`y`, the
browser places the panel against the anchor's live geometry (falling back to
the auto-flow if the anchor is unknown) and reports the position back as a
`layout` frame with `auto: true` so the owner folds it into replay; whenever
an anchor's *height* settles differently (auto-fit content, a resize), every
panel anchored on it — transitively — re-settles in the same browser frame,
with the moves reported back the same way. Explicit `x`/`y` in the frame
always wins for initial placement (an SDK that resolved the chain locally, or
a folded-back drag), but `rel` still records the dependency edge that drives
the cascade. Hubs relay `rel` opaquely; against a hub that doesn't advertise
the feature, placement and cascading are the SDK's own to provide (the
Python `below=` and the Rust SDK's local resolution are reference fallbacks).

## Component contracts

The wire protocol says how frames move; it deliberately says nothing about
what a *particular panel* expects inside them. That layer — which data fields
a slider has, which update keys a live plot consumes, what payload a file
browser's clicks emit — is declared per template as a **`contract` block in
`components.json`**, generated from a `CONTRACT` declaration on each Python
component class (the same freshness test that guards the JSX guards these).
The contract is the normative reference for SDK authors: implementing a panel
means reading its contract, not the Python source.

Each contract carries:

| key | meaning |
|---|---|
| `data` | authorable data-blob fields → informal type strings (`"number"`, `"list[str]"`, `"str -- note"`). `_th` (the accent theme) is universal and auto-declared. |
| `props` | register-prop-level fields outside the data blob (e.g. Custom's `html`) |
| `updates` | the `update` payload keys the panel consumes → what each carries |
| `events` | the `input` payload shapes the panel emits (browser → owner + subscribers) |
| `requests` | `request`→`response` round-trips the panel makes and their reply shapes |
| `binary` | how the panel uses the binary envelope, if at all |
| `geometry` | default `w`/`h` and whether the panel auto-fits its height |
| `encoded` | legacy string-double-encoded data fields — **frozen at empty**: template JSX must parse tolerantly (a JSON string from Python's `json.dumps` OR plain JSON from any other SDK) rather than SDKs encoding defensively |

**Update-payload vocabulary** (frontend-defined, shared by all panels): a
payload's top-level `x`/`y`/`w`/`h`/`rotation`/`opacity`/lock flags/
`frameColor` patch the panel's frame; `data_patch` merges changed fields into
the data blob; `post` is a value pushed straight to the mounted panel;
`post_style` restyles live; `plot`/`plot_extend` are the streaming-figure
channel. A panel's contract lists which of these it uses.

Hubs that cache update payloads for replay MUST honour the streaming-figure
semantics rather than merging by key: a full `plot` supersedes any pending
`plot_extend`, and a cached `plot_extend` folds INTO the cached figure
(append per trace index, capped at `max`) — otherwise a late-joining client
replays a stale figure plus one dangling delta, and a reconnecting one
double-applies the last point. `danvasd` implements this
(`apply_plot_extend`); `tests/test_broker_replay.py` asserts it.

## The shared property plane

The canvas is a shared document: **any peer may write any panel's properties**
with `set_props` (`{"type": "set_props", "id": ..., "props": {...}}`), whether
or not it owns the panel. The write routes to the panel's owner and applies
through the owner's real setters — validation, lock enforcement, and the live
broadcast come from that one code path, and because every write to a panel
sequences through its owner, concurrent writers converge last-writer-wins with
no merge machinery. Placement keys (`x`/`y`/`w`/`h`/`rotation`/`opacity`) and
lock flags are accepted alongside component properties (`min`, `max`,
`color`, `options`, …). Unknown keys and rejected values are dropped at the
owner; its echoed state is canonical.

Permissions: a **browser** passes the same gate as `input` (roles, `operable`,
`lock_for`); a **process peer** (dial-in source, merge proxy) is authoritative
— only a hard `locked` stops it.

`subscribe` is the events half: a subscribed connection receives a copy of a
panel's `input` frames (the originator excluded; the owner's handlers are
unaffected), so any process can *react* to any panel — behavior stays where
its code and state live, but who listens is a live, shared fact.

## Writing a non-Python client (the polyglot subset)

> The step-by-step version of this section — the eight-piece build list, the
> optional tier, and the conformance workflow — lives in
> [docs/sdk-authoring.md](docs/sdk-authoring.md).

**The entry-point convention:** a danvas program's default move is to OWN its
canvas — the SDK's primary entry (`serve(port)`) finds/spawns `danvasd` on the
port, or attaches to a hub already serving it (so two programs pointed at the
same port compose on one canvas), then dials in and opens the browser when it
spawned. Dial-only (`connect(url)`) is the explicit opt-out for joining a
canvas served elsewhere. All three SDKs follow this shape.

A minimal *source* SDK needs only:

1. Dial in as above (a plain WebSocket client — no server required): emit
   `register` for each panel, `update` for state changes, `remove` on
   teardown, and re-send all of it on every reconnect.
2. Handle inbound `input` / `layout` by invoking user callbacks, and send
   `heartbeat` every ~10 s.
3. Optionally, the binary envelope for media.

(The *served*-source form — running a canvas server the hub dials, as Python
canvases do — is the same frame vocabulary with the connection direction
reversed, plus replay-to-connecting-clients duties.) Everything else (roles,
overlays, containers, shapes) is optional and additive — a client that
ignores those frames still composes correctly.

## Appendix: the persist file (owner-side state)

`serve(persist=...)` is an **owner** feature — the serving process saves its
own canvas state and restores it before (re)connecting, so nothing here rides
the wire — but the format is specified so a non-Python owner can implement
the same durability.

The JSON form (`*.canvas.json`, written atomically via temp-file + rename,
debounced ~1 s after each user edit):

```json
{
  "layout": {
    "components": [{"name", "id", "x", "y", "w", "h", "rotation", "opacity",
                     ...lock/chrome flags..., "state"?}],
    "arrows":     [{"name", "start", "end", "props"}]
  },
  "drawings": { "<record id>": <ink record>, ... }
}
```

`components[].state` is the user-set value of an input control (a slider's
value, a toggle's choice) — content panels omit it, since re-running the
program reproduces their state. Matching on **name** (not the per-run id) is
what lets a restore survive a process restart. `drawings` is the free-form
ink record set exactly as the `draw` frames carry it. A path ending in
`.db`/`.sqlite`/`.sqlite3` selects an append-only SQLite ledger holding the
same snapshot shape plus an events table; the JSON file is the interchange
form.
