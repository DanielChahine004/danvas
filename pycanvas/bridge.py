"""Bidirectional state sync between Python components and the browser.

A single WebSocket connection carries all components, multiplexed by id.
``broadcast`` is thread-safe: user threads call ``component.update(...)`` which
schedules the actual send onto the server's asyncio event loop.
"""

import asyncio
import json
import math
import random
import secrets
import sys
import threading
import time
import traceback
import uuid
from collections import deque

from fastapi import WebSocketDisconnect

from ._flags import LAYOUT_FLAGS
from .kernel import Kernel


def _diag(msg):
    """Print a server-thread diagnostic line without tripping the Jupyter kernel.

    These lines (viewer connected / disconnected) fire from the asyncio server
    thread, not from a cell. Inside an ipykernel, ``sys.stdout`` is an
    ``OutStream`` that tags every write with the *currently executing* cell's
    parent header and ships it over iopub; a write from a background thread
    therefore gets attributed to whatever cell ran last. During "Run All" that
    cell has already finished, so VS Code's Jupyter extension tries to attach
    the output to a disposed execution and throws "notebook controller is
    DISPOSED", killing every remaining queued cell. Writing to the original
    ``sys.__stdout__`` (the kernel's real fd, surfaced in the kernel log) skips
    the per-cell redirection. In a plain script ``__stdout__ is stdout``, so
    behaviour there is unchanged.
    """
    stream = sys.__stdout__
    if stream is None:  # e.g. pythonw / detached stdout — nothing to write to
        return
    try:
        stream.write(msg + "\n")
        stream.flush()
    except (ValueError, OSError):
        pass  # stream closed mid-shutdown; a diagnostic line isn't worth raising

# Friendly auto-generated identities for connecting viewers (editable in the UI).
_VIEWER_ANIMALS = ["Fox", "Owl", "Bear", "Wolf", "Hawk", "Lynx", "Otter",
                   "Crane", "Seal", "Moth", "Newt", "Wren", "Stoat", "Vole"]
_VIEWER_COLORS = ["#ef4444", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6",
                  "#ec4899", "#14b8a6", "#f97316"]

# An idle browser sends a heartbeat every ~10s (see frontend bridge.js). A
# connection silent for longer than this is treated as dead and reaped, so the
# viewer count can't stay inflated by a hard-dropped tab (the WS keepalive ping
# is disabled server-side; see server.py).
_HEARTBEAT_TIMEOUT = 30.0
_REAP_INTERVAL = 10.0

# Binary-frame type codes (must match the frontend's bridge.js). High-rate media
# rides a binary WebSocket frame instead of base64-in-JSON: a 2-byte header
# (``[type][id-length]``) plus the id, then the raw payload, so the browser feeds
# bytes straight into a Blob/ArrayBuffer with no base64 decode or JSON parse.
# Control messages (register/update/layout/chat/...) stay JSON: they're low-rate
# and self-describing, so binary would cost readability for no real throughput.
BINARY_VIDEO = 1   # payload: JPEG-encoded frame bytes
BINARY_AUDIO = 2   # payload: little-endian int16 PCM samples (interleaved)
BINARY_CUSTOM = 3  # payload: opaque user bytes -> Custom.push_binary -> canvas.onPush
BINARY_REACT = 4   # payload: opaque user bytes -> React.push_binary -> canvas.onFrame


def encode_binary_frame(type_code, comp_id, payload):
    """Pack a component binary frame: ``[type][idLen][id bytes][payload]``.

    ``comp_id`` is ascii-safe (component ids are code-defined names/uuids) and
    capped at 255 bytes so its length fits one header byte. ``payload`` is raw
    ``bytes`` (e.g. JPEG-encoded frame data).
    """
    cid = comp_id.encode("utf-8")[:255]
    return bytes((type_code, len(cid))) + cid + payload


