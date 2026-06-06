"""Bidirectional state sync between Python components and the browser.

A single WebSocket connection carries all components, multiplexed by id.
``broadcast`` is thread-safe: user threads call ``component.update(...)`` which
schedules the actual send onto the server's asyncio event loop.
"""

import asyncio
import json
import math
import threading
import traceback
import uuid

from fastapi import WebSocketDisconnect


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
        try:
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
            # If a full canvas was loaded, replay it last so reloads keep it
            # (it replaces the document, incl. any user drawings it contained).
            if self._loaded_doc is not None:
                await self._send(
                    ws, {"type": "load_snapshot", "data": self._loaded_doc}
                )

            while True:
                raw = await ws.receive_text()
                self._on_message(raw)
        except WebSocketDisconnect:
            pass
        except Exception:
            traceback.print_exc()
        finally:
            self._connections.discard(ws)
            self._send_locks.pop(ws, None)

    def _on_message(self, raw):
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        kind = msg.get("type")
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
        elif kind == "snapshot":
            # Reply to a request_snapshot; hand the document to the waiter.
            waiter = self._snapshot_waiters.get(msg.get("reqId"))
            if waiter is not None:
                waiter["data"] = msg.get("data")
                waiter["event"].set()

    async def _send(self, ws, msg):
        """Send one frame, serialized against any other send to this socket."""
        lock = self._send_locks.get(ws)
        if lock is None:  # connection already torn down
            return
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
