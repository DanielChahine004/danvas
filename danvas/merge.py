"""Merge several running danvas canvases into one unified, read-and-relay view.

Each user keeps hosting their own :class:`~danvas.Canvas` on their own port,
exactly as before. This module adds an *aggregator* that connects to those
canvases as a client (just like a browser does), composites their panels onto a
single surface, and re-serves the union on a new port::

    # CLI -- unify three running canvases onto http://localhost:8080
    python -m danvas.merge :8001 :8002 host3:8003 --port 8080

    # sources may also be tunnel URLs, and the merged view can itself be tunneled
    python -m danvas.merge https://a.loca.lt https://b.loca.lt --tunnel

    # or from Python
    from danvas.merge import Merge
    Merge([8001, 8002]).serve(port=8080)

The merge host runs *no* component logic and holds *no* variables. It caches
each source's presentation messages, fans them out to browsers, and routes
interaction events back to the owning source -- so a click on Sarah's button
still computes in Sarah's process, Josef's in his, and so on. By default the
canvases are overlaid with their real coordinates preserved; pass
``region_width`` to spread the sources out side-by-side instead.

Limitations (v1): free-form user drawings are not composited (only code-driven
panels and arrows are merged), and rearranging panels in the merged view is
local to the merge host -- it is not pushed back to the source canvases. A
source's panels go inert (and are removed from the view) while that source is
disconnected, and reappear when it reconnects.
"""

import argparse
import asyncio
import json
import time
import traceback

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from . import server
from .bridge import Bridge


def _parse_source(spec):
    """Normalise a source spec to ``(ws_uri, label)``.

    Accepts:

    - a bare port — ``8001`` / ``"8001"`` / ``":8001"`` (localhost) — or a
      ``"host:port"``: connected over ``ws://`` on the same/another LAN, as before.
    - a full URL — ``"https://x.loca.lt"`` / ``"wss://host/ws"`` — so a canvas
      exposed through a tunnel (see :meth:`danvas.Canvas.serve` ``tunnel=True``)
      can be merged from anywhere. ``http``→``ws`` and ``https``→``wss``, and the
      ``/ws`` endpoint path is appended when missing.
    """
    if isinstance(spec, int):
        return f"ws://localhost:{spec}/ws", f"localhost:{spec}"
    text = str(spec).strip()
    if "://" in text:
        scheme, _, rest = text.partition("://")
        scheme = {"http": "ws", "https": "wss"}.get(scheme.lower(), scheme.lower())
        rest = rest.rstrip("/")
        label = rest.split("/", 1)[0]
        if not rest.endswith("/ws"):
            rest += "/ws"
        return f"{scheme}://{rest}", label
    if text.startswith(":"):
        text = "localhost" + text
    if ":" in text:
        host, _, port = text.rpartition(":")
        host = host or "localhost"
    else:
        host, port = "localhost", text
    return f"ws://{host}:{int(port)}/ws", f"{host}:{port}"


class _Source:
    """One upstream canvas: its endpoint, its layout offset, and its live socket.

    The socket (``_ws``) is set while connected so the merge host can route a
    browser's interaction back to this canvas; it is ``None`` when the source is
    down. Position-less panels are auto-cascaded within the source's region via
    ``_cascade``.
    """

    def __init__(self, uri, offset_x, offset_y, label=None):
        self.uri = uri
        self.offset = (offset_x, offset_y)
        self.label = label or uri
        self._ws = None
        self._cascade = 0

    async def send(self, msg):
        """Forward a message upstream to this canvas (no-op if disconnected)."""
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps(msg))
        except WebSocketException:
            pass


