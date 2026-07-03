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
| `register` | a panel exists: `{id, component, props, x, y, w, h, ...flags}` |
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

## The merge control plane

A merge hub speaks the base protocol to each source (as a `?proxy=1` client)
and adds a small control vocabulary with its own browsers: `merge_add`,
`merge_auth`, `merge_remove`, `merge_offset` up; `merge_sources`,
`merge_auth_required`, `merge_auth_failed` down. Panel ids are namespaced
`s<N>:<id>` per source; interactions on a namespaced id route back to the
owning source with the prefix stripped.

## Writing a non-Python client (the polyglot subset)

A minimal *source* SDK — a process that owns panels on a hub-composed canvas —
needs only:

1. Serve the base protocol (or connect to a hub that does): emit `register`
   for each panel, `update` for state changes, `remove` on teardown, and
   replay all of it to any (re)connecting client.
2. Handle inbound `input` / `layout` / `request` by invoking user callbacks,
   and `heartbeat` by updating liveness.
3. Optionally, the binary envelope for media.

Everything else (roles, overlays, containers, shapes) is optional and
additive — a client that ignores those frames still composes correctly.
