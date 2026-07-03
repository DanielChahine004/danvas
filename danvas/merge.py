"""Merge several running danvas canvases into one unified, read-and-relay view.

Each user keeps hosting their own :class:`~danvas.Canvas` on their own port,
exactly as before. This module adds a *standing merge server* that composes a
**per-connection** set of source canvases: each browser that connects picks which
sources it wants to see (via the ``?sources=`` query or the in-UI merge panel),
and the server connects to those canvases *as a client* (like a browser does),
composites their panels onto one surface, and routes interactions back to the
owning source -- so a click on Sarah's button still computes in Sarah's process::

    # start a standing merge server; browsers choose their own source sets
    python -m danvas.merge --port 8080
    #   then open  http://127.0.0.1:8080/?sources=127.0.0.1:8001,127.0.0.1:8002

    # or seed a default set (used by connections that don't pass ?sources=)
    python -m danvas.merge :8001 :8002 --port 8080

    # protected sources: supply the password so the CLI-seeded set can authenticate
    python -m danvas.merge :8001 --auth 127.0.0.1:8001=secret

    # or from Python
    from danvas.merge import Merge
    Merge([8001, 8002]).serve(port=8080)

The merge server runs *no* component logic and holds *no* variables. Per unique
``(source, credential)`` it keeps one upstream client connection (reference-counted
across the browsers that want it), caches that connection's register/update/arrow
messages, fans them out to the interested browsers, and routes interaction events
back to the owning source.

**Password-protected sources.** A source that requires a password can be merged:
the server does that canvas's ``POST /__auth__`` password flow, captures the
session cookie, and connects with it. The source then streams only the panels and
updates that password's *role* is allowed to see (its own egress filtering does the
work), so a merged viewer sees exactly that role's canvas -- the merge server never
interprets the source's roles.

**Free-form drawings composite too.** A source's user-drawn ink is relayed into the
merged view (namespaced per source, so multiple sources' ink coexists, and hidden/
shown with the source's eye toggle); editing or erasing a source stroke from the
merged view routes back to the owning canvas; and fresh strokes drawn on the merged
view are the merge server's own shared annotation layer (visible to every merge
viewer, not pushed to any source).

Binary media relays too: a source's video/audio/push_binary envelopes reach the
merged view with the panel id rewritten in-envelope, and a viewer's binary INPUT
on a merged panel routes back to the owner (streams aren't cached — they're
transient by the protocol's own rule, so they don't replay).

Limitations: rearranging panels in the merged view is local to the merge server.
Cross-source arrows work from a *dial-in* peer (it holds the composed replica,
so it can bind an arrow to any panel it sees — its own, the hub's, or another
source's); a *dialed-out* source canvas still can't draw them, because it never
sees the composed ids. A
source going offline keeps its panels (and its ink) on the merged view by default,
frozen at their last-known state — dimmed and non-operable, so a held record can't
be mistaken for live data — until it reconnects, when the fresh replay replaces
them. Pass ``retain=False`` (``--no-retain`` / ``serve(merge_retain=False)``) for
the historical drop-on-offline behaviour.
"""

import argparse
import asyncio
import json
import os
import re
import time
import traceback
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import quote, urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from . import server
from ._protocol import BINARY_FRAME_CODES
from .bridge import Bridge

# Media codes relay hub-ward (source -> browsers); INPUT routes owner-ward.
_BINARY_MEDIA_CODES = {c for name, c in BINARY_FRAME_CODES.items()
                       if name != "INPUT"}
_BINARY_INPUT_CODE = BINARY_FRAME_CODES["INPUT"]


def _bin_reframe(data, new_id):
    """Rewrite the id inside a binary envelope ([type][idLen][id][payload])."""
    idlen = data[1]
    nid = new_id.encode()
    return bytes([data[0], len(nid)]) + nid + bytes(data[2 + idlen:])


def _bin_id(data):
    """The panel id carried in a binary envelope, or None if malformed."""
    if len(data) < 2 or len(data) < 2 + data[1]:
        return None
    return bytes(data[2:2 + data[1]]).decode("utf-8", "replace")


def _parse_source(spec):
    """Normalise a source spec to ``(ws_uri, http_parts, label)``.

    ``http_parts`` is ``(scheme, host, port, is_tls)`` for the source's HTTP
    origin -- used to probe whether the canvas is password-protected and to run
    its ``/__auth__`` login flow. Accepts a bare port (``8001`` / ``":8001"``),
    a ``host:port``, or a full URL (``https://x.loca.lt`` / ``wss://host/ws``).
    """
    if isinstance(spec, int):
        ws_uri, label = f"ws://localhost:{spec}/ws", f"localhost:{spec}"
    else:
        text = str(spec).strip()
        if "://" in text:
            scheme, _, rest = text.partition("://")
            scheme = {"http": "ws", "https": "wss"}.get(scheme.lower(), scheme.lower())
            rest = rest.rstrip("/")
            label = rest.split("/", 1)[0]
            if not rest.endswith("/ws"):
                rest += "/ws"
            ws_uri = f"{scheme}://{rest}"
        else:
            if text.startswith(":"):
                text = "localhost" + text
            if ":" in text:
                host, _, port = text.rpartition(":")
                host = host or "localhost"
            else:
                host, port = "localhost", text
            ws_uri, label = f"ws://{host}:{int(port)}/ws", f"{host}:{port}"
    u = urlsplit(ws_uri)
    tls = u.scheme == "wss"
    host = u.hostname or "localhost"
    port = u.port or (443 if tls else 80)
    http_parts = ("https" if tls else "http", host, port, tls)
    return ws_uri, http_parts, label


def _http_conn(http_parts, timeout=6):
    _scheme, host, port, tls = http_parts
    cls = HTTPSConnection if tls else HTTPConnection
    return cls(host, port, timeout=timeout)


def _probe_source(http_parts):
    """Classify a source (blocking; run in an executor): ``"open"`` (reachable, no
    auth), ``"auth"`` (password-protected -> HTTP 401), or ``"offline"``."""
    try:
        conn = _http_conn(http_parts)
        conn.request("GET", "/")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return "auth" if resp.status == 401 else "open"
    except Exception:
        return "offline"