class MergeBridge(Bridge):
    """A :class:`Bridge` that serves a cached union of several source canvases.

    Unlike a normal bridge it owns no component objects. It caches the latest
    ``register``/``update``/``arrow`` message per id (the replay material for new
    browsers), maps each id to the source that owns it, and routes inbound
    ``input``/``layout`` messages back to that source.
    """

    def __init__(self, sources, region_width=0):
        super().__init__()
        self._sources = sources
        self._region_width = region_width
        # Presentation caches (raw wire messages), replayed to fresh browsers.
        self._registers = {}   # id -> register msg (offset already applied)
        self._updates = {}     # id -> last update msg
        self._arrows_raw = {}  # id -> arrow msg
        self._id_source = {}   # id -> _Source that owns it

    # -- startup: spawn the source clients on the server's event loop ---------
    def set_loop(self, loop):
        """Capture the running loop (lifespan) and launch each source client."""
        super().set_loop(loop)
        for src in self._sources:
            loop.create_task(self._run_source(src))

    async def _run_source(self, src):
        """Stay connected to one source: replay on connect, relay, reconnect."""
        while True:
            try:
                async with connect(src.uri, max_size=None) as ws:
                    src._ws = ws
                    print(f"[merge] connected to {src.label}")
                    async for raw in ws:
                        await self._ingest(src, raw)
            except (OSError, WebSocketException, asyncio.TimeoutError):
                pass
            except Exception:
                traceback.print_exc()
            finally:
                src._ws = None
                await self._drop_source(src)
            await asyncio.sleep(1.0)  # backoff before reconnecting

    # -- inbound from a source (downstream messages) -------------------------
    async def _ingest(self, src, raw):
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        kind = msg.get("type")
        if kind == "register":
            self._offset_register(src, msg)
            cid = msg.get("id")
            self._registers[cid] = msg
            self._id_source[cid] = src
            await self._fanout(msg)
        elif kind == "update":
            cid = msg.get("id")
            payload = dict(msg.get("payload") or {})
            # A panel moved/resized on the source: translate its position into
            # the merged space if this source is offset into its own region.
            ox, oy = src.offset
            if (ox or oy) and ("x" in payload or "y" in payload):
                if payload.get("x") is not None:
                    payload["x"] += ox
                if payload.get("y") is not None:
                    payload["y"] += oy
                msg = {"type": "update", "id": cid, "payload": payload}
            # Merge into the cached state (value and geometry updates interleave,
            # so last-wins would drop one); this is what fresh browsers replay.
            self._updates.setdefault(cid, {}).update(payload)
            await self._fanout(msg)
        elif kind == "arrow":
            aid = msg.get("id")
            self._arrows_raw[aid] = msg
            self._id_source[aid] = src
            await self._fanout(msg)
        elif kind == "remove":
            cid = msg.get("id")
            self._registers.pop(cid, None)
            self._updates.pop(cid, None)
            self._arrows_raw.pop(cid, None)
            self._id_source.pop(cid, None)
            await self._fanout(msg)
        # 'load_snapshot' (free-form drawings) is intentionally not composited.

    def _offset_register(self, src, msg):
        """Shift a panel into its source's region (only when regions are used).

        Default behaviour (zero offset) is a faithful overlay: positioned panels
        keep their real coordinates, and position-less panels are passed through
        untouched so the merged view auto-cascades them like any other canvas.

        When ``region_width`` separates the sources, positioned panels are
        translated by the source offset and position-less ones are cascaded
        within the region instead of all landing on the same spot.
        """
        ox, oy = src.offset
        if ox == 0 and oy == 0:
            return  # overlay: preserve source coordinates as-is
        if "x" in msg and "y" in msg:
            msg["x"] += ox
            msg["y"] += oy
        else:
            step = src._cascade * 40
            msg["x"] = ox + step
            msg["y"] = oy + step
            src._cascade += 1

    async def _drop_source(self, src):
        """Remove a disconnected source's panels from the view and caches."""
        dead = [cid for cid, owner in self._id_source.items() if owner is src]
        for cid in dead:
            self._registers.pop(cid, None)
            self._updates.pop(cid, None)
            self._arrows_raw.pop(cid, None)
            self._id_source.pop(cid, None)
            await self._fanout({"type": "remove", "id": cid})
        if dead:
            print(f"[merge] {src.label} disconnected; dropped {len(dead)} shapes")

    # -- browser-facing server (overrides Bridge's object-based replay) ------
    async def handle_connection(self, ws):
        await ws.accept()
        self._connections.add(ws)
        self._last_seen[ws] = time.monotonic()
        viewer = self._make_viewer()
        self._viewers[ws] = viewer
        self._broadcast_roster()  # viewer joined the merged view
        # The base ``_send`` serializes per connection via a lock keyed on the
        # socket; it creates one lazily, but we still drop it on disconnect.
        try:
            await self._send(ws, {"type": "welcome", "you": viewer})
            for entry in self._chat_history:
                await self._send(ws, entry)
            for cid, reg in self._registers.items():
                await self._send(ws, reg)
                payload = self._updates.get(cid)
                if payload:
                    await self._send(
                        ws, {"type": "update", "id": cid, "payload": payload}
                    )
            for msg in self._arrows_raw.values():
                await self._send(ws, msg)
            while True:
                raw = await ws.receive_text()
                await self._route_from_browser(ws, raw)
        except Exception:
            pass
        finally:
            self._connections.discard(ws)
            self._send_locks.pop(ws, None)
            self._viewers.pop(ws, None)
            self._last_seen.pop(ws, None)
            self._broadcast_roster()  # viewer left the merged view

    async def _route_from_browser(self, ws, raw):
        """Send a browser interaction back to the source that owns the panel."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        self._last_seen[ws] = time.monotonic()
        kind = msg.get("type")
        # Presence/chat are mediated by the merge host itself, not routed to a
        # source — viewers on the merged view see each other and can chat.
        if kind == "heartbeat":
            return
        if kind == "set_name":
            self._rename_viewer(ws, msg.get("name"))
            return
        if kind == "chat":
            self._handle_chat(ws, msg.get("text"))
            return
        cid = msg.get("id")
        src = self._id_source.get(cid)
        if src is None:
            return  # canvas-level message (e.g. snapshot) -- nothing to route
        if kind == "input":
            await src.send(msg)
        elif kind == "layout":
            # Translate merged-canvas coords back into the source's own space so
            # the source's stored geometry stays correct.
            ox, oy = src.offset
            out = dict(msg)
            if out.get("x") is not None:
                out["x"] -= ox
            if out.get("y") is not None:
                out["y"] -= oy
            await src.send(out)

    async def _fanout(self, msg):
        """Send one message to every connected browser (runs on the loop)."""
        for ws in list(self._connections):
            await self._safe_send(ws, msg)


class Merge:
    """Public entry point: unify several running canvases onto one new port.

    ``sources`` is a list of ports (``8001``) or addresses (``"host:8001"``).
    By default the canvases are **overlaid**, each panel keeping its real
    coordinates. Pass ``region_width`` to instead spread the sources out
    side-by-side, each in its own horizontal region that many pixels wide.
    """

    def __init__(self, sources, region_width=0):
        parsed = []
        for i, spec in enumerate(sources):
            uri, label = _parse_source(spec)
            parsed.append(_Source(uri, offset_x=i * region_width, offset_y=0,
                                  label=label))
        self._bridge = MergeBridge(parsed, region_width=region_width)
        self._server = None
        self._tunnel = None

    def serve(self, port=8080, open_browser=True, host="127.0.0.1", block=True,
              tunnel=False, tunnel_provider="cloudflared"):
        """Start the merge host.

        With ``block=True`` (default) this blocks until shutdown. With
        ``block=False`` it starts the host in the background and returns ``self``
        for chaining (use in a notebook, then call :meth:`stop`).

        Pass ``tunnel=True`` to expose the merged view on the public internet
        through a tunnel (``tunnel_provider`` selects the backend, default
        ``"cloudflared"``), so collaborators on any network can open the printed
        ``https://…`` URL. The tunnel is closed when the host stops.
        """
        if not block:
            self._server = server.run_background(
                self._bridge, port=port, open_browser=open_browser, host=host,
                compress=tunnel,
            )
            if tunnel:
                self._start_tunnel(port, tunnel_provider)
            return self
        if tunnel:
            self._start_tunnel(port, tunnel_provider)
        try:
            server.run(self._bridge, port=port, open_browser=open_browser,
                       host=host, compress=tunnel)
        finally:
            self._stop_tunnel()

    def _start_tunnel(self, port, provider):
        from .tunnel import open_tunnel
        self._tunnel = open_tunnel(port, provider=provider)
        print(f"[merge] public URL: {self._tunnel.url}"
              "   <- share this with anyone, anywhere")

    def _stop_tunnel(self):
        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel = None

    def stop(self):
        """Signal the background merge host to shut down and close any tunnel."""
        if self._server is not None:
            self._server.should_exit = True
        self._stop_tunnel()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m danvas.merge",
        description="Merge several running danvas canvases into one view.",
    )
    parser.add_argument(
        "sources", nargs="+",
        help="source canvases as PORT, :PORT, HOST:PORT, or a full tunnel URL "
             "(e.g. :8001 host:8002 https://x.loca.lt)",
    )
    parser.add_argument("--port", type=int, default=8080, help="port to serve on")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--no-open", action="store_true", help="don't open a browser")
    parser.add_argument("--region-width", type=int, default=0,
                        help="spread sources side-by-side, this many px each "
                             "(0 = overlay, preserving real coordinates)")
    parser.add_argument("--tunnel", action="store_true",
                        help="expose the merged view on the public internet")
    parser.add_argument("--tunnel-provider", default="cloudflared",
                        choices=["cloudflared", "localtunnel"],
                        help="tunnel backend for --tunnel (default cloudflared)")
    args = parser.parse_args(argv)
    Merge(
        args.sources,
        region_width=args.region_width,
    ).serve(port=args.port, open_browser=not args.no_open, host=args.host,
            tunnel=args.tunnel, tunnel_provider=args.tunnel_provider)


if __name__ == "__main__":
    main()