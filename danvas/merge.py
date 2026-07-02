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

Limitations: binary media (video/audio feeds) is not relayed through the merge,
cross-canvas arrows are not supported (an arrow binds by panel id within one
canvas), and rearranging panels in the merged view is local to the merge server. A
source going offline drops its panels (and its ink) until it reconnects.
"""

import argparse
import asyncio
import json
import re
import time
import traceback
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import quote, urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from . import server
from .bridge import Bridge


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


class _Upstream:
    """One client connection to a source canvas, shared by every browser that
    requested this exact ``(ws_uri, cookie)`` pair.

    Pooled and reference-counted: two browsers viewing the same *open* canvas
    share one upstream, but two viewing it under *different passwords* (= different
    roles) get two, because the source filters per-connection by role. Caches the
    source's frames (already id-namespaced) so a newly-interested browser can be
    replayed without re-fetching.
    """

    def __init__(self, ws_uri, http_parts, label, cookie, offset, tag):
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
        self.drawings = {}    # nsid -> free-form drawing record (the "after" state)

    async def send(self, msg):
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps(msg))
        except WebSocketException:
            pass

    def cached_frames(self):
        """Every frame needed to reconstruct this source on a fresh browser."""
        yield from self.registers.values()
        for nsid, payload in self.updates.items():
            yield {"type": "update", "id": nsid, "payload": payload}
        yield from self.arrows.values()
        if self.drawings:
            # The source's free-form ink, replayed as one "added" draw diff.
            yield {"type": "draw",
                   "diff": {"added": dict(self.drawings), "updated": {}, "removed": {}}}

    def cached_ids(self):
        return list(self.registers) + list(self.arrows)


class _Conn:
    """One browser connected to the merge server and the sources it has chosen."""

    def __init__(self, ws):
        self.ws = ws
        self.sources = set()   # upstream.key it currently sees
        self.hidden = set()    # upstream.key eye-toggled off (kept, not rendered)


class MergeBridge(Bridge):
    """A :class:`Bridge` that serves a per-connection union of source canvases.

    Unlike a normal bridge it owns no component objects. It maintains a pool of
    upstream client connections keyed by ``(source, credential)``, maps each
    namespaced id back to its owning upstream for interaction routing, and fans
    each source's frames only to the browsers that asked for it.
    """

    def __init__(self, default_sources=None, default_auth=None, region_width=0):
        super().__init__()
        # Specs (+ offsets) seeded for a connection that doesn't pass ?sources=.
        self._default_sources = list(default_sources or [])   # [(spec, (ox,oy))]
        self._default_auth = dict(default_auth or {})          # label -> password
        self._region_width = region_width
        self._upstreams = {}          # key -> _Upstream
        self._tag_to_upstream = {}    # tag -> _Upstream
        self._conns = {}              # ws -> _Conn
        self._tag_seq = 0

    # -- startup -------------------------------------------------------------
    def set_loop(self, loop):
        """Capture the running loop; upstreams are launched lazily per demand."""
        super().set_loop(loop)

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

    # -- inbound from a source (downstream) ----------------------------------
    async def _run_upstream(self, up):
        """Stay connected to one source: replay on connect, relay, reconnect.

        Runs until the last interested browser releases the upstream (the task is
        then cancelled). On every drop, the source's panels are removed from the
        interested browsers and its caches cleared, so a restart heals cleanly.
        """
        while True:
            try:
                headers = ({"Cookie": f"pc_session={up.cookie}"} if up.cookie else None)
                async with connect(up.ws_uri, max_size=None,
                                   additional_headers=headers) as ws:
                    up.ws = ws
                    up.status = "live"
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
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return  # binary media frame or non-JSON -- not composited
        kind = msg.get("type")
        ox, oy = up.offset
        if kind == "register":
            cid = self._ns(up.tag, msg.get("id"))
            msg["id"] = cid
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
            up.updates.setdefault(cid, {}).update(payload)
            self._fanout_upstream(up, {"type": "update", "id": cid, "payload": payload})
        elif kind == "arrow":
            cid = self._ns(up.tag, msg.get("id"))
            msg["id"] = cid
            if isinstance(msg.get("start"), str):
                msg["start"] = self._ns(up.tag, msg["start"])
            if isinstance(msg.get("end"), str):
                msg["end"] = self._ns(up.tag, msg["end"])
            up.arrows[cid] = msg
            self._fanout_upstream(up, msg)
        elif kind == "remove":
            cid = self._ns(up.tag, msg.get("id"))
            up.registers.pop(cid, None)
            up.updates.pop(cid, None)
            up.arrows.pop(cid, None)
            self._fanout_upstream(up, {"type": "remove", "id": cid})
        elif kind == "draw":
            # Free-form ink drawn on the source (or its on-connect replay): namespace
            # every record id by the source tag so sources can't collide, cache it
            # for replay, and fan it out to the browsers viewing this source.
            ns_diff = self._remap_draw_diff(msg.get("diff") or {},
                                            lambda i: self._ns(up.tag, i))
            self._fold_draw(up.drawings, ns_diff)
            self._fanout_upstream(up, {"type": "draw", "diff": ns_diff})

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
        """A source dropped: remove its panels + ink from the interested browsers
        and clear its caches, then mark it offline in their rosters."""
        up.status = "offline"
        for conn in self._interested(up):
            self._send_source_teardown(conn.ws, up)
        up.registers.clear()
        up.updates.clear()
        up.arrows.clear()
        up.drawings.clear()
        self._emit_sources_to_interested(up)

    # -- fan-out -------------------------------------------------------------
    def _interested(self, up):
        """The browser connections currently rendering this upstream."""
        return [c for c in self._conns.values()
                if up.key in c.sources and up.key not in c.hidden]

    def _fanout_upstream(self, up, msg):
        for conn in self._interested(up):
            self._loop.create_task(self._safe_send(conn.ws, msg))

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
        if up._task is None:
            up._task = self._loop.create_task(self._run_upstream(up))
        for msg in up.cached_frames():
            self._loop.create_task(self._safe_send(conn.ws, msg))

    def _release(self, conn, key):
        """Drop a source from a browser's view; tear the upstream down if now unused."""
        if key not in conn.sources:
            return
        conn.sources.discard(key)
        conn.hidden.discard(key)
        up = self._upstreams.get(key)
        if up is None:
            return
        self._send_source_teardown(conn.ws, up)
        up.refs -= 1
        if up.refs <= 0:
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
                          "status": up.status, "hidden": key in conn.hidden})
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

    # -- browser-facing server (overrides Bridge's object-based replay) ------
    async def handle_connection(self, ws, role=None):
        await ws.accept()
        self._connections.add(ws)
        self._last_seen[ws] = time.monotonic()
        viewer = self._make_viewer()
        self._viewers[ws] = viewer
        conn = _Conn(ws)
        self._conns[ws] = conn
        self._broadcast_roster()
        try:
            await self._send(ws, {"type": "welcome", "you": viewer, "mergeHost": True})
            for entry in self._chat_history:
                await self._send(ws, entry)
            # Seed the source set from ?sources= (URLs only), else the default set.
            qp = getattr(ws, "query_params", {})
            raw_sources = qp.get("sources") if qp else None
            if raw_sources:
                for spec in [s for s in raw_sources.split(",") if s]:
                    self._loop.create_task(self._add_source_for_conn(conn, spec))
            else:
                for spec, offset in self._default_sources:
                    _u, _h, label = _parse_source(spec)
                    self._loop.create_task(self._add_default_source(conn, spec, offset, label))
            # Replay the merged view's own annotation ink (drawn by merge viewers,
            # not owned by any source) to this fresh viewer.
            if self._drawings:
                await self._send(ws, {"type": "draw", "diff": {
                    "added": dict(self._drawings), "updated": {}, "removed": {}}})
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                text = msg.get("text")
                if text:
                    await self._route_from_browser(conn, text)
        except Exception:
            pass
        finally:
            for key in list(conn.sources):
                self._release(conn, key)
            self._conns.pop(ws, None)
            self._connections.discard(ws)
            self._send_locks.pop(ws, None)
            self._viewers.pop(ws, None)
            self._last_seen.pop(ws, None)
            self._broadcast_roster()

    def _handle_merge_draw(self, conn, diff):
        """Route a merged-view draw diff: source-owned records (namespaced id) go
        back up to the owning canvas, stripped; merge-native records (bare id) are
        stored on the merge server and relayed to the other merge viewers.

        Splitting per record keeps a mixed diff correct: a viewer erasing a source
        stroke while adding a fresh one sends one diff carrying both.
        """
        per_source = {}
        local = {"added": {}, "updated": {}, "removed": {}}
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
                    local[bucket][rid] = val
        # Source-owned edits: strip the namespace and forward to the owning canvas.
        # The source applies + re-broadcasts, which comes back down to every viewer,
        # so they converge (no local apply here — that would double it).
        for up, sd in per_source.items():
            stripped = self._remap_draw_diff(sd, lambda i: self._strip(i)[1])
            self._loop.create_task(up.send({"type": "draw", "diff": stripped}))
        # Merge-native ink: fold into the merge server's own drawing set (so a fresh
        # viewer replays it) and relay to the OTHER merge viewers.
        if any(local[bucket] for bucket in ("added", "updated", "removed")):
            self._apply_draw(local)
            for other in list(self._conns.values()):
                if other is not conn:
                    self._loop.create_task(self._safe_send(other.ws, {"type": "draw", "diff": local}))

    async def _add_default_source(self, conn, spec, offset, label):
        """Attach a CLI-seeded default source, honouring its ``--auth`` password
        and region offset (the browser-driven ``_add_source_for_conn`` uses no
        offset and prompts for passwords instead)."""
        if conn.ws not in self._conns:
            return
        try:
            ws_uri, http_parts, _label = _parse_source(spec)
        except Exception:
            return
        password = self._default_auth.get(label)
        cookie = None
        if password is not None:
            cookie = await self._loop.run_in_executor(None, _authenticate, http_parts, password)
            if not cookie:
                return
        if conn.ws not in self._conns:
            return
        up = self._get_or_create_upstream(ws_uri, http_parts, label, cookie, offset)
        self._attach(conn, up)
        self._emit_sources(conn)

    async def _route_from_browser(self, conn, raw):
        """Route a browser frame: merge control messages, chat/presence, or an
        interaction forwarded up to the source that owns the addressed panel."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        self._last_seen[conn.ws] = time.monotonic()
        kind = msg.get("type")
        if kind == "heartbeat":
            return
        if kind == "set_name":
            self._rename_viewer(conn.ws, msg.get("name"))
            return
        if kind == "chat":
            self._handle_chat(conn.ws, msg.get("text"))
            return
        # -- merge control plane --
        if kind == "merge_add":
            uri = msg.get("uri")
            if uri:
                self._loop.create_task(
                    self._add_source_for_conn(conn, uri, msg.get("password")))
            return
        if kind == "merge_auth":
            uri = msg.get("uri")
            if uri:
                self._loop.create_task(
                    self._add_source_for_conn(conn, uri, msg.get("password")))
            return
        if kind == "merge_remove":
            up = self._tag_to_upstream.get(msg.get("sid"))
            if up is not None:
                self._release(conn, up.key)
                self._emit_sources(conn)
            return
        if kind == "draw":
            # Free-form ink drawn on the merged view. A stroke on a SOURCE's ink
            # (namespaced id) routes back to that canvas; a fresh stroke (bare id)
            # is the merged view's own annotation layer, shared among merge viewers.
            self._handle_merge_draw(conn, msg.get("diff") or {})
            return
        if kind == "merge_toggle":
            up = self._tag_to_upstream.get(msg.get("sid"))
            if up is not None and up.key in conn.sources:
                if msg.get("hidden"):
                    conn.hidden.add(up.key)
                    self._send_source_teardown(conn.ws, up)
                else:
                    conn.hidden.discard(up.key)
                    for frame in up.cached_frames():
                        await self._safe_send(conn.ws, frame)
                self._emit_sources(conn)
            return
        # -- interaction: forward to the owning source --
        cid = msg.get("id")
        if not isinstance(cid, str):
            return
        tag, orig = self._strip(cid)
        up = self._tag_to_upstream.get(tag)
        if up is None:
            return
        out = dict(msg)
        out["id"] = orig
        if kind == "layout":
            ox, oy = up.offset
            if out.get("x") is not None:
                out["x"] -= ox
            if out.get("y") is not None:
                out["y"] -= oy
        # start/end on any forwarded frame reference sibling ids in the same source
        if isinstance(out.get("start"), str):
            out["start"] = self._strip(out["start"])[1]
        if isinstance(out.get("end"), str):
            out["end"] = self._strip(out["end"])[1]
        await up.send(out)


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

    def __init__(self, sources=None, region_width=0, auth=None):
        defaults = []
        for i, spec in enumerate(sources or []):
            _u, _h, _label = _parse_source(spec)
            defaults.append((spec, (i * region_width, 0)))
        # auth: {label -> password} for CLI-seeded protected sources.
        norm_auth = {}
        for k, v in (auth or {}).items():
            _u, _h, label = _parse_source(k)
            norm_auth[label] = v
        self._bridge = MergeBridge(default_sources=defaults, default_auth=norm_auth,
                                   region_width=region_width)
        self._server = None
        self._tunnel = None

    def serve(self, port=8080, open_browser=True, host="127.0.0.1", block=True,
              tunnel=False, tunnel_provider="cloudflared"):
        """Start the merge server.

        With ``block=True`` (default) this blocks until shutdown. With
        ``block=False`` it starts in the background and returns ``self`` (use in a
        notebook, then call :meth:`stop`). ``tunnel=True`` exposes the merged view
        publicly over HTTPS.
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
    ).serve(port=args.port, open_browser=not args.no_open, host=args.host,
            tunnel=args.tunnel, tunnel_provider=args.tunnel_provider)


if __name__ == "__main__":
    main()