def _authenticate(http_parts, password):
    """Run a source's ``/__auth__`` password flow (blocking; run in an executor).

    Returns the ``pc_session`` cookie token on success, or ``None`` on a wrong
    password / unreachable host. The canvas replies to a correct password with a
    303 redirect carrying ``Set-Cookie: pc_session=...``.
    """
    try:
        conn = _http_conn(http_parts)
        body = "password=" + quote(password or "", safe="")
        conn.request("POST", "/__auth__", body=body,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        set_cookie = resp.getheader("Set-Cookie") or ""
        resp.read()
        conn.close()
        m = re.search(r"pc_session=([^;]+)", set_cookie)
        return m.group(1) if m else None
    except Exception:
        return None


class _InboundWS:
    """Adapt a server-side (FastAPI) WebSocket to the ``.send(text)`` shape the
    upstream routing path expects from a *client* connection — so a dial-in
    source rides the exact same `_Upstream` machinery as a dialed-out one."""

    def __init__(self, ws):
        self._ws = ws

    async def send(self, text):
        await self._ws.send_text(text)

    async def send_binary(self, data):
        await self._ws.send_bytes(data)


class _Upstream:
    """One client connection to a source canvas, shared by every browser that
    requested this exact ``(ws_uri, cookie)`` pair.

    Pooled and reference-counted: two browsers viewing the same *open* canvas
    share one upstream, but two viewing it under *different passwords* (= different
    roles) get two, because the source filters per-connection by role. Caches the
    source's frames (already id-namespaced) so a newly-interested browser can be
    replayed without re-fetching.

    A **dial-in** upstream (``dialin=True``) is the same record with the
    connection direction flipped: the source dialed us (``?source=1`` on the
    hub's ``/ws``), so there is no ``_run_upstream`` retry loop — the lifecycle
    is the inbound socket's, and ``ws_uri`` is the pseudo-uri ``dialin:<label>``.
    """

    def __init__(self, ws_uri, http_parts, label, cookie, offset, tag):
        self.dialin = False
        self.ws_uri = ws_uri
        self.http_parts = http_parts
        self.label = label
        self.cookie = cookie                 # pc_session token, or None (open)
        self.offset = offset                 # (ox, oy) for region layout
        self.tag = tag                       # id namespace prefix, e.g. "s0"
        self.key = (ws_uri, cookie or "")    # pool key
        self.ws = None                       # live upstream socket, or None
        self.status = "connecting"           # connecting | live | offline
        self.refs = 0                        # browser connections interested
        self._task = None
        # Replay caches (namespaced ids, offset already applied).
        self.registers = {}   # nsid -> register msg
        self.updates = {}     # nsid -> accumulated payload dict
        self.arrows = {}      # nsid -> arrow msg
        self.shapes = {}      # nsid -> managed-shape frame (kept current)
        self.drawings = {}    # nsid -> free-form drawing record (the "after" state)

    async def send(self, msg):
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps(msg))
        except WebSocketException:
            pass

    async def send_binary(self, data):
        """Route a binary envelope to the owner (dialed-out client sockets
        accept bytes directly; a dial-in's _InboundWS adapts to send_bytes)."""
        ws = self.ws
        if ws is None:
            return
        try:
            if hasattr(ws, "send_binary"):
                await ws.send_binary(data)
            else:
                await ws.send(data)
        except WebSocketException:
            pass

    def cached_frames(self):
        """Every frame needed to reconstruct this source on a fresh browser."""
        yield from self.registers.values()
        for nsid, payload in self.updates.items():
            yield {"type": "update", "id": nsid, "payload": payload}
        yield from self.arrows.values()
        yield from self.shapes.values()
        if self.drawings:
            # The source's free-form ink, replayed as one "added" draw diff.
            yield {"type": "draw",
                   "diff": {"added": dict(self.drawings), "updated": {}, "removed": {}}}
        # A retained-offline source (caches kept past the drop; only a
        # retain=True host leaves them populated while offline) replays frozen:
        # last-known state, controls non-operable — so a browser joining while
        # the source is down sees the same held record the live viewers do.
        if self.status == "offline":
            yield from self.freeze_frames()

    def freeze_frames(self):
        """The freeze overlay a retaining host applies when this source goes
        down: per cached panel, non-operable AND visibly dimmed — stale data
        that *looks* live is worse than a vanished panel, so the hold must be
        unmistakable. Opacity rides the existing panel property, so no frontend
        change is involved; the reconnect teardown + fresh replay restores it."""
        for nsid in self.registers:
            yield {"type": "update", "id": nsid,
                   "payload": {"operable": False, "opacity": 0.45}}

    def cached_ids(self):
        return list(self.registers) + list(self.arrows) + list(self.shapes)


class _Conn:
    """One browser connected to the merge server and the sources it has chosen."""

    def __init__(self, ws):
        self.ws = ws
        self.sources = set()   # upstream.key it currently sees
        # Per-source hide is purely client-side (a render filter in the browser),
        # so the server keeps every source's frames flowing and holds no hidden
        # state — hiding a source in the merged view never reaches the source.