class Bridge:
    def __init__(self):
        self._components = {}  # id -> BaseComponent
        self._arrows = {}  # id -> Arrow
        # Identity of this server *run*. Component ids are minted fresh every
        # run, so a browser whose socket reconnects to a new run (re-running the
        # script, a crash, a hot reload) still shows the previous run's panels —
        # stacked exactly on top of the new ones, dead to input. The run id rides
        # the welcome frame; the frontend clears every managed shape when it sees
        # the id change, so a stale page heals itself on reconnect.
        self._run_id = uuid.uuid4().hex[:8]
        # Wire observers (canvas.on_frame / serve(debug=True)): each is called
        # as fn(direction, msg) with direction "out" (Python -> browser) or "in"
        # (browser -> Python) for every JSON frame and a summary of every binary
        # frame. ``_tap_guard`` makes taps reentrancy-safe: anything a tap itself
        # sends (e.g. updating a debug panel) is not re-tapped, so a tap that
        # drives a component can't recurse.
        self._frame_taps = []
        # Observers of viewer cursor moves (canvas.on_cursor). Kept separate from
        # frame taps: cursors are high-rate and intentionally off the wire-tap
        # path, so they neither flood debug logs nor pay the frame-tap guard.
        self._cursor_taps = []
        self._tap_guard = threading.local()
        self._connections = set()
        self._any_connected = threading.Event()  # set while ≥1 client is connected
        # One asyncio.Lock per live connection. The websockets legacy protocol
        # forbids concurrent writes (its drain() has no internal lock — two
        # coroutines draining a flow-control-paused socket trip an assertion), so
        # every send to a given socket is serialized through its lock. Without
        # this, a high-rate feed (e.g. 30fps video) overlaps sends and crashes.
        self._send_locks = {}  # ws -> asyncio.Lock
        self._loop = None
        self._snapshot_waiters = {}  # reqId -> {"event": Event, "data": ...}
        self._loaded_doc = None  # last full document loaded, replayed on connect
        # Live free-form drawings (tldraw records the *user* draws, not pycanvas
        # panels) keyed by record id. Browsers relay their changes as `draw`
        # diffs; we accumulate the canonical set here, fan it out to the other
        # browsers, and replay it to anyone who connects later.
        self._drawings = {}  # record id -> tldraw record
        # Per-connection viewer identity (id / display name / color) for the
        # presence roster and chat. ``_last_seen`` tracks each socket's most
        # recent inbound message so the reaper can drop silent (dead) ones.
        self._viewers = {}     # ws -> {"id", "name", "color"}
        self._last_seen = {}   # ws -> monotonic timestamp of last message
        self._chat_seq = 0     # monotonic id for chat messages
        self._chat_history = deque(maxlen=100)  # recent chat, replayed on join
        # Components that want to observe chat (the Chat panel's Python handle).
        self._chat_sinks = []
        # Back-reference to the owning Canvas and whether the native UI may spawn
        # an ephemeral Inspector. Set by Canvas (``_canvas`` in __init__,
        # ``_ui_inspector`` in serve); the flag is advertised to each browser in
        # the welcome frame so the button only shows where it's allowed.
        self._canvas = None
        self._ui_inspector = False
        # When True, browsers report their pointer position (in canvas/page
        # coords) so Python can read it off the roster as ``viewer["cursor"]``.
        # Advertised in the welcome frame; gated like ``_ui_inspector`` (default
        # on only for a private local bind) since it's viewer telemetry.
        self._cursors = False
        # True when this process is a hot-reload restart (serve sets it). It rides
        # the welcome frame so a reconnecting browser — whose page never reloaded,
        # only its socket — drops the previous run's panels before this run's are
        # replayed. Without it, panels (which get fresh ids each run) pile up: the
        # old shapes linger beside the new ones. See serve(hot_reload=True).
        self._reload = False
        # Optional viewport/navigation config (initial camera, zoom limits, pan/
        # zoom lock, UI chrome visibility). Sent to each browser in `welcome` and
        # applied to tldraw on connect. ``None`` leaves every default in place.
        self._view = None
        # Per-client view state: viewer_id -> view_dict. When set, overrides the
        # global _view for that specific client (merged with global defaults).
        self._view_per_client = {}
        # Per-role view state: role -> view_dict. Applied to a viewer on connect
        # by their login role. Precedence is global < per-role < per-client, so a
        # client-specific override still wins. Set via canvas.set_view(roles=...).
        self._view_per_role = {}
        # Conflated ("latest" queue policy) send state. For components that opt
        # out of FIFO, we keep only the newest pending value per (socket,
        # component, channel) and a flag marking whether a sender is draining it,
        # so a fast producer can't pile a backlog onto a slow client. Guarded by a
        # plain lock since producers are user threads and the sender is the loop.
        self._conflate_pending = {}   # (ws, comp_id, kind) -> (kind, msg|bytes)
        self._conflate_active = set()  # (ws, comp_id, kind) with a live sender
        self._conflate_lock = threading.Lock()
        # Pending file downloads (the Download panel). Maps an unguessable token
        # to ``(filename, source, expiry)`` where ``source`` is a filesystem path
        # or ``bytes``; the ``/__download__/<token>`` HTTP route streams it. Kept
        # for a TTL window (not single-use, so a HEAD+GET or a retry both work)
        # then purged so the table can't grow without bound. Guarded by a plain
        # lock: tokens are minted on the input-dispatch thread and consumed on the
        # event loop.
        self._downloads = {}        # token -> (filename, source, expiry monotonic)
        self._download_lock = threading.Lock()
        # Upload targets (the Upload panel). Maps a panel's unguessable token to
        # the component, so the ``/__upload__/<token>`` route can find which panel
        # an incoming file belongs to and fire its callbacks. Tokens are stable
        # for the panel's lifetime (the button is reused), unlike one-off download
        # tokens. Plain dict: writes happen on the loop/insert thread, reads on the
        # event loop; both are cheap and a stale entry is harmless.
        self._uploads = {}          # token -> Upload component
        # User input/layout callbacks (``on_change``/``on_layout`` and the
        # component routers) run here, on a single FIFO worker thread, instead of
        # on the asyncio event loop. A slow or blocking callback (a sleep, an HTTP
        # call, heavy compute -- exactly what "drag slider -> move robot" handlers
        # do) would otherwise freeze the loop and stall rendering and every other
        # viewer. One ordered thread preserves per-message order (so a slider drag
        # settles on its last value) while keeping the loop free. Lazy: no thread
        # until the first inbound message.
        self._dispatch = Kernel()

    # -- wire observation ------------------------------------------------------
    def add_frame_tap(self, fn):
        """Register ``fn(direction, msg)`` to observe every WebSocket frame.

        ``direction`` is ``"out"`` (Python -> browser) or ``"in"`` (browser ->
        Python); ``msg`` is the frame's dict (binary media frames arrive as a
        ``{"type": "binary", ...}`` summary). Taps observe; they must not block
        (they run inline on the sending/receiving path). A tap may safely drive
        components — frames a tap itself causes are not re-tapped.
        """
        self._frame_taps.append(fn)
        return fn

    def remove_frame_tap(self, fn):
        if fn in self._frame_taps:
            self._frame_taps.remove(fn)

    def add_cursor_tap(self, fn):
        """Register ``fn(viewer)`` to observe viewer cursor moves (on_cursor).

        ``viewer`` is a snapshot dict with ``id``/``name``/``color`` and the new
        ``cursor`` (``{"x", "y"}`` in canvas coords). Runs off the event loop on
        the input-dispatch thread, but it is high-rate — keep it cheap.
        """
        self._cursor_taps.append(fn)
        return fn

    def remove_cursor_tap(self, fn):
        if fn in self._cursor_taps:
            self._cursor_taps.remove(fn)

    def _tap_cursor(self, viewer):
        for fn in list(self._cursor_taps):
            try:
                fn(viewer)
            except Exception:
                traceback.print_exc()

    def _tap_frame(self, direction, msg):
        """Hand one frame to every tap, guarding against tap-driven recursion."""
        if not self._frame_taps or getattr(self._tap_guard, "active", False):
            return
        self._tap_guard.active = True
        try:
            for fn in list(self._frame_taps):
                try:
                    fn(direction, msg)
                except Exception:
                    traceback.print_exc()
        finally:
            self._tap_guard.active = False

    def _tap_binary(self, data):
        """Report a binary media frame to taps as a small JSON-able summary."""
        if not self._frame_taps:
            return
        try:  # header: [type][idLen][id bytes][payload] (see encode_binary_frame)
            kind = {BINARY_VIDEO: "video", BINARY_AUDIO: "audio"}.get(data[0])
            cid = data[2:2 + data[1]].decode("utf-8", "replace")
            self._tap_frame("out", {"type": "binary", "id": cid,
                                    "media": kind, "bytes": len(data)})
        except Exception:
            pass

    # -- wiring --------------------------------------------------------------
    def add_component(self, component):
        self._components[component.id] = component

    def remove_component(self, component_id):
        """Forget a component and tell connected clients to drop its panel."""
        self._components.pop(component_id, None)
        # Drop any upload token pointing at this component so the map can't grow.
        for tok in [t for t, c in self._uploads.items()
                    if getattr(c, "id", None) == component_id]:
            self._uploads.pop(tok, None)
        self.broadcast({"type": "remove", "id": component_id})

    def reorder_component(self, component_id, op):
        """Restack a panel (front/back/forward/backward) on every live client.

        ``front``/``back`` also move the component to the end/start of the replay
        registry, so a client that connects or reloads rebuilds the panels in the
        new stacking order (later-registered shapes sit on top). ``forward`` /
        ``backward`` are a live one-step nudge only: tldraw's overlap-aware step
        has no faithful registry equivalent, so it isn't persisted across reload.
        """
        comp = self._components.get(component_id)
        if comp is not None and op in ("front", "back"):
            self._components.pop(component_id)
            if op == "front":
                self._components[component_id] = comp  # last in -> top of stack
            else:
                self._components = {component_id: comp, **self._components}
        self.broadcast({"type": "order", "id": component_id, "op": op})

    def add_arrow(self, arrow):
        """Store an ``Arrow`` and broadcast its register message to live clients.

        The object (not a snapshot) is kept so reconnecting clients replay the
        arrow with its current props after their panels are recreated.
        """
        self._arrows[arrow.id] = arrow
        self.broadcast(arrow.register_message())

    def remove_arrow(self, arrow_id):
        """Forget an arrow and tell connected clients to drop it."""
        self._arrows.pop(arrow_id, None)
        self.broadcast({"type": "remove", "id": arrow_id})

    def set_loop(self, loop):
        self._loop = loop
        loop.create_task(self._reap_loop())

    def register_message(self, component, role=None, client_id=None):
        """Build the ``register`` message for a component, including placement.

        ``role``/``client_id`` identify the connecting viewer so a component with
        per-viewer prop overlays (see :meth:`React.update` ``roles=``) replays the
        slice that viewer should see; omit them for the shared props.
        """
        msg = {
            "type": "register",
            "id": component.id,
            "component": component.component,
            "props": component.register_props_for(role, client_id),
        }
        pos = getattr(component, "_position", None)
        if pos is not None:
            msg["x"], msg["y"] = pos
        rot = getattr(component, "_rotation", None)
        if rot is not None:
            msg["rotation"] = math.radians(rot)
        # Lock/chrome flags: send each only when it differs from its default
        # (e.g. locked=True, or draggable=False as movable=False), matching
        # set_layout's payload and the frontend's lockMeta. The Python names
        # (draggable/operable/grabbable) map to the wire keys
        # (movable/interactive/selectable) via the LAYOUT_FLAGS table, the single
        # source of truth shared with base.py and canvas.py.
        for flag in LAYOUT_FLAGS.values():
            value = getattr(component, flag.attr, flag.default)
            if value != flag.default:
                msg[flag.wire] = value
        # Per-viewer layout overlay (set_layout(roles=) / a drag written to the
        # viewer's own layer): merge it on top of the base geometry. x/y/rotation
        # and the lock flags are top-level register fields; w/h are shape props.
        overlay = (component._layout_overlay_for(role, client_id)
                   if hasattr(component, "_layout_overlay_for") else {})
        if overlay:
            if "x" in overlay:
                msg["x"] = overlay["x"]
            if "y" in overlay:
                msg["y"] = overlay["y"]
            if "rotation" in overlay:
                msg["rotation"] = math.radians(overlay["rotation"])
            if "w" in overlay:
                msg["props"]["w"] = overlay["w"]
            if "h" in overlay:
                msg["props"]["h"] = overlay["h"]
            for name, flag in LAYOUT_FLAGS.items():
                if name in overlay:
                    msg[flag.wire] = bool(overlay[name])
        return msg

    def register_live(self, component, only_roles=None):
        """Push a newly-added component to connected clients who may see it.

        Role-aware: clients whose role is not in the component's ``_roles``
        list are skipped. Used for components inserted after the server is
        already running (e.g. from a Jupyter cell). Fresh connections still get
        the full replay via ``handle_connection``; this covers live clients.

        ``only_roles`` (a set/list of role names) further narrows the push to
        viewers in those roles — used by :meth:`BaseComponent.add_role` so newly
        allowed roles get the panel without re-registering it to viewers who
        already had it.
        """
        roles = getattr(component, "_roles", [])
        lock_for = getattr(component, "_lock_for", [])
        state = component.state_payload()
        if self._loop is None:
            return
        # Build the register frame once when the panel has no per-viewer overlays
        # (the common case) — only re-derive per viewer when there's a role/client
        # override to merge, so a fan-out to N viewers isn't N identical builds.
        shared_reg = (None if component._has_viewer_overlays()
                      else self.register_message(component))
        for ws, viewer in list(self._viewers.items()):
            role = viewer.get("role")
            if roles and role not in roles:
                continue
            if only_roles is not None and role not in only_roles:
                continue
            reg = shared_reg if shared_reg is not None else self.register_message(
                component, role=role, client_id=viewer.get("id"))
            asyncio.run_coroutine_threadsafe(self._safe_send(ws, reg), self._loop)
            if state:
                asyncio.run_coroutine_threadsafe(
                    self._safe_send(ws, {"type": "update", "id": component.id,
                                         "payload": state}),
                    self._loop,
                )
            if lock_for and role in lock_for:
                asyncio.run_coroutine_threadsafe(
                    self._safe_send(ws, {"type": "update", "id": component.id,
                                         "payload": {"operable": False}}),
                    self._loop,
                )

    # -- connection lifecycle (runs in the event loop) -----------------------
    async def handle_connection(self, ws, role=None):
        await ws.accept()
        self._connections.add(ws)
        self._any_connected.set()
        self._send_locks[ws] = asyncio.Lock()
        self._last_seen[ws] = time.monotonic()
        # A reconnecting browser re-sends its identity (id/name/color) as query
        # params so a flapping tab keeps one viewer instead of churning new ones.
        # ``role`` still comes from the trusted session, never the client.
        qp = getattr(ws, "query_params", {})
        requested = {"id": qp.get("vid"), "name": qp.get("vname"),
                     "color": qp.get("vcolor")}
        viewer = self._make_viewer(role=role, requested=requested)
        self._viewers[ws] = viewer
        self._broadcast_roster()  # tell everyone a viewer joined
        try:
            # Tell this client who it is, so it can label its own chat messages
            # and prefill the editable name field.
            view_for_client = self._view_for(viewer["id"], role)
            await self._send(ws, {"type": "welcome", "you": viewer,
                                  "uiInspector": self._ui_inspector,
                                  "cursors": self._cursors,
                                  "view": view_for_client,
                                  "runId": self._run_id,
                                  "reload": self._reload})
            # Replay recent chat so a fresh viewer sees the conversation so far.
            for entry in self._chat_history:
                await self._send(ws, entry)
            # Replay full state to the freshly connected client, filtered by role.
            for comp in self._components.values():
                roles = getattr(comp, "_roles", [])
                if roles and role not in roles:
                    continue  # this panel is not visible to this role
                await self._send(ws, self.register_message(
                    comp, role=role, client_id=viewer["id"]))
                state = comp.state_payload()
                if state:
                    await self._send(
                        ws, {"type": "update", "id": comp.id, "payload": state}
                    )
                lock_for = getattr(comp, "_lock_for", [])
                if lock_for and role in lock_for:
                    await self._send(
                        ws, {"type": "update", "id": comp.id,
                             "payload": {"operable": False}}
                    )
            # Arrows bind to panels, so replay them after every panel exists.
            for arrow in self._arrows.values():
                await self._send(ws, arrow.register_message())
            # Replay the live free-form drawings as a single "added" diff so a
            # fresh (or reloaded) browser sees what others have drawn.
            if self._drawings:
                await self._send(ws, {
                    "type": "draw",
                    "diff": {"added": self._drawings, "updated": {}, "removed": {}},
                })
            # If a full canvas was loaded, replay it last so reloads keep it
            # (it replaces the document, incl. any user drawings it contained).
            if self._loaded_doc is not None:
                await self._send(
                    ws, {"type": "load_snapshot", "data": self._loaded_doc}
                )
            # One diagnostic line per connection, so "nothing happens" debugging
            # starts with evidence: the viewer reached the server and what state
            # it was seeded with.
            _diag(f"[pycanvas] viewer '{viewer['name']}' connected "
                  f"(replayed {len(self._components)} panels, "
                  f"{len(self._arrows)} arrows)")

            while True:
                raw = await ws.receive_text()
                self._on_message(ws, raw)
        except WebSocketDisconnect:
            pass
        except Exception:
            # Same background-thread hazard as _diag: route the trace to the
            # kernel's real stderr so it can't be misattributed to a finished
            # cell and dispose the controller mid "Run All".
            if sys.__stderr__ is not None:
                traceback.print_exc(file=sys.__stderr__)
        finally:
            self._connections.discard(ws)
            if not self._connections:
                self._any_connected.clear()
            self._send_locks.pop(ws, None)
            self._drop_conflate(ws)
            gone = self._viewers.pop(ws, None)
            # Viewer ids are minted fresh per connection and never reused, so a
            # per-client view override for a departed viewer can never apply
            # again — drop it so the map doesn't grow unbounded.
            if gone is not None:
                self._view_per_client.pop(gone["id"], None)
                # Tell peers to drop this viewer's rendered cursor.
                if self._cursors:
                    self.broadcast({"type": "cursor_gone", "id": gone["id"]})
                _diag(f"[pycanvas] viewer '{gone['name']}' disconnected")
            self._last_seen.pop(ws, None)
            self._broadcast_roster()  # tell everyone a viewer left

    def _make_viewer(self, role=None, requested=None):
        """Mint a viewer identity (id + friendly editable name + color + role).

        ``requested`` carries a browser-supplied id/name/color from a reconnect
        (see handle_connection): when its id looks valid it's reused so a tab that
        flaps keeps a single, stable identity instead of being renamed each time.
        These three fields are client-reported either way (only ``role`` is
        trusted), so honouring them changes no trust boundary.
        """
        rid = (requested or {}).get("id")
        if rid and rid.isalnum() and len(rid) <= 32:
            rname = ((requested or {}).get("name") or "").strip()[:40]
            rcolor = (requested or {}).get("color") or ""
            color = (rcolor if len(rcolor) == 7 and rcolor[0] == "#"
                     and all(c in "0123456789abcdefABCDEF" for c in rcolor[1:])
                     else random.choice(_VIEWER_COLORS))
            return {"id": rid, "name": rname or random.choice(_VIEWER_ANIMALS),
                    "color": color, "cursor": None, "role": role}
        existing = {v["name"] for v in self._viewers.values()}
        animal = random.choice(_VIEWER_ANIMALS)
        name = animal
        n = 2
        while name in existing:  # keep auto-names distinct; user can rename
            name = f"{animal} {n}"
            n += 1
        color = random.choice(_VIEWER_COLORS)
        # ``cursor`` is the viewer's last-known pointer position in canvas/page
        # coords (``{"x", "y"}``), or None until they move it (and only ever
        # populated when cursor reporting is enabled). Read it via canvas.viewers.
        # ``role`` is the access level granted at login (None when no passwords set).
        return {"id": uuid.uuid4().hex[:8], "name": name, "color": color,
                "cursor": None, "role": role}

    def _broadcast_roster(self):
        """Push the live-viewer roster (and count) to every connected browser.

        Carries the full list of viewers (id / name / color) so the UI can show
        who's here and the chat can colour names; ``count`` is kept for the
        presence badge. Sent on every join/leave/rename. Safe before the loop
        exists (broadcast no-ops then).
        """
        viewers = list(self._viewers.values())
        self.broadcast({"type": "presence", "count": len(viewers),
                        "viewers": viewers})

    # -- chat / identity (browser <-> browser, relayed through the server) ----
    def _rename_viewer(self, ws, name):
        """Apply a viewer's editable display name, then re-broadcast the roster."""
        viewer = self._viewers.get(ws)
        if viewer is None:
            return
        clean = (name or "").strip()[:24]
        if not clean:
            return
        viewer["name"] = clean
        self._broadcast_roster()

    def _handle_chat(self, ws, text):
        """Stamp a chat line with the sender's identity and fan it out to all.

        The identity is taken from the server's record of the socket (not the
        client's claim), so a name can't be spoofed. The message is appended to
        the replay history and also delivered to any Python ``Chat`` sinks.
        """
        viewer = self._viewers.get(ws)
        if viewer is None:
            return
        body = (text or "").strip()
        if not body:
            return
        self._chat_seq += 1
        entry = {
            "type": "chat",
            "msgId": self._chat_seq,
            "id": viewer["id"],
            "name": viewer["name"],
            "color": viewer["color"],
            "text": body[:2000],
            "ts": time.time(),
        }
        self._chat_history.append(entry)
        self.broadcast(entry)
        for sink in self._chat_sinks:
            try:
                sink(entry)
            except Exception:
                traceback.print_exc()

    def post_chat(self, text, name="host", color="#64748b"):
        """Inject a chat message from Python (e.g. a system/host announcement)."""
        body = (text or "").strip()
        if not body:
            return
        self._chat_seq += 1
        entry = {
            "type": "chat", "msgId": self._chat_seq, "id": "host",
            "name": name, "color": color, "text": body[:2000], "ts": time.time(),
        }
        self._chat_history.append(entry)
        self.broadcast(entry)
        for sink in self._chat_sinks:
            try:
                sink(entry)
            except Exception:
                traceback.print_exc()

    def add_chat_sink(self, fn):
        """Register a callback fired with every chat entry (Chat panel handle)."""
        self._chat_sinks.append(fn)

    def remove_chat_sink(self, fn):
        if fn in self._chat_sinks:
            self._chat_sinks.remove(fn)

    # -- file downloads (Download panel <-> /__download__ route) --------------
    def register_download(self, filename, source, ttl=300):
        """Stash ``source`` under a fresh token and return it (any-thread safe).

        ``source`` is a filesystem path or ``bytes``; the ``/__download__/<token>``
        route streams it to the browser as an attachment named ``filename``. The
        token is unguessable and expires after ``ttl`` seconds, so a leaked URL
        can't be replayed indefinitely. Expired tokens are purged opportunistically
        on each register/consume.
        """
        token = secrets.token_urlsafe(24)
        with self._download_lock:
            self._purge_downloads()
            self._downloads[token] = (filename, source, time.monotonic() + ttl)
        return token

    def take_download(self, token):
        """Resolve a download token to ``(filename, source)`` or ``None``.

        Returns ``None`` if the token is unknown or has expired. Not single-use:
        the entry stays until its TTL lapses, so a browser that issues a HEAD then
        GET (or retries) still succeeds.
        """
        with self._download_lock:
            self._purge_downloads()
            item = self._downloads.get(token)
        if item is None:
            return None
        filename, source, _exp = item
        return filename, source

    def _purge_downloads(self):
        """Drop expired download tokens. Call with ``_download_lock`` held."""
        now = time.monotonic()
        expired = [t for t, (_, _, exp) in self._downloads.items() if exp <= now]
        for t in expired:
            self._downloads.pop(t, None)

    # -- file uploads (Upload panel <-> /__upload__ route) -------------------
    def register_upload(self, token, component):
        """Bind an upload ``token`` to the panel that receives its files."""
        self._uploads[token] = component

    def upload_component(self, token):
        """Resolve an upload token to its panel, or ``None`` if unknown."""
        return self._uploads.get(token)

    def deliver_upload(self, component, info, viewer=None):
        """Fire a panel's upload handler off the event loop (any-thread safe).

        ``info`` is the server-built file dict (``name``/``size``/``content_type``
        and one of ``data``/``path``); ``viewer`` is the uploader's identity (see
        :meth:`resolve_viewer`). Runs on the input-dispatch thread, like every
        other user callback, so a slow handler can't stall rendering.
        """
        self._dispatch.submit(
            lambda: component._receive_upload(info, viewer or {})
        )

    def resolve_viewer(self, viewer_id, role=None):
        """Build the uploader identity dict for an upload handler.

        ``role`` is the server-trusted access level from the HTTP auth session —
        always present (``None`` when no passwords are set) and safe to gate on.
        ``viewer_id`` is the browser's self-reported roster id; when it matches a
        *currently connected* viewer, the live ``id``/``name``/``color`` are
        merged in for attribution. A stale or forged id simply doesn't resolve
        (you still get ``role``), and because name/colour are read from the server
        roster — never from the client — the only thing a client can claim is the
        id of another viewer who is actually online. Don't trust name/id for
        authorization; use ``role`` for that.
        """
        info = {"role": role}
        if viewer_id:
            for v in list(self._viewers.values()):
                if v.get("id") == viewer_id:
                    info["id"] = v.get("id")
                    info["name"] = v.get("name")
                    info["color"] = v.get("color")
                    break
        return info

    async def _reap_loop(self):
        """Drop connections that have gone silent past the heartbeat deadline.

        Browsers send a periodic heartbeat; one that stops (a hard-closed or
        network-dropped tab) is closed here so the viewer count and roster don't
        stay inflated — the WS keepalive ping is disabled (see server.py), so
        without this a dead socket lingers until the next failed send.
        """
        while True:
            await asyncio.sleep(_REAP_INTERVAL)
            try:
                now = time.monotonic()
                dead = [ws for ws in list(self._connections)
                        if now - self._last_seen.get(ws, now) > _HEARTBEAT_TIMEOUT]
                for ws in dead:
                    try:
                        await ws.close(code=1001)
                    except Exception:
                        pass
                    # handle_connection's finally normally cleans up, but force
                    # it here too in case the receive loop is wedged.
                    self._connections.discard(ws)
                    self._send_locks.pop(ws, None)
                    self._drop_conflate(ws)
                    gone = self._viewers.pop(ws, None)
                    if gone is not None:
                        self._view_per_client.pop(gone["id"], None)
                        self._last_seen.pop(ws, None)
                        self._broadcast_roster()
            except Exception:
                traceback.print_exc()

    def _on_message(self, ws, raw):
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        # Any inbound frame proves the socket is alive — refresh its deadline.
        self._last_seen[ws] = time.monotonic()
        kind = msg.get("type")
        if kind == "heartbeat":
            return  # liveness only; timestamp already refreshed above
        if kind == "cursor":
            # High-rate pointer telemetry: return *before* the frame tap so cursor
            # spam never floods debug logs or on_frame taps. Already throttled +
            # dead-banded client-side; the server conflates per sender per viewer.
            if self._cursors:
                v = self._viewers.get(ws)
                x, y = msg.get("x"), msg.get("y")
                if v is not None and isinstance(x, (int, float)) \
                        and isinstance(y, (int, float)):
                    x, y = float(x), float(y)
                    # 1) Store newest on the roster entry for Python to read.
                    v["cursor"] = {"x": x, "y": y}
                    # 2) Relay to *other* viewers for peer rendering, tagged with
                    #    the sender's identity/colour. Conflated per sender (a slow
                    #    viewer only gets the latest); tap=False keeps the relay off
                    #    the wire-debug path.
                    self.broadcast_conflated(
                        f"cursor:{v['id']}", exclude=ws, tap=False,
                        msg={"type": "cursor", "id": v["id"], "x": x, "y": y,
                             "color": v["color"], "name": v["name"]},
                    )
                    # 3) Fan out to Python cursor observers off the loop thread.
                    if self._cursor_taps:
                        self._dispatch.submit(
                            lambda vv=dict(v): self._tap_cursor(vv)
                        )
            return
        self._tap_frame("in", msg)
        if kind == "set_name":
            self._rename_viewer(ws, msg.get("name"))
            return
        if kind == "chat":
            self._handle_chat(ws, msg.get("text"))
            return
        if kind == "ui":
            # Native-UI request (e.g. the toolbar Inspector toggle). Gated by the
            # same flag advertised in `welcome`, and only ever touches the canvas
            # when one is attached (the merge host has none and ignores it).
            if self._ui_inspector and self._canvas is not None:
                if msg.get("action") == "toggle_inspector":
                    try:
                        self._canvas._toggle_ui_inspector()
                    except Exception:
                        traceback.print_exc()
            return
        if kind == "input":
            comp = self._components.get(msg.get("id"))
            if comp is not None:
                payload = msg.get("payload") or {}
                # Run the (user-authored) handler on the dispatch thread, never on
                # the event loop -- a blocking callback can't stall rendering or
                # other viewers. The state echo happens there too, after handling.
                self._dispatch.submit(
                    lambda c=comp, p=payload: self._dispatch_input(c, p, ws)
                )
        elif kind == "layout":
            # User moved/resized a panel in the browser; sync Python's state.
            comp = self._components.get(msg.get("id"))
            if comp is not None:
                self._dispatch.submit(
                    lambda c=comp, m=msg: self._dispatch_layout(c, m, ws)
                )
        elif kind == "draw":
            # A browser relayed a free-form drawing change. Fold it into the
            # canonical record set and echo it to the other browsers so every
            # open view converges (re-applying its own diff is idempotent).
            diff = msg.get("diff") or {}
            self._apply_draw(diff)
            self.broadcast({"type": "draw", "diff": diff})
        elif kind == "request":
            # A panel's ``canvas.request(data)`` — the awaitable twin of input.
            # Answer it off the loop (a slow handler can't stall rendering) and
            # reply correlated by reqId.
            comp = self._components.get(msg.get("id"))
            if comp is not None:
                self._dispatch.submit(
                    lambda c=comp, r=msg.get("reqId"), d=msg.get("data"):
                    self._dispatch_request(c, r, d, ws)
                )
        elif kind == "snapshot":
            # Reply to a request_snapshot; hand the document to the waiter.
            waiter = self._snapshot_waiters.get(msg.get("reqId"))
            if waiter is not None:
                waiter["data"] = msg.get("data")
                waiter["event"].set()

    def _dispatch_input(self, comp, payload, ws):
        """Run a component's input handler (off the loop) and echo its state.

        Called on the dispatch thread. Passes the viewer dict (which includes
        ``role``) to ``_handle_input`` so callbacks can inspect who triggered
        the action. Echoes resulting state to other clients.
        """
        viewer = self._viewers.get(ws, {})
        comp._handle_input(payload, viewer)
        state = comp.state_payload()
        if state:
            self.broadcast(
                {"type": "update", "id": comp.id, "payload": state}, exclude=ws
            )

    def _dispatch_request(self, comp, req_id, data, ws=None):
        """Answer a panel's ``canvas.request`` (off the loop) and reply by reqId.

        Runs the component's request handler on the dispatch thread, then
        broadcasts a ``response`` correlated by ``reqId`` — the requesting tab
        resolves its Promise; other tabs (which don't hold that reqId) ignore it.
        The requester's viewer identity is passed through so an ``on_request``
        handler that declares a second parameter learns who asked. A panel with no
        request handler, a handler that raises, or a return value that isn't
        JSON-serialisable all come back as an ``error`` (rejecting the Promise)
        rather than hanging the caller.
        """
        handle = getattr(comp, "_handle_request", None)
        if handle is None:
            self._reply(req_id, error="this panel does not accept requests")
            return
        viewer = self._viewers.get(ws, {})
        try:
            result = handle(data, viewer)
            json.dumps(result)  # surface a non-serialisable reply as a clean error
        except Exception as exc:
            traceback.print_exc()
            self._reply(req_id, error=repr(exc))
            return
        self._reply(req_id, result=result)

    def _reply(self, req_id, result=None, error=None):
        """Broadcast a ``response`` for ``req_id`` (the frontend correlates it)."""
        msg = {"type": "response", "reqId": req_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self.broadcast(msg)

    def _dispatch_layout(self, comp, msg, ws=None):
        """Apply a user move/resize (off the loop) and echo the new geometry.

        Echoes to every client (a second browser, or a merge host) as an
        ``update`` -- the server->browser form the frontend applies. The fields
        already carry the wire units the frontend expects (canvas x/y, radian
        rotation). The mover's viewer identity is threaded through so an
        ``on_layout`` handler with a second parameter learns who rearranged it.
        """
        comp._apply_remote_layout(msg, self._viewers.get(ws, {}))
        geom = {k: msg[k] for k in ("x", "y", "w", "h", "rotation")
                if msg.get(k) is not None}
        if geom:
            self.broadcast({"type": "update", "id": comp.id, "payload": geom})

    async def _send(self, ws, msg):
        """Send one frame, serialized against any other send to this socket."""
        if ws not in self._connections:
            return  # connection already torn down
        # Lazily create the per-connection lock so any code path that registers a
        # connection (incl. subclasses overriding handle_connection, e.g. the
        # merge host) gets serialized sends without having to know about it. Safe
        # without a guard: there's no await between get and setdefault.
        lock = self._send_locks.get(ws)
        if lock is None:
            lock = self._send_locks.setdefault(ws, asyncio.Lock())
        async with lock:
            await ws.send_text(json.dumps(msg))

    async def _send_bytes(self, ws, data):
        """Send one binary frame, serialized against any other send (text or
        binary) to this socket — the websockets drain forbids overlapping
        writes, so binary media must share the same per-connection lock."""
        if ws not in self._connections:
            return
        lock = self._send_locks.get(ws)
        if lock is None:
            lock = self._send_locks.setdefault(ws, asyncio.Lock())
        async with lock:
            await ws.send_bytes(data)

    # -- outbound (thread-safe) ----------------------------------------------
    def _emit(self, targets, msg):
        """Schedule ``msg`` to each websocket in ``targets`` — the shared tail of
        :meth:`broadcast` / :meth:`send_to_role` / :meth:`send_to_client`.

        A no-op before the loop exists (replay carries the state on connect); each
        send is wrapped in :meth:`_safe_send` so a dead socket is dropped rather
        than raising. ``targets`` is materialised by the caller (a snapshot of the
        connection/viewer map) so a concurrent connect/disconnect can't mutate it
        mid-iteration.
        """
        if self._loop is None:
            return
        for ws in targets:
            asyncio.run_coroutine_threadsafe(self._safe_send(ws, msg), self._loop)

    def broadcast(self, msg, exclude=None):
        """Send ``msg`` to every connected client. Safe to call from any thread.

        ``exclude`` skips one connection (the originator of a change), used to
        avoid echoing a browser's own input straight back to it.
        """
        if self._loop is None:
            return  # not serving yet; connection replay will carry the state
        self._tap_frame("out", msg)
        self._emit([ws for ws in list(self._connections) if ws is not exclude], msg)

    async def _safe_send(self, ws, msg):
        try:
            await self._send(ws, msg)
        except Exception:
            self._connections.discard(ws)
            self._send_locks.pop(ws, None)

    def broadcast_binary(self, data, exclude=None):
        """Send a pre-encoded binary frame to every client. Any-thread safe.

        Mirrors :meth:`broadcast` but for ``bytes`` (high-rate media). A client
        that hasn't mounted the target panel yet simply has no handler for the
        frame and drops it — the next frame lands once it's ready.
        """
        if self._loop is None:
            return
        self._tap_binary(data)
        for ws in list(self._connections):
            if ws is exclude:
                continue
            asyncio.run_coroutine_threadsafe(
                self._safe_send_binary(ws, data), self._loop
            )

    async def _safe_send_binary(self, ws, data):
        try:
            await self._send_bytes(ws, data)
        except Exception:
            self._connections.discard(ws)
            self._send_locks.pop(ws, None)

    def send_to_client(self, viewer_id, msg):
        """Send ``msg`` to the one client with this viewer id. Any-thread safe.

        A no-op if no live connection carries that id (it has disconnected, or
        the id is stale). The viewer map is snapshotted before scanning so a
        concurrent connect/disconnect on the loop thread can't trip a
        "dict changed size" error -- mirrors :meth:`broadcast`.
        """
        ws = next((s for s, v in list(self._viewers.items())
                   if v.get("id") == viewer_id), None)
        self._emit((ws,) if ws is not None else (), msg)

    def send_to_role(self, role, msg):
        """Send ``msg`` to every connected client whose login role matches.

        Any-thread safe; a no-op when no one is connected under ``role``. The
        viewer map is snapshotted before scanning, like :meth:`send_to_client`.
        """
        self._emit([ws for ws, v in list(self._viewers.items())
                    if v.get("role") == role], msg)

    def _view_for(self, viewer_id, role):
        """Merge the view layers that apply to one viewer, newest layer wins.

        Precedence is global (:attr:`_view`) < per-role < per-client, so a
        client-specific override beats a role default beats the global view.
        Returns ``None`` when no layer is set (the welcome frame treats a missing
        view as "leave tldraw's defaults").
        """
        merged, have = {}, False
        for layer in (self._view, self._view_per_role.get(role),
                      self._view_per_client.get(viewer_id)):
            if layer:
                merged.update(layer)
                have = True
        return merged if have else None

    @staticmethod
    def _merge_update(existing, new_msg):
        """Fold a new update message into a pending one, newest value per key.

        Merging (not replacing) keeps partial updates from being lost: a pending
        ``set_layout(x=1)`` followed by ``set_layout(w=5)`` ends up carrying both.
        Top-level fields other than ``payload`` take the newest message's value.
        """
        if existing is None:
            return {**new_msg, "payload": dict(new_msg.get("payload") or {})}
        existing.setdefault("payload", {}).update(new_msg.get("payload") or {})
        for k, v in new_msg.items():
            if k != "payload":
                existing[k] = v
        return existing

    def broadcast_conflated(self, comp_id, *, msg=None, data=None, exclude=None,
                            tap=True):
        """Broadcast an update under the ``latest`` queue policy.

        Keeps only the most recent pending value per viewer for this component,
        dropping stale ones: dict updates merge newest-per-key (so partial
        updates survive), binary frames replace wholesale. The per-viewer backlog
        is bounded to one in-flight send plus one pending value, so a fast
        producer (e.g. a camera) can't accumulate latency on a slow client.

        Pass exactly one of ``msg`` (a dict to JSON-send) or ``data`` (bytes).
        """
        if self._loop is None:
            return
        # tap=False suppresses wire-debug observation for high-rate internal
        # relays (cursor positions) that would otherwise flood on_frame/debug.
        if tap:
            if data is not None:
                self._tap_binary(data)
            else:
                self._tap_frame("out", msg)
        kind = "bin" if data is not None else "msg"
        for ws in list(self._connections):
            if ws is exclude:
                continue
            key = (ws, comp_id, kind)
            with self._conflate_lock:
                if kind == "bin":
                    self._conflate_pending[key] = ("bin", data)
                else:
                    prev = self._conflate_pending.get(key)
                    merged = self._merge_update(prev[1] if prev else None, msg)
                    self._conflate_pending[key] = ("msg", merged)
                if key in self._conflate_active:
                    continue  # a sender is draining this slot; it'll see the latest
                self._conflate_active.add(key)
            asyncio.run_coroutine_threadsafe(
                self._conflated_sender(key), self._loop
            )

    async def _conflated_sender(self, key):
        """Drain a conflated slot until empty, sending only its latest value.

        New values that land while a send is in flight overwrite/merge the slot,
        so intermediate ones are skipped rather than queued.
        """
        ws = key[0]
        while True:
            with self._conflate_lock:
                item = self._conflate_pending.pop(key, None)
                if item is None:
                    self._conflate_active.discard(key)
                    return
            kind, val = item
            try:
                if kind == "bin":
                    await self._send_bytes(ws, val)
                else:
                    await self._send(ws, val)
            except Exception:
                self._connections.discard(ws)
                self._send_locks.pop(ws, None)
                with self._conflate_lock:
                    self._conflate_pending.pop(key, None)
                    self._conflate_active.discard(key)
                return

    def _drop_conflate(self, ws):
        """Forget any conflated state for a closed connection."""
        with self._conflate_lock:
            for key in [k for k in self._conflate_pending if k[0] is ws]:
                self._conflate_pending.pop(key, None)
            for key in [k for k in self._conflate_active if k[0] is ws]:
                self._conflate_active.discard(key)

    def _apply_draw(self, diff):
        """Fold a tldraw store diff into the canonical free-form record set.

        ``added``/``updated`` carry full records (``updated`` as ``[from, to]``
        pairs, of which we keep the new value); ``removed`` carries the dropped
        records by id. This mirrors :meth:`tldraw Store.applyDiff` on the wire so
        the server's cache stays in step with every browser.
        """
        for rid, rec in (diff.get("added") or {}).items():
            self._drawings[rid] = rec
        for rid, pair in (diff.get("updated") or {}).items():
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                self._drawings[rid] = pair[1]
        for rid in (diff.get("removed") or {}):
            self._drawings.pop(rid, None)

    def _panel_shape_ids(self):
        """tldraw shape ids of every pycanvas-managed panel and arrow.

        The frontend keys shapes as ``shape:<component id>``; these are the
        shapes we own (panels + connector arrows) and want to exclude from a
        saved canvas, leaving only the user's free-form drawings.
        """
        return [f"shape:{cid}" for cid in self._components] + \
               [f"shape:{aid}" for aid in self._arrows]

    # -- user-drawing snapshot (request/response with the browser) ------------
    def request_snapshot(self, timeout=5.0):
        """Ask a connected browser for the user's free-form drawings.

        Returns tldraw "content" (shapes/bindings/assets) for everything on the
        canvas *except* the pycanvas panels and connector arrows — those are
        recreated from Python code, not persisted. The browser is the source of
        truth for free-form drawings, so this round-trips over the socket and
        blocks the calling thread until a reply arrives (or ``timeout`` elapses).
        Requires at least one open client.
        """
        if not self._connections:
            raise RuntimeError("no connected browser to read the canvas from")
        req_id = uuid.uuid4().hex
        waiter = {"event": threading.Event(), "data": None}
        self._snapshot_waiters[req_id] = waiter
        try:
            self.broadcast({
                "type": "get_snapshot",
                "reqId": req_id,
                "panelIds": self._panel_shape_ids(),
            })
            if not waiter["event"].wait(timeout):
                raise TimeoutError("timed out waiting for the canvas snapshot")
            return waiter["data"]
        finally:
            self._snapshot_waiters.pop(req_id, None)

    def load_snapshot(self, data):
        """Push saved user drawings to connected browsers (merged onto the page).

        The content is *added* to the live canvas, so the code-created panels
        stay put and the drawings reappear on top of them. Remembered so a
        client that connects (or reloads) later is sent the same drawings,
        making them survive page reloads.
        """
        self._loaded_doc = data
        self.broadcast({"type": "load_snapshot", "data": data})
