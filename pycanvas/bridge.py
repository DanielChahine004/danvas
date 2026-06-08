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
        if not getattr(component, "_movable", True):
            msg["movable"] = False
        if not getattr(component, "_resizable", True):
            msg["resizable"] = False
        if not getattr(component, "_interactive", True):
            msg["interactive"] = False
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
            await self._send(ws, {"type": "welcome", "you": viewer,
                                  "uiInspector": self._ui_inspector})
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
            self._viewers.pop(ws, None)
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
                    if self._viewers.pop(ws, None) is not None:
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
                comp._handle_input(msg.get("payload") or {})
                # Echo the resulting state to every client so other open views
                # stay in sync with a browser-driven change (a second browser on
                # this canvas, or a merge host aggregating it). Output-only
                # components return None here and are left alone. The originating
                # browser already shows the value, so the echo is idempotent.
                state = comp.state_payload()
                if state:
                    self.broadcast(
                        {"type": "update", "id": comp.id, "payload": state}
                    )
        elif kind == "layout":
            # User moved/resized a panel in the browser; sync Python's state.
            comp = self._components.get(msg.get("id"))
            if comp is not None:
                comp._apply_remote_layout(msg)
                # Echo the new geometry to every client (a second browser, or a
                # merge host) as an ``update`` -- the server->browser form the
                # frontend applies. The fields already carry the wire units the
                # frontend expects (canvas x/y, radian rotation).
                geom = {k: msg[k] for k in ("x", "y", "w", "h", "rotation")
                        if msg.get(k) is not None}
                if geom:
                    self.broadcast(
                        {"type": "update", "id": comp.id, "payload": geom}
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

    # -- outbound (thread-safe) ----------------------------------------------
    def broadcast(self, msg):
        """Send ``msg`` to every connected client. Safe to call from any thread."""
        if self._loop is None:
            return
        for ws in list(self._connections):
            asyncio.run_coroutine_threadsafe(self._safe_send(ws, msg), self._loop)

    async def _safe_send(self, ws, msg):
        try:
            await self._send(ws, msg)
        except Exception:
            self._connections.discard(ws)
            self._send_locks.pop(ws, None)

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