class _MergeHost:
    """The merge machinery a :class:`Bridge` runs to compose *other* canvases in.

    Held by a Bridge as ``bridge._merge`` (``None`` = merging off). Maintains a
    pool of upstream client connections keyed by ``(source, credential)``, maps
    each namespaced id back to its owning upstream for interaction routing, and
    fans each source's frames only to the browsers that asked for it. Used both by
    the dedicated :class:`Merge` server (a Bridge with no components of its own)
    and by a normal :class:`~danvas.Canvas` served with ``merge=True`` — which
    composes merged sources *alongside* its own panels (the "hub" case). The
    owning Bridge drives it through three hooks: :meth:`on_connect`,
    :meth:`route`, :meth:`on_disconnect`.
    """

    def __init__(self, bridge, default_sources=None, default_auth=None, region_width=0,
                 retain=True):
        self.bridge = bridge
        # retain=True (the default): a source going offline KEEPS its panels/ink
        # on the merged view, frozen at their last-known state — dimmed and
        # non-operable, so held data can't be mistaken for live — instead of
        # dropping them. The crash-isolation mode: the source script can die and
        # the hub stays a faithful record of it up to the last frame. On
        # reconnect the stale frames are torn down and the source's fresh replay
        # repopulates (ids are minted per run, so reconciling in place is not
        # possible). retain=False restores the historical drop-on-offline.
        self.retain = retain
        # Canvas-wide ("shared") sources: EVERY connection gets these, on top of
        # whatever it adds itself via ?sources= / the UI panel. Set by the code API
        # (Canvas.merge) and the CLI's seeded set. Each is {spec, offset, password}.
        self._shared_sources = []
        for spec, offset in list(default_sources or []):
            _u, _h, label = _parse_source(spec)
            self._shared_sources.append(
                {"spec": spec, "offset": offset,
                 "password": (default_auth or {}).get(label)})
        self._upstreams = {}          # key -> _Upstream
        self._tag_to_upstream = {}    # tag -> _Upstream
        self._conns = {}              # ws -> _Conn
        self._dialins = {}            # inbound ws -> its dial-in _Upstream
        self._tag_seq = 0
        # DANVAS_LEDGER=<path.db>: append every routed user action to the
        # SQLite event ledger (the _ledger.py schema) — hub-level forensics,
        # same file format the canvas's persist= ledger writes.
        self._ledger = None
        ledger_path = os.environ.get("DANVAS_LEDGER")
        if ledger_path:
            from . import _ledger as _ledger_mod
            self._ledger = _ledger_mod.open_ledger(ledger_path)
        # reqId -> (ws, expiry): who asked, so the owner's response frame
        # routes back to exactly that viewer (and nobody else).
        self._pending_req = {}
        # nsid -> (conn, expiry): the viewer who last drove input on a panel, so
        # the source's echo of that change isn't fanned back to them (it would fight
        # their live drag). Short-lived; a stale entry just means one missed echo.
        self._input_movers = {}

    # -- canvas-wide sources (the Canvas.merge code API) ---------------------
    def add_source(self, url, password=None, offset=(0, 0)):
        """Merge ``url`` for EVERY viewer (now and future). Idempotent by url."""
        if any(s["spec"] == url for s in self._shared_sources):
            return
        src = {"spec": url, "offset": offset, "password": password}
        self._shared_sources.append(src)
        if self._loop is not None:  # serving — attach it to everyone live
            for conn in list(self._conns.values()):
                self._loop.create_task(self._add_shared_source_for_conn(conn, src))

    def remove_source(self, url):
        """Un-merge a canvas-wide ``url`` from every viewer."""
        self._shared_sources = [s for s in self._shared_sources if s["spec"] != url]
        try:
            ws_uri, _h, _l = _parse_source(url)
        except Exception:
            return
        if self._loop is None:
            return
        for conn in list(self._conns.values()):
            for key in [k for k in conn.sources if k[0] == ws_uri]:
                self._release(conn, key)
                self._emit_sources(conn)

    def shared_specs(self):
        return [s["spec"] for s in self._shared_sources]

    @staticmethod
    def _resolve_uri(url):
        """A source spec -> the ws uri it pools under. A dial-in's pseudo-uri
        (``dialin:<label>``) has no host:port to parse, so it resolves to
        itself — offsets and lookups work the same for both kinds."""
        try:
            return _parse_source(url)[0]
        except Exception:
            return str(url)

    def set_offset(self, url, offset):
        """Translate a merged source's whole block of panels to a new origin, for
        every viewer (hub-wide). The offset is applied on the way down and undone on
        the way back, so the SOURCE canvas is never moved — this is purely how this
        hub lays the merged content out. Shifts the replay cache (so reconnects /
        new viewers land at the new origin) and live-nudges every viewing browser.
        """
        ws_uri = self._resolve_uri(url)
        ox2, oy2 = float(offset[0]), float(offset[1])
        for s in self._shared_sources:
            try:
                if _parse_source(s["spec"])[0] == ws_uri:
                    s["offset"] = (ox2, oy2)
            except Exception:
                pass
        for up in [u for u in self._upstreams.values() if u.ws_uri == ws_uri]:
            ox, oy = up.offset
            dx, dy = ox2 - ox, oy2 - oy
            if dx == 0 and dy == 0:
                continue
            up.offset = (ox2, oy2)
            for reg in up.registers.values():
                if isinstance(reg.get("x"), (int, float)):
                    reg["x"] += dx
                if isinstance(reg.get("y"), (int, float)):
                    reg["y"] += dy
            for payload in up.updates.values():
                if isinstance(payload.get("x"), (int, float)):
                    payload["x"] += dx
                if isinstance(payload.get("y"), (int, float)):
                    payload["y"] += dy
            for rec in up.drawings.values():
                if isinstance(rec.get("x"), (int, float)):
                    rec["x"] += dx
                if isinstance(rec.get("y"), (int, float)):
                    rec["y"] += dy
            if self._loop is None:
                continue
            for conn in self._interested(up):
                for nsid, reg in up.registers.items():
                    if isinstance(reg.get("x"), (int, float)) and isinstance(reg.get("y"), (int, float)):
                        self._loop.create_task(self._safe_send(
                            conn.ws, {"type": "update", "id": nsid,
                                      "payload": {"x": reg["x"], "y": reg["y"]}}))
                if up.drawings:   # re-place the source's ink at the new origin
                    self._loop.create_task(self._safe_send(conn.ws, {
                        "type": "draw", "diff": {"added": dict(up.drawings),
                                                 "updated": {}, "removed": {}}}))
                self._emit_sources(conn)   # refresh the roster's offset

    def offset_of(self, url):
        """The current (x, y) origin a source is merged at, or (0, 0)."""
        ws_uri = self._resolve_uri(url)
        for s in self._shared_sources:
            try:
                if _parse_source(s["spec"])[0] == ws_uri:
                    return tuple(s["offset"])
            except Exception:
                pass
        for up in self._upstreams.values():   # dial-ins have no shared entry
            if up.ws_uri == ws_uri:
                return tuple(up.offset)
        return (0.0, 0.0)

    # The owning Bridge supplies the event loop and the send primitives; exposing
    # them as ``self._loop`` / ``self._safe_send`` keeps the ported method bodies
    # unchanged from when this was a Bridge subclass.
    @property
    def _loop(self):
        return self.bridge._loop

    @property
    def _safe_send(self):
        return self.bridge._safe_send

    # -- id namespacing ------------------------------------------------------
    @staticmethod
    def _ns(tag, cid):
        return f"{tag}:{cid}"

    @staticmethod
    def _strip(nsid):
        """``"s3:abc"`` -> ``("s3", "abc")``. Splits on the first ``:`` only, so a
        source id that itself contains a colon round-trips intact."""
        tag, _, rest = str(nsid).partition(":")
        return tag, rest

    # -- free-form drawing id remapping --------------------------------------
    @staticmethod
    def _remap_record(rec, fn):
        """Apply ``fn`` to a drawing record's own id and any panel bindings, so a
        source's ink can be namespaced down and a merge viewer's edit stripped up
        (a bound user-drawn arrow keeps pointing at the same panel either way)."""
        if not isinstance(rec, dict):
            return rec
        out = dict(rec)
        if isinstance(out.get("id"), str):
            out["id"] = fn(out["id"])
        props = out.get("props")
        if isinstance(props, dict):
            p = dict(props)
            for k in ("bindStart", "bindEnd"):
                if isinstance(p.get(k), str):
                    p[k] = fn(p[k])
            out["props"] = p
        return out

    @classmethod
    def _remap_draw_diff(cls, diff, fn):
        """Rewrite every record id + diff key in a ``draw`` diff through ``fn``
        (``updated`` entries are ``[before, after]`` pairs)."""
        out = {}
        for bucket in ("added", "updated", "removed"):
            b = diff.get(bucket)
            if not isinstance(b, dict):
                continue
            nb = {}
            for rid, val in b.items():
                nk = fn(rid) if isinstance(rid, str) else rid
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    nb[nk] = [cls._remap_record(val[0], fn), cls._remap_record(val[1], fn)]
                else:
                    nb[nk] = cls._remap_record(val, fn)
            out[bucket] = nb
        return out

    @staticmethod
    def _fold_draw(store, diff):
        """Fold a draw diff into a ``{id: record}`` cache (the replay material)."""
        for rid, rec in (diff.get("added") or {}).items():
            store[rid] = rec
        for rid, pair in (diff.get("updated") or {}).items():
            store[rid] = pair[1] if isinstance(pair, (list, tuple)) and len(pair) == 2 else pair
        for rid in (diff.get("removed") or {}):
            store.pop(rid, None)

    @staticmethod
    def _offset_draw_diff(diff, dx, dy):
        """Shift every record's x/y in a draw diff by ``(dx, dy)`` — used to apply a
        source's origin offset to its ink coming down, and undo it going back up."""
        def shift(rec):
            if not isinstance(rec, dict):
                return rec
            r = dict(rec)
            if isinstance(r.get("x"), (int, float)):
                r["x"] = r["x"] + dx
            if isinstance(r.get("y"), (int, float)):
                r["y"] = r["y"] + dy
            return r
        out = {}
        for bucket in ("added", "updated", "removed"):
            b = diff.get(bucket)
            if not isinstance(b, dict):
                continue
            nb = {}
            for rid, val in b.items():
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    nb[rid] = [shift(val[0]), shift(val[1])]
                else:
                    nb[rid] = shift(val)
            out[bucket] = nb
        return out

    # -- inbound from a source (downstream) ----------------------------------
    async def _run_upstream(self, up):
        """Stay connected to one source: replay on connect, relay, reconnect.

        Runs until the last interested browser releases the upstream (the task is
        then cancelled). On every drop, the source's panels are removed from the
        interested browsers and its caches cleared, so a restart heals cleanly.
        """
        # ?proxy=1 tells the source not to exclude this connection from its own
        # input echoes (see Bridge._proxy_conns) — the merge fronts many browsers,
        # so it needs the source's authoritative state to cache + relay.
        proxy_uri = up.ws_uri + ("&" if "?" in up.ws_uri else "?") + "proxy=1"
        while True:
            try:
                headers = ({"Cookie": f"pc_session={up.cookie}"} if up.cookie else None)
                async with connect(proxy_uri, max_size=None,
                                   additional_headers=headers) as ws:
                    up.ws = ws
                    up.status = "live"
                    # Retained frames from the source's previous life are stale
                    # on reconnect (panel ids are minted per run): tear them
                    # down now and let the fresh replay repopulate. A no-op in
                    # non-retain mode, whose caches were cleared on the drop.
                    if up.registers or up.arrows or up.drawings:
                        for conn in self._interested(up):
                            self._send_source_teardown(conn.ws, up)
                        up.registers.clear()
                        up.updates.clear()
                        up.arrows.clear()
                        up.shapes.clear()
                        up.drawings.clear()
                    self._emit_sources_to_interested(up)
                    async for raw in ws:
                        self._ingest(up, raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                # auth rejection / network drop / bad handshake -- reconnect below
                pass
            finally:
                up.ws = None
                self._on_upstream_down(up)
            await asyncio.sleep(1.0)

    def _ingest(self, up, raw):
        """Fold one source frame into the upstream cache and fan it out.

        Only panels (register/update/remove) and connector arrows are composited,
        matching the historical merge scope; binary media and other frame types are
        ignored. Ids (including an arrow's start/end) are namespaced with the
        upstream tag so sources can't collide and route-back is unambiguous.
        """
        if isinstance(raw, (bytes, bytearray)):
            self.ingest_binary(up, raw)
            return
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return  # non-JSON text -- not composited
        kind = msg.get("type")
        ox, oy = up.offset
        if kind == "register":
            cid = self._ns(up.tag, msg.get("id"))
            msg["id"] = cid
            # Re-stamp ownership with THIS hub's name for the source: the
            # source may say "host" about itself, but on the composed canvas
            # the panel's owner is the source, by its merge label.
            msg["owner"] = up.label
            if (ox or oy) and "x" in msg and "y" in msg:
                msg["x"] += ox
                msg["y"] += oy
            elif ox or oy:
                # position-less panel: cascade within the source's region
                step = len([m for m in up.registers.values()]) * 40
                msg["x"] = ox + step
                msg["y"] = oy + step
            up.registers[cid] = msg
            self._fanout_upstream(up, msg)
        elif kind == "update":
            cid = self._ns(up.tag, msg.get("id"))
            payload = dict(msg.get("payload") or {})
            if (ox or oy):
                if payload.get("x") is not None:
                    payload["x"] += ox
                if payload.get("y") is not None:
                    payload["y"] += oy
            self._fold_state(up, cid, payload)
            # If this echo is the result of a viewer's own input, don't fan it back
            # to that viewer (they already have it locally; re-applying a streamed
            # value fights their drag). Others still get it, and it's cached above
            # so a hide/show replays the current state.
            self._fanout_upstream(up, {"type": "update", "id": cid, "payload": payload},
                                  exclude=self._recent_input_mover(cid))
        elif kind == "arrow":
            cid = self._ns(up.tag, msg.get("id"))
            msg["id"] = cid
            if isinstance(msg.get("start"), str):
                msg["start"] = self._compose_endpoint(up, msg["start"])
            if isinstance(msg.get("end"), str):
                msg["end"] = self._compose_endpoint(up, msg["end"])
            up.arrows[cid] = msg
            self._fanout_upstream(up, msg)
        elif kind == "shape":
            cid = self._ns(up.tag, msg.get("id"))
            msg["id"] = cid
            if (ox or oy):
                if isinstance(msg.get("x"), (int, float)):
                    msg["x"] += ox
                if isinstance(msg.get("y"), (int, float)):
                    msg["y"] += oy
            up.shapes[cid] = msg
            self._fanout_upstream(up, msg)
        elif kind == "shape_update":
            cid = self._ns(up.tag, msg.get("id"))
            msg["id"] = cid
            if (ox or oy):
                if isinstance(msg.get("x"), (int, float)):
                    msg["x"] += ox
                if isinstance(msg.get("y"), (int, float)):
                    msg["y"] += oy
            # Fold into the cached shape so a late browser gets the CURRENT
            # shape, not the original plus patches (same rule as panels).
            shape = up.shapes.get(cid)
            if shape is not None:
                props = msg.get("props")
                if isinstance(props, dict):
                    shape.setdefault("props", {}).update(props)
                for k, v in msg.items():
                    if k not in ("type", "id", "props"):
                        shape[k] = v
            self._fanout_upstream(up, msg)
        elif kind == "response":
            # The owner answered a viewer's request: route to the asker only.
            entry = self._pending_req.pop(msg.get("reqId"), None)
            if entry is not None and entry[1] > time.monotonic():
                self._loop.create_task(self._safe_send(entry[0], msg))
        elif kind == "remove":
            cid = self._ns(up.tag, msg.get("id"))
            up.registers.pop(cid, None)
            up.updates.pop(cid, None)
            up.arrows.pop(cid, None)
            up.shapes.pop(cid, None)
            self._fanout_upstream(up, {"type": "remove", "id": cid})
        elif kind == "draw":
            # Free-form ink drawn on the source (or its on-connect replay): namespace
            # every record id by the source tag so sources can't collide, cache it
            # for replay, and fan it out to the browsers viewing this source.
            ns_diff = self._remap_draw_diff(msg.get("diff") or {},
                                            lambda i: self._ns(up.tag, i))
            if ox or oy:  # translate the source's ink with its panels
                ns_diff = self._offset_draw_diff(ns_diff, ox, oy)
            self._fold_draw(up.drawings, ns_diff)
            self._fanout_upstream(up, {"type": "draw", "diff": ns_diff})

    @staticmethod
    def _fold_state(up, cid, payload):
        """Fold an update payload into the replay cache the way the OWNER's
        own reconnect replay would express it: geometry onto the cached
        register's top level, a value ``post`` into the register's baked
        ``props.data``, anything else onto the accumulated updates. Without
        this, a browser refresh replays the ORIGINAL register plus patches —
        and the transient channels (``post`` feeds mounted nodes, never the
        store) don't survive a fresh mount, so values/positions snapped back.
        """
        reg = up.registers.get(cid)
        rest = dict(payload)
        if reg is not None:
            for k in ("x", "y", "rotation", "opacity"):
                if isinstance(rest.get(k), (int, float)):
                    reg[k] = rest.pop(k)
            if "post" in rest:
                props = reg.get("props")
                data = props.get("data") if isinstance(props, dict) else None
                if isinstance(data, str):
                    try:
                        blob = json.loads(data)
                        if isinstance(blob, dict):
                            # The built-in controls' content keys (slider/
                            # toggle value, label text, image src) — the one
                            # bounded convention the hub knows about panels.
                            for key in ("value", "text", "src"):
                                if key in blob:
                                    blob[key] = rest.pop("post")
                                    props["data"] = json.dumps(blob)
                                    break
                    except (ValueError, TypeError):
                        pass
        if rest:
            up.updates.setdefault(cid, {}).update(rest)

    def ingest_binary(self, up, data):
        """A source's binary media frame (video/audio/push_binary): rewrite
        the id inside the envelope to the composed namespace and relay to the
        interested browsers. Not cached — streams aren't replayed (the same
        rule as everywhere else in danvas)."""
        cid = _bin_id(data)
        if cid is None or data[0] not in _BINARY_MEDIA_CODES:
            return
        out = _bin_reframe(data, self._ns(up.tag, cid))
        for conn in self._interested(up):
            self._loop.create_task(self.bridge._safe_send_binary(conn.ws, out))

    def route_binary(self, ws, data):
        """A viewer's binary INPUT frame addressed to a merged panel: strip
        the namespace and forward to the owner. Returns True when handled
        (bare ids fall through to the Bridge's own dispatch)."""
        if len(data) < 2 or data[0] != _BINARY_INPUT_CODE:
            return False
        cid = _bin_id(data)
        if cid is None:
            return False
        tag, orig = self._strip(cid)
        up = self._tag_to_upstream.get(tag)
        if up is None:
            return False
        self._loop.create_task(up.send_binary(_bin_reframe(data, orig)))
        return True

    def _send_source_teardown(self, ws, up):
        """Remove a source's panels (``remove`` frames) AND its free-form ink (a
        draw ``removed`` diff — user drawings live under their own id, not the
        ``shape:`` id a ``remove`` frame targets) from one browser."""
        for cid in up.cached_ids():
            self._loop.create_task(self._safe_send(ws, {"type": "remove", "id": cid}))
        if up.drawings:
            removed = {rid: {} for rid in up.drawings}
            self._loop.create_task(self._safe_send(
                ws, {"type": "draw", "diff": {"added": {}, "updated": {}, "removed": removed}}))

    def _on_upstream_down(self, up):
        """A source dropped. Default: remove its panels + ink from the interested
        browsers and clear its caches. With ``retain=True``: keep everything at
        its last-known state and freeze it (controls non-operable) — the UI
        outlives the source process. Either way it's marked offline in rosters."""
        up.status = "offline"
        if self.retain:
            for conn in self._interested(up):
                for msg in up.freeze_frames():
                    self._loop.create_task(self._safe_send(conn.ws, msg))
        else:
            for conn in self._interested(up):
                self._send_source_teardown(conn.ws, up)
            up.registers.clear()
            up.updates.clear()
            up.arrows.clear()
            up.shapes.clear()
            up.drawings.clear()
        self._emit_sources_to_interested(up)

    # -- fan-out -------------------------------------------------------------
    def _interested(self, up):
        """The browser connections subscribed to this upstream. (A client that
        has the source hidden still receives its frames — hide is client-side —
        so a hidden panel stays live and current for when it's shown again.)"""
        return [c for c in self._conns.values() if up.key in c.sources]

    def _fanout_upstream(self, up, msg, exclude=None):
        for conn in self._interested(up):
            if conn is exclude:
                continue
            self._loop.create_task(self._safe_send(conn.ws, msg))

    def _recent_input_mover(self, nsid):
        """The viewer who drove input on ``nsid`` within the last second, else
        ``None`` (evicting an expired entry)."""
        entry = self._input_movers.get(nsid)
        if entry is None:
            return None
        conn, expiry = entry
        if time.monotonic() > expiry:
            self._input_movers.pop(nsid, None)
            return None
        return conn

    # -- upstream pool -------------------------------------------------------
    def _get_or_create_upstream(self, ws_uri, http_parts, label, cookie, offset):
        key = (ws_uri, cookie or "")
        up = self._upstreams.get(key)
        if up is not None:
            return up
        tag = f"s{self._tag_seq}"
        self._tag_seq += 1
        up = _Upstream(ws_uri, http_parts, label, cookie, offset, tag)
        self._upstreams[key] = up
        self._tag_to_upstream[tag] = up
        return up

    def _attach(self, conn, up):
        """Add ``up`` to a browser's view: ref it, start it if new, replay its cache."""
        if up.key in conn.sources:
            return
        conn.sources.add(up.key)
        up.refs += 1
        # A dial-in source has no outbound retry loop — its lifecycle is the
        # inbound socket's (see on_connect / on_disconnect).
        if up._task is None and not up.dialin:
            up._task = self._loop.create_task(self._run_upstream(up))
        for msg in up.cached_frames():
            self._loop.create_task(self._safe_send(conn.ws, msg))

    def _release(self, conn, key):
        """Drop a source from a browser's view; tear the upstream down if now unused."""
        if key not in conn.sources:
            return
        conn.sources.discard(key)
        up = self._upstreams.get(key)
        if up is None:
            return
        self._send_source_teardown(conn.ws, up)
        up.refs -= 1
        if up.refs <= 0:
            if up.dialin:
                return  # lifecycle belongs to the source socket, not browser refs
            if up._task is not None:
                up._task.cancel()
            self._upstreams.pop(key, None)
            self._tag_to_upstream.pop(up.tag, None)

    # -- source roster to the browser ----------------------------------------
    def _sources_message(self, conn):
        items = []
        for key in conn.sources:
            up = self._upstreams.get(key)
            if up is None:
                continue
            items.append({"sid": up.tag, "uri": up.ws_uri, "label": up.label,
                          "status": up.status, "offset": list(up.offset)})
        return {"type": "merge_sources", "sources": items}

    def _emit_sources(self, conn):
        self._loop.create_task(self._safe_send(conn.ws, self._sources_message(conn)))

    def _emit_sources_to_interested(self, up):
        for conn in self._conns.values():
            if up.key in conn.sources:
                self._emit_sources(conn)

    # -- adding a source (with the auth handshake) ---------------------------
    async def _add_source_for_conn(self, conn, spec, password=None):
        """Resolve a source spec for one browser: authenticate if needed, attach.

        With no password, an open source attaches immediately; a protected one asks
        the browser for a password (``merge_auth_required``). With a password, the
        source's ``/__auth__`` flow runs and the resulting cookie authenticates the
        upstream; a wrong password reports ``merge_auth_failed``.
        """
        if conn.ws not in self._conns:
            return  # browser left mid-handshake
        try:
            ws_uri, http_parts, label = _parse_source(spec)
        except Exception:
            return
        offset = (0, 0)
        cookie = None
        loop = self._loop
        if password is not None:
            cookie = await loop.run_in_executor(None, _authenticate, http_parts, password)
            if not cookie:
                await self._safe_send(conn.ws, {"type": "merge_auth_failed",
                                                "uri": ws_uri, "label": label})
                return
        else:
            status = await loop.run_in_executor(None, _probe_source, http_parts)
            if status == "auth":
                await self._safe_send(conn.ws, {"type": "merge_auth_required",
                                                "uri": ws_uri, "label": label})
                return
            # "open" attaches; "offline" still attaches (the upstream retries and
            # shows as offline in the roster until it comes up).
        if conn.ws not in self._conns:
            return
        up = self._get_or_create_upstream(ws_uri, http_parts, label, cookie, offset)
        self._attach(conn, up)
        self._emit_sources(conn)

    # -- Bridge hooks (called from Bridge.handle_connection / _on_message) ----
    def on_connect(self, ws, qp):
        """Register a freshly-connected browser and seed its sources: the
        canvas-wide (``Canvas.merge`` / CLI) set that everyone gets, PLUS any this
        browser asked for via ``?sources=``. Called after the owning Bridge has
        replayed its own state, so a hub's own panels arrive first.

        A connection carrying ``?source=1`` is not a browser at all — it's a
        **dial-in source** (an SDK process that dialed the hub instead of
        serving a canvas for the hub to dial). It gets a dial-in upstream and
        no ``_Conn``; the Bridge has already replayed the hub's state to it, so
        it can observe the canvas like any subscriber."""
        if qp and qp.get("source"):
            self._attach_dialin(ws, qp)
            return
        conn = _Conn(ws)
        self._conns[ws] = conn
        # Dial-in sources are canvas-wide: every browser sees them, frozen
        # replay included when one is offline-retained — and the roster must
        # arrive with them (without this, a late-joining browser's merge panel
        # stays empty until something changes).
        attached_dialin = False
        for up in list(self._upstreams.values()):
            if up.dialin:
                self._attach(conn, up)
                attached_dialin = True
        if attached_dialin:
            self._emit_sources(conn)
        for src in list(self._shared_sources):
            self._loop.create_task(self._add_shared_source_for_conn(conn, src))
        raw_sources = qp.get("sources") if qp else None
        if raw_sources:
            for spec in [s for s in raw_sources.split(",") if s]:
                self._loop.create_task(self._add_source_for_conn(conn, spec))

    def _attach_dialin(self, ws, qp):
        """Bind an inbound source connection to its (possibly pre-existing)
        dial-in upstream. Identity is the ``label``: a source re-dialing under
        the same label is the same source's next life, so any retained frames
        from the previous one are stale (ids are minted per run) — tear them
        down and let the fresh registers repopulate."""
        label = (qp.get("label") or "").strip() or f"source{self._tag_seq}"
        key = (f"dialin:{label}", "")
        up = self._upstreams.get(key)
        if up is None:
            tag = f"s{self._tag_seq}"
            self._tag_seq += 1
            up = _Upstream(f"dialin:{label}", None, label, None, (0, 0), tag)
            up.dialin = True
            self._upstreams[key] = up
            self._tag_to_upstream[tag] = up
        elif up.registers or up.arrows or up.drawings:
            for conn in self._interested(up):
                self._send_source_teardown(conn.ws, up)
            up.registers.clear()
            up.updates.clear()
            up.arrows.clear()
            up.shapes.clear()
            up.drawings.clear()
        up.ws = _InboundWS(ws)
        up.status = "live"
        self._dialins[ws] = up
        for conn in list(self._conns.values()):
            self._attach(conn, up)
        self._emit_sources_to_interested(up)

    def on_disconnect(self, ws):
        """Release everything a departing browser was viewing — or, for a
        departing dial-in source, apply the offline policy (retain: freeze its
        panels in place; otherwise: tear down and forget it)."""
        up = self._dialins.pop(ws, None)
        if up is not None:
            up.ws = None
            self._on_upstream_down(up)      # freeze (retain) or teardown+clear
            if not self.retain:
                stale = [c for c in self._conns.values() if up.key in c.sources]
                for c in stale:
                    c.sources.discard(up.key)
                    up.refs -= 1
                self._upstreams.pop(up.key, None)
                self._tag_to_upstream.pop(up.tag, None)
                for c in stale:
                    self._emit_sources(c)
            return
        conn = self._conns.pop(ws, None)
        if conn is not None:
            for key in list(conn.sources):
                self._release(conn, key)

    # -- free-form drawing on the merged view --------------------------------
    def _has_namespaced(self, diff):
        """True if any record in the diff belongs to a merged source."""
        for bucket in ("added", "updated", "removed"):
            b = diff.get(bucket)
            if isinstance(b, dict):
                for rid in b:
                    if isinstance(rid, str) and self._tag_to_upstream.get(self._strip(rid)[0]):
                        return True
        return False

    def _route_draw(self, conn, ws, diff):
        """Handle a merged-view draw diff that touches at least one source's ink:
        source-owned records (namespaced id) are stripped and routed back to the
        owning canvas; any bare records are the hub's own ink, applied through the
        Bridge's normal draw path (persist + taps + relay to other viewers).

        A *pure*-bare diff never reaches here — :meth:`route` lets the base handle
        it — so this only replicates the bare path for the rare mixed diff.
        """
        per_source = {}
        bare = {"added": {}, "updated": {}, "removed": {}}
        for bucket in ("added", "updated", "removed"):
            b = diff.get(bucket)
            if not isinstance(b, dict):
                continue
            for rid, val in b.items():
                tag = self._strip(rid)[0] if isinstance(rid, str) else None
                up = self._tag_to_upstream.get(tag)
                if up is not None:
                    per_source.setdefault(up, {"added": {}, "updated": {}, "removed": {}})[bucket][rid] = val
                else:
                    bare[bucket][rid] = val
        for up, sd in per_source.items():
            stripped = self._remap_draw_diff(sd, lambda i: self._strip(i)[1])
            ox, oy = up.offset
            if ox or oy:   # merged coords -> the source's own coords
                stripped = self._offset_draw_diff(stripped, -ox, -oy)
            self._loop.create_task(up.send({"type": "draw", "diff": stripped}))
        if any(bare[bucket] for bucket in ("added", "updated", "removed")):
            viewer = self.bridge._viewers.get(ws) or {}
            view = self.bridge._view_for(viewer.get("id"), viewer.get("role")) or {}
            if not view.get("read_only"):
                self.bridge._apply_draw(bare)
                self.bridge.broadcast({"type": "draw", "diff": bare}, exclude=ws)

    async def _add_shared_source_for_conn(self, conn, src):
        """Attach a canvas-wide source ``{spec, offset, password}`` to one browser,
        authenticating with its supplied password (there's no browser to prompt for
        a canvas-wide source, so a protected one must carry its ``password=``)."""
        if conn.ws not in self._conns:
            return
        try:
            ws_uri, http_parts, label = _parse_source(src["spec"])
        except Exception:
            return
        cookie = None
        if src.get("password") is not None:
            cookie = await self._loop.run_in_executor(
                None, _authenticate, http_parts, src["password"])
            if not cookie:
                return
        if conn.ws not in self._conns:
            return
        up = self._get_or_create_upstream(ws_uri, http_parts, label, cookie, src["offset"])
        self._attach(conn, up)
        self._emit_sources(conn)

    async def route(self, ws, raw):
        """The Bridge's ``_on_message`` gate: handle a merge-plane frame and return
        ``True``, else ``False`` so the base handles it normally.

        Handled here: the merge control messages (add/auth/remove), a ``draw`` that
        touches a merged source, and any interaction (input/layout/…) addressed to
        a merged panel (a namespaced ``s<N>:`` id) — forwarded to the owning canvas.
        Everything else (heartbeat/chat/cursor, and interactions on the hub's OWN
        panels, whose ids are bare) returns ``False``.

        Frames FROM a dial-in source are its canvas content — register/update/
        remove/arrow/draw are ingested through the same path a dialed-out
        source's frames take (namespaced, cached, fanned out). Anything else it
        sends (heartbeat, input on a hub panel it observed) falls through to
        the base viewer path: a source is also a subscriber that may petition.
        """
        up = self._dialins.get(ws)
        if up is not None:
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                return False
            kind = msg.get("type")
            if kind in ("register", "update", "remove", "arrow", "draw",
                        "shape", "shape_update", "response"):
                self._ingest(up, raw)
                return True
            # A source is also a peer on the shared plane: petitions and
            # subscriptions it addresses to ANOTHER source's panel (namespaced
            # id) route through the hub like a browser's would. Bare ids (the
            # hub's own panels) fall through to the base viewer path.
            cid = msg.get("id")
            if isinstance(cid, str):
                tag, orig = self._strip(cid)
                target = self._tag_to_upstream.get(tag)
                if target is not None:
                    if kind in ("subscribe", "unsubscribe"):
                        self._sub(ws, cid, kind == "subscribe")
                        return True
                    out = dict(msg)
                    out["id"] = orig
                    self._unoffset_out(target, out, kind)
                    self._record(kind, cid, msg)
                    if kind == "request" and msg.get("reqId") is not None:
                        self._note_request(msg["reqId"], ws)
                    await target.send(out)
                    if kind == "input":
                        self._relay_input_subs(ws, cid, msg.get("payload"))
                    return True
            return False
        conn = self._conns.get(ws)
        if conn is None:
            return False
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return False
        kind = msg.get("type")
        if kind in ("merge_add", "merge_auth"):
            uri = msg.get("uri")
            if uri:
                self._loop.create_task(
                    self._add_source_for_conn(conn, uri, msg.get("password")))
            return True
        if kind == "merge_remove":
            up = self._tag_to_upstream.get(msg.get("sid"))
            if up is not None:
                self._release(conn, up.key)
                self._emit_sources(conn)
            return True
        if kind == "merge_offset":
            up = self._tag_to_upstream.get(msg.get("sid"))
            if up is not None:
                self.set_offset(up.ws_uri, (msg.get("x", 0), msg.get("y", 0)))
            return True
        if kind == "draw":
            diff = msg.get("diff") or {}
            if self._has_namespaced(diff):
                self._record("draw", None, msg)
                self._route_draw(conn, ws, diff)
                return True
            return False  # pure local/native ink — the base draw path handles it
        # -- interaction on a merged panel: forward to the owning source --
        cid = msg.get("id")
        if not isinstance(cid, str):
            return False
        tag, orig = self._strip(cid)
        up = self._tag_to_upstream.get(tag)
        if up is None:
            return False  # a bare id (the hub's own panel) — the base handles it
        if kind in ("subscribe", "unsubscribe"):
            # Subscriptions to a merged panel live at the hub: the hub relays a
            # copy of forwarded input frames itself (the owner never needs to
            # know who's listening through the hub).
            self._sub(ws, cid, kind == "subscribe")
            return True
        out = dict(msg)
        out["id"] = orig
        self._unoffset_out(up, out, kind)
        self._record(kind, cid, msg)
        if kind == "request" and msg.get("reqId") is not None:
            self._note_request(msg["reqId"], ws)
        # start/end on any forwarded frame reference sibling ids in the same source
        if isinstance(out.get("start"), str):
            out["start"] = self._strip(out["start"])[1]
        if isinstance(out.get("end"), str):
            out["end"] = self._strip(out["end"])[1]
        await up.send(out)
        # Keep the merge's cache current for changes made THROUGH the merged view,
        # so a hide/show (cache replay) doesn't snap the panel back:
        #
        # * layout — the geometry is fully described by the frame itself and is
        #   represented directly (top-level x/y, props w/h), so fold it into the
        #   cache and fan it to the OTHER viewers (the mover already applied it, so
        #   excluding them avoids rubber-banding). The source still excludes the
        #   proxy from layout echoes, so this is the only copy.
        # * input — a control's *display* state isn't the raw input payload (a
        #   slider's value rides a {post: v} push, not {value: v}), and it's
        #   component-specific, so we DON'T guess it here. Instead the source echoes
        #   its authoritative state to the proxy (see ?proxy=1), which the update
        #   path above caches and relays. We only record who moved it, so that
        #   echo isn't fanned back to them.
        if kind == "layout":
            geom = {k: msg[k] for k in
                    ("x", "y", "w", "h", "rotation", "autoH", "autoW")
                    if msg.get(k) is not None}
            if geom:
                self._fold_state(up, cid, geom)
                fan = {"type": "update", "id": cid, "payload": geom}
                for other in list(self._conns.values()):
                    if other is not conn and up.key in other.sources:
                        self._loop.create_task(self._safe_send(other.ws, fan))
        elif kind == "input":
            self._input_movers[cid] = (conn, time.monotonic() + 1.0)
            self._relay_input_subs(ws, cid, msg.get("payload"))
        return True

    def _compose_endpoint(self, up, ref):
        """An arrow endpoint as the composed canvas knows it.

        A source's own panel id gets the source's namespace (the historical
        behaviour). A reference to a panel the sender can SEE but doesn't own —
        the hub's own panel by its bare id, or another source's panel by its
        already-namespaced id — passes through untouched. That's what makes
        **cross-source arrows** work: a dial-in peer holds the composed
        replica, so the ids it binds to are already the composed ones.
        """
        if ref in self.bridge._components:            # the hub's own panel
            return ref
        tag, rest = self._strip(ref)
        if rest and tag in self._tag_to_upstream:     # another source's panel
            return ref
        return self._ns(up.tag, ref)                  # the sender's own panel

    def _note_request(self, req_id, ws):
        """Remember who asked, so the owner's response routes back to them.
        Entries expire (30s) and the table self-prunes on insert."""
        now = time.monotonic()
        if len(self._pending_req) > 256:
            self._pending_req = {k: v for k, v in self._pending_req.items()
                                 if v[1] > now}
        self._pending_req[req_id] = (ws, now + 30.0)

    def _record(self, kind, cid, msg):
        """Append one routed user action to the hub ledger (no-op when off;
        an append failure must never take down the routing path)."""
        if self._ledger is None:
            return
        try:
            self._ledger.append_event(kind, cid, msg)
        except Exception:
            traceback.print_exc()

    # -- shared-plane helpers (subscriptions on merged panels) ----------------
    def _sub(self, ws, nsid, on):
        """Record/drop a subscription to a merged panel's input events. Stored
        in the bridge's table (one table for bare and namespaced ids; the
        bridge's disconnect cleanup covers both)."""
        subs = self.bridge._input_subs
        if on:
            subs.setdefault(nsid, set()).add(ws)
        else:
            existing = subs.get(nsid)
            if existing is not None:
                existing.discard(ws)

    def _relay_input_subs(self, origin_ws, nsid, payload):
        """Copy a forwarded input event to the hub-side subscribers of that
        merged panel (the owner dispatches its own handlers; subscribers react
        in parallel). The originator is excluded."""
        for sub in list(self.bridge._input_subs.get(nsid, ())):
            if sub is not origin_ws:
                self._loop.create_task(self._safe_send(
                    sub, {"type": "input", "id": nsid, "payload": payload}))

    @staticmethod
    def _unoffset_out(up, out, kind):
        """Undo the hub's origin offset on a frame headed back to its owner:
        merged-view coords -> the source's own coords. Layout carries x/y at
        the top level; set_props nests them in ``props``."""
        ox, oy = up.offset
        if not (ox or oy):
            return
        if kind == "layout":
            if out.get("x") is not None:
                out["x"] -= ox
            if out.get("y") is not None:
                out["y"] -= oy
        elif kind == "set_props" and isinstance(out.get("props"), dict):
            p = dict(out["props"])
            if isinstance(p.get("x"), (int, float)):
                p["x"] -= ox
            if isinstance(p.get("y"), (int, float)):
                p["y"] -= oy
            out["props"] = p


class MergeBridge(Bridge):
    """A :class:`Bridge` for the dedicated :class:`Merge` server: a bridge with no
    components of its own that runs a :class:`_MergeHost`. A normal
    :class:`~danvas.Canvas` served with ``merge=True`` runs the same host on its
    own bridge, composing merged sources alongside its panels — this class is just
    the component-less variant the CLI/``Merge`` entry point uses."""

    def __init__(self, default_sources=None, default_auth=None, region_width=0,
                 retain=True):
        super().__init__()
        self._merge = _MergeHost(self, default_sources=default_sources,
                                 default_auth=default_auth, region_width=region_width,
                                 retain=retain)


class Merge:
    """Public entry point: a standing merge server that composes a per-connection
    set of running canvases onto one new port.

    ``sources`` is an optional list of ports (``8001``) or addresses
    (``"host:8001"``) that seed the **default** set — the sources a browser sees
    when it connects without its own ``?sources=`` list. Browsers add/remove/hide
    sources live from the merge panel regardless. ``region_width`` spreads the
    default sources side-by-side (each in its own horizontal region that many px
    wide) instead of overlaying them.
    """

    def __init__(self, sources=None, region_width=0, auth=None, retain=True):
        defaults = []
        for i, spec in enumerate(sources or []):
            _u, _h, _label = _parse_source(spec)
            defaults.append((spec, (i * region_width, 0)))
        # auth: {label -> password} for CLI-seeded protected sources.
        norm_auth = {}
        for k, v in (auth or {}).items():
            _u, _h, label = _parse_source(k)
            norm_auth[label] = v
        # retain (default True): a dead source's panels stay on the merged view,
        # frozen (dimmed, non-operable) at their last-known state, until it comes
        # back — see _MergeHost.retain. retain=False drops them instead.
        self._bridge = MergeBridge(default_sources=defaults, default_auth=norm_auth,
                                   region_width=region_width, retain=retain)
        self._server = None
        self._tunnel = None

    def serve(self, port=8080, open_browser=True, host="127.0.0.1", block=True,
              tunnel=False, tunnel_provider="cloudflared", password=None):
        """Start the merge server.

        With ``block=True`` (default) this blocks until shutdown. With
        ``block=False`` it starts in the background and returns ``self`` (use in a
        notebook, then call :meth:`stop`). ``tunnel=True`` exposes the merged view
        publicly over HTTPS. ``password`` gates the hub behind the same
        ``/__auth__`` login a protected canvas uses — browsers see the password
        page once; dial-in sources authenticate with ``password=`` on
        :class:`~danvas.SourceClient` / :func:`danvas.connect`.
        """
        self._bridge._auth = bool(password)
        if not block:
            self._server = server.run_background(
                self._bridge, port=port, open_browser=open_browser, host=host,
                compress=tunnel, password=password,
            )
            if tunnel:
                self._start_tunnel(port, tunnel_provider)
            return self
        if tunnel:
            self._start_tunnel(port, tunnel_provider)
        try:
            server.run(self._bridge, port=port, open_browser=open_browser,
                       host=host, compress=tunnel, password=password)
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
        """Signal the background merge server to shut down and close any tunnel."""
        if self._server is not None:
            self._server.should_exit = True
        self._stop_tunnel()


def _parse_auth_flags(pairs):
    """``["host:8001=pw", ...]`` -> ``{"host:8001": "pw"}``."""
    out = {}
    for pair in pairs or []:
        uri, sep, pw = pair.partition("=")
        if sep:
            out[uri.strip()] = pw
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m danvas.merge",
        description="Standing merge server: compose running danvas canvases into "
                    "one view. Browsers pick their sources via ?sources= or the "
                    "in-UI merge panel; positional sources seed the default set.",
    )
    parser.add_argument(
        "sources", nargs="*",
        help="default source canvases as PORT, :PORT, HOST:PORT, or a full tunnel "
             "URL (used by connections that don't pass ?sources=)",
    )
    parser.add_argument("--port", type=int, default=8080, help="port to serve on")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--no-open", action="store_true", help="don't open a browser")
    parser.add_argument("--region-width", type=int, default=0,
                        help="spread default sources side-by-side, this many px "
                             "each (0 = overlay, preserving real coordinates)")
    parser.add_argument("--auth", action="append", metavar="URI=PASSWORD",
                        help="password for a protected default source (repeatable)")
    parser.add_argument("--password", default=None,
                        help="gate the merged view behind a password (the "
                             "same /__auth__ login a protected canvas uses)")
    parser.add_argument("--no-retain", action="store_true",
                        help="drop a dead source's panels from the merged view "
                             "(default: keep them frozen — dimmed, non-operable "
                             "— at their last-known state until it reconnects)")
    parser.add_argument("--tunnel", action="store_true",
                        help="expose the merged view on the public internet")
    parser.add_argument("--tunnel-provider", default="cloudflared",
                        choices=["cloudflared", "localtunnel"],
                        help="tunnel backend for --tunnel (default cloudflared)")
    args = parser.parse_args(argv)
    Merge(
        args.sources,
        region_width=args.region_width,
        auth=_parse_auth_flags(args.auth),
        retain=not args.no_retain,
    ).serve(port=args.port, open_browser=not args.no_open, host=args.host,
            tunnel=args.tunnel, tunnel_provider=args.tunnel_provider,
            password=args.password)


if __name__ == "__main__":
    main()
