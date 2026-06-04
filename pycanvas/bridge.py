"""Bidirectional state sync between Python components and the browser.

A single WebSocket connection carries all components, multiplexed by id.
``broadcast`` is thread-safe: user threads call ``component.update(...)`` which
schedules the actual send onto the server's asyncio event loop.
"""

import asyncio
import json
import math
import traceback

from fastapi import WebSocketDisconnect


class Bridge:
    def __init__(self):
        self._components = {}  # id -> BaseComponent
        self._connections = set()
        self._loop = None

    # -- wiring --------------------------------------------------------------
    def add_component(self, component):
        self._components[component.id] = component

    def remove_component(self, component_id):
        """Forget a component and tell connected clients to drop its panel."""
        self._components.pop(component_id, None)
        self.broadcast({"type": "remove", "id": component_id})

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
        try:
            # Replay full state to the freshly connected client.
            for comp in self._components.values():
                await self._send(ws, self.register_message(comp))
                state = comp.state_payload()
                if state:
                    await self._send(
                        ws, {"type": "update", "id": comp.id, "payload": state}
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

    def _on_message(self, raw):
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if msg.get("type") == "input":
            comp = self._components.get(msg.get("id"))
            if comp is not None:
                comp._handle_input(msg.get("payload") or {})

    async def _send(self, ws, msg):
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
            await ws.send_text(json.dumps(msg))
        except Exception:
            self._connections.discard(ws)
