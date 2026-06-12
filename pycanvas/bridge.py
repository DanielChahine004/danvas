"""Bidirectional state sync between Python components and the browser.

A single WebSocket connection carries all components, multiplexed by id.
``broadcast`` is thread-safe: user threads call ``component.update(...)`` which
schedules the actual send onto the server's asyncio event loop.
"""

import asyncio
import json
import math
import random
import threading
import time
import traceback
import uuid
from collections import deque

from fastapi import WebSocketDisconnect

from .kernel import Kernel

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
        self._connections = set()
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
        # Conflated ("latest" queue policy) send state. For components that opt
        # out of FIFO, we keep only the newest pending value per (socket,
        # component, channel) and a flag marking whether a sender is draining it,
        # so a fast producer can't pile a backlog onto a slow client. Guarded by a
        # plain lock since producers are user threads and the sender is the loop.
        self._conflate_pending = {}   # (ws, comp_id, kind) -> (kind, msg|bytes)
        self._conflate_active = set()  # (ws, comp_id, kind) with a live sender
        self._conflate_lock = threading.Lock()
        # User input/layout callbacks (``on_change``/``on_layout`` and the
        # component routers) run here, on a single FIFO worker thread, instead of
        # on the asyncio event loop. A slow or blocking callback (a sleep, an HTTP
        # call, heavy compute -- exactly what "drag slider -> move robot" handlers
        # do) would otherwise freeze the loop and stall rendering and every other
        # viewer. One ordered thread preserves per-message order (so a slider drag
        # settles on its last value) while keeping the loop free. Lazy: no thread
        # until the first inbound message.
        self._dispatch = Kernel()

    # -- wiring --------------------------------------------------------------
    def add_component(self, component):
        self._components[component.id] = component

    def remove_component(self, component_id):
        """Forget a component and tell connected clients to drop its panel."""
        self._components.pop(component_id, None)
        self.broadcast({"type": "remove", "id": component_id})

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

    def register_message(self, component):
        """Build the ``register`` message for a component, including placement."""
        msg = {
            "type": "register",
            "id": component.id,
            "component": component.component,
            "props": component.register_props(),
        }
        pos = getattr(component, "_position", None)
        if pos is not None:
            msg["x"], msg["y"] = pos
        rot = getattr(component, "_rotation", None)
        if rot is not None:
            msg["rotation"] = math.radians(rot)
        if getattr(component, "_locked", False):
            msg["locked"] = True
        # The public API names are draggable/operable/grabable (see base.py); the
        # wire keys stay movable/interactive/selectable, matching set_layout's
        # payload and the frontend's lockMeta. Read the new attribute names —
        # reading the old ones silently defaulted every lock to "on", so initial
        # draggable/operable/grabable=False never reached the browser.
        if not getattr(component, "_draggable", True):
            msg["movable"] = False
        if not getattr(component, "_resizable", True):
            msg["resizable"] = False
        if not getattr(component, "_operable", True):
            msg["interactive"] = False
        if not getattr(component, "_grabable", True):
            msg["selectable"] = False
        if not getattr(component, "_frame", True):
            msg["frame"] = False
        return msg

    def register_live(self, component):
        """Broadcast a newly-added component to already-connected clients.

        Used for components inserted after the server is already running (e.g.
        from a Jupyter cell). Fresh connections still get the full replay via
        ``handle_connection``; this covers clients already on the page.
        """
        self.broadcast(self.register_message(component))
        state = component.state_payload()
        if state:
            self.broadcast(
                {"type": "update", "id": component.id, "payload": state}
            )

    # -- connection lifecycle (runs in the event loop) -----------------------
    async def handle_connection(self, ws):
        await ws.accept()
        self._connections.add(ws)
        self._send_locks[ws] = asyncio.Lock()
        self._last_seen[ws] = time.monotonic()
        viewer = self._make_viewer()
        self._viewers[ws] = viewer
        self._broadcast_roster()  # tell everyone a viewer joined
        try:
            # Tell this client who it is, so it can label its own chat messages
            # and prefill the editable name field.
            view_for_client = self._view_per_client.get(viewer["id"])
            if view_for_client is not None:
                view_for_client = {**(self._view or {}), **view_for_client}
            else:
                view_for_client = self._view
            await self._send(ws, {"type": "welcome", "you": viewer,
                                  "uiInspector": self._ui_inspector,
                                  "view": view_for_client,
                                  "reload": self._reload})
            # Replay recent chat so a fresh viewer sees the conversation so far.
            for entry in self._chat_history:
                await self._send(ws, entry)
            # Replay full state to the freshly connected client.
            for comp in self._components.values():
                await self._send(ws, self.register_message(comp))
                state = comp.state_payload()
                if state:
                    await self._send(
                        ws, {"type": "update", "id": comp.id, "payload": state}
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

            while True:
                raw = await ws.receive_text()
                self._on_message(ws, raw)
        except WebSocketDisconnect:
            pass
        except Exception:
            traceback.print_exc()
        finally:
            self._connections.discard(ws)
            self._send_locks.pop(ws, None)
            self._drop_conflate(ws)
            gone = self._viewers.pop(ws, None)
            # Viewer ids are minted fresh per connection and never reused, so a
            # per-client view override for a departed viewer can never apply
            # again — drop it so the map doesn't grow unbounded.
            if gone is not None:
                self._view_per_client.pop(gone["id"], None)
            self._last_seen.pop(ws, None)
            self._broadcast_roster()  # tell everyone a viewer left

    def _make_viewer(self):
        """Mint a fresh viewer identity (id + friendly editable name + color)."""
        existing = {v["name"] for v in self._viewers.values()}
        animal = random.choice(_VIEWER_ANIMALS)
        name = animal
        n = 2
        while name in existing:  # keep auto-names distinct; user can rename
            name = f"{animal} {n}"
            n += 1
        color = random.choice(_VIEWER_COLORS)
        return {"id": uuid.uuid4().hex[:8], "name": name, "color": color}

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

    # Backwards-compatible alias (older call sites / the merge host).
    _broadcast_presence = _broadcast_roster

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
                    lambda c=comp, m=msg: self._dispatch_layout(c, m)
                )
        elif kind == "draw":
            # A browser relayed a free-form drawing change. Fold it into the
            # canonical record set and echo it to the other browsers so every
            # open view converges (re-applying its own diff is idempotent).
            diff = msg.get("diff") or {}
            self._apply_draw(diff)
            self.broadcast({"type": "draw", "diff": diff})
        elif kind == "snapshot":
            # Reply to a request_snapshot; hand the document to the waiter.
            waiter = self._snapshot_waiters.get(msg.get("reqId"))
            if waiter is not None:
                waiter["data"] = msg.get("data")
                waiter["event"].set()

    def _dispatch_input(self, comp, payload, ws):
        """Run a component's input handler (off the loop) and echo its state.

        Called on the dispatch thread. Echoes the resulting state to the *other*
        clients so a second browser (or a merge host aggregating this canvas)
        stays in sync with a browser-driven change. Output-only components return
        None and are left alone. The originating browser is excluded: it already
        shows the value, and echoing back mid-drag would fight the live thumb with
        stale values.
        """
        comp._handle_input(payload)
        state = comp.state_payload()
        if state:
            self.broadcast(
                {"type": "update", "id": comp.id, "payload": state}, exclude=ws
            )

    def _dispatch_layout(self, comp, msg):
        """Apply a user move/resize (off the loop) and echo the new geometry.

        Echoes to every client (a second browser, or a merge host) as an
        ``update`` -- the server->browser form the frontend applies. The fields
        already carry the wire units the frontend expects (canvas x/y, radian
        rotation).
        """
        comp._apply_remote_layout(msg)
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
    def broadcast(self, msg, exclude=None):
        """Send ``msg`` to every connected client. Safe to call from any thread.

        ``exclude`` skips one connection (the originator of a change), used to
        avoid echoing a browser's own input straight back to it.
        """
        if self._loop is None:
            return
        for ws in list(self._connections):
            if ws is exclude:
                continue
            asyncio.run_coroutine_threadsafe(self._safe_send(ws, msg), self._loop)

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
        if self._loop is None:
            return
        ws = next((s for s, v in list(self._viewers.items())
                   if v.get("id") == viewer_id), None)
        if ws is not None:
            asyncio.run_coroutine_threadsafe(self._safe_send(ws, msg), self._loop)

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

    def broadcast_conflated(self, comp_id, *, msg=None, data=None, exclude=None):
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
