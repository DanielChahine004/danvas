"""The native Canvas API over a dial-in connection: ``danvas.connect(url)``.

``SourceClient`` is the wire-level reference (the executable spec for non-
Python SDKs). This module is the ergonomic layer on top: a **RemoteCanvas**
that *is* a :class:`~danvas.canvas.Canvas` — same factories, same component
objects, same handler threading — whose bridge ships frames up one dial-in
socket instead of fanning out to browsers of its own::

    import danvas

    canvas = danvas.connect("127.0.0.1:8000", label="rig")   # any served canvas
    servo = canvas.slider("servo", min=0, max=180)           # native factory

    @servo.on_change                                          # native handlers
    def _(v):
        print("browser set", v)

    servo.min = 10                                            # native setters
    canvas["servo"].color = (255, 0, 0)                       # native lookup

Because the components are the *real* danvas components bound to a socket-
backed bridge, everything a normal script does — ``update()``, live property
setters, ``set_layout``, ``threaded=True`` handlers, containers — works
unchanged; the only difference is where the frames go. The hub composes these
panels for every viewer, routes interactions back here, and holds them frozen
(retention) if this process dies.

Panels owned by OTHER processes are reachable through the shared property
plane: ``canvas.shared`` mirrors the whole canvas this process joined
(``{id: {"component", "props", "state", ...}}``), ``canvas.set_props(id, ...)``
writes any panel's properties, and ``canvas.subscribe(id, fn)`` reacts to any
panel's input events. What doesn't exist here: ``serve()`` (a RemoteCanvas
joins a canvas, it doesn't host one) and binary media (the merge fabric
doesn't relay it).
"""

import logging
import os

from .bridge import Bridge
from .canvas import Canvas
from .source import SourceClient

_log = logging.getLogger("danvas")


class _RemoteBridge(Bridge):
    """A Bridge whose every outbound path is one dial-in socket.

    The component layer talks to the bridge through a thin waist (broadcast /
    conflated / per-viewer sends / ``register_live`` / the ``_emit`` tail);
    re-pointing that waist at ``SourceClient._send`` makes the entire native
    component stack remote-transparent. Per-viewer scoping (roles/client
    overlays) has no meaning on a single upstream pipe — the hub applies its
    own role filtering to its own viewers — so those sends collapse to the
    shared frame.
    """

    def __init__(self, client):
        super().__init__()
        self._client = client
        # Panels made here are owned (handler-executed) by this process; the
        # hub re-stamps them with our dial-in label on the composed canvas.
        self._owner_label = client.label

    # -- every outbound path becomes "send it up the socket" ------------------
    def _emit(self, targets, msg):
        self._client._send(msg)

    def broadcast(self, msg, exclude=None, roles=None):
        self._client._send(msg)

    def send_to_client(self, viewer_id, msg):
        self._client._send(msg)

    def send_to_role(self, role, msg):
        self._client._send(msg)

    def broadcast_conflated(self, comp_id, *, msg=None, data=None, exclude=None,
                            **kw):
        # The hub coalesces per-browser on its side; up here there is exactly
        # one pipe, so just send — text or media alike.
        if msg is not None:
            self._client._send(msg)
        elif data is not None:
            self._client._send_binary(data)

    def broadcast_binary(self, data, exclude=None, roles=None):
        # Media rides the same envelope up; the hub rewrites the id in-place
        # and relays to browsers (video/audio through a hub works).
        self._client._send_binary(data)

    def register_live(self, component, only_roles=None):
        self._client._send(self.register_message(component))
        state = component.state_payload()
        if state:
            self._client._send({"type": "update", "id": component.id,
                                "payload": state})


class RemoteHandle:
    """A live proxy for a panel ANOTHER process owns, resolved by name.

    Reads come from the connection's converging mirror; writes go through the
    shared property plane (they apply at the owner's real setters); events come
    via subscription. So ``canvas["servo"].max = 90`` and
    ``canvas["go"].on_click(fn)`` work whether the panel is local or lives in
    a peer process — the danvas name is the cross-process identity.
    """

    __slots__ = ("_canvas", "id", "name")

    def __init__(self, canvas, panel_id, name):
        object.__setattr__(self, "_canvas", canvas)
        object.__setattr__(self, "id", panel_id)
        object.__setattr__(self, "name", name)

    # -- reads: the mirror (eventually consistent, like any replica) ----------
    def _entry(self):
        return self._canvas._client.panels.get(self.id) or {}

    def __getattr__(self, key):
        entry = self._entry()
        state = entry.get("state") or {}
        if key in state:
            return state[key]
        props = entry.get("props") or {}
        if key in props:
            return props[key]
        if key in entry:
            return entry[key]
        raise AttributeError(
            f"remote panel {self.name!r} has no visible property {key!r} "
            "(reads come from the replica; only streamed state is readable)")

    # -- writes: the shared property plane -------------------------------------
    def __setattr__(self, key, value):
        self._canvas.set_props(self.id, **{key: value})

    def set_props(self, **props):
        self._canvas.set_props(self.id, **props)
        return self

    def update(self, value=None, **props):
        """Content-verb parity with the native object: ``handle.update("ready")``
        does what the owner's ``label.update("ready")`` does — the value routes
        to the owner's ``update()`` (silent, like any programmatic update);
        keyword props ride along as property writes."""
        if value is not None:
            props["value"] = value
        self._canvas.set_props(self.id, **props)
        return self

    def set_layout(self, **layout):
        self._canvas.set_props(self.id, **layout)
        return self

    # -- events: subscription (the owner's handlers keep running too) ----------
    def on_input(self, fn=None):
        """The raw event feed: ``fn(payload)`` for every input on this panel."""
        return self._canvas.subscribe(self.id, fn)

    def on_click(self, fn=None):
        """Button-flavoured sugar: ``fn()`` per click — alongside (not instead
        of) whatever handler the owning process registered."""
        if fn is None:
            return lambda f: self.on_click(f)
        self._canvas.subscribe(self.id, lambda _p, f=fn: f())
        return fn

    def on_change(self, fn=None):
        """Value-control sugar: ``fn(value)`` per committed change."""
        if fn is None:
            return lambda f: self.on_change(f)
        self._canvas.subscribe(
            self.id, lambda p, f=fn: f(p.get("value", p) if isinstance(p, dict) else p))
        return fn

    def __repr__(self):
        entry = self._entry()
        return (f"<RemoteHandle {self.name!r} ({entry.get('component')}) "
                f"id={self.id}>")


class RemoteCanvas(Canvas):
    """A Canvas that joins a served canvas instead of serving one.

    Built by :func:`danvas.connect`. Everything panel-shaped is inherited from
    :class:`Canvas`; the differences are the socket-backed bridge, replay of
    this process's panels on every (re)connect, and the shared-plane accessors
    for panels other processes own.
    """

    def __init__(self, url, label="python", password=None):
        super().__init__()
        client = SourceClient(url, label=label, password=password)
        self._client = client
        self._bridge = _RemoteBridge(client)
        self._bridge._canvas = self
        # Replay on every (re)connect: same shape as Bridge.handle_connection's
        # replay to a fresh browser — register + current state per panel.
        client._replay_hook = self._replay_frames
        # Interactions the hub routes to this source's panels: dispatch through
        # the real bridge machinery, so handler modes (inline/threaded/
        # dedicated/async) and the authoritative state echo work unchanged.
        client.on_frame(self._on_hub_frame)
        # Binary INPUT the hub routes to this source's panels (sendBinary /
        # camera / mic relays): through the real dispatch, like JSON input.
        client._binary_hook = lambda data: _hub_binary(self._bridge, data)
        # Live-announce inserts from the start: Canvas gates register_live on
        # _serving, and for a RemoteCanvas the dial-in session IS the serving
        # state. Frames sent before connect() drop at the (socket-less) client
        # and the on-connect replay reconstructs them, so it's always safe.
        self._serving = True

    # -- lifecycle -------------------------------------------------------------
    # Named dial() rather than connect(): Canvas.connect(a, b) is the ARROW
    # verb, inherited and fully functional here (an arrow between this
    # process's panels rides the socket like any frame) — the session verb
    # must not shadow it.
    def dial(self, timeout=10.0):
        self._client.connect(timeout=timeout)
        return self

    def close(self):
        self._client.close()

    def serve(self, *a, **kw):
        raise RuntimeError(
            "a RemoteCanvas joins an already-served canvas; open the host "
            "canvas's URL to view it (or use danvas.Canvas().serve() to host)")

    # -- replay / inbound -------------------------------------------------------
    def _replay_frames(self):
        """The frames that reconstruct this process's panels on the hub."""
        for comp in self._bridge._components.values():
            yield self._bridge.register_message(comp)
            state = comp.state_payload()
            if state:
                yield {"type": "update", "id": comp.id, "payload": state}
        for arrow in self._arrows:
            yield arrow.register_message()

    def _on_hub_frame(self, msg):
        _dispatch_hub_frame(self._bridge, msg)

    # -- cross-process lookup: the danvas name is the identity ------------------
    def __getitem__(self, name):
        """``canvas["name"]`` resolves panels this process owns (the native
        component object) AND panels any peer owns (a :class:`RemoteHandle`
        proxying reads/writes/events over the wire). Own panels win a name
        collision; foreign names resolve through the connection's mirror —
        waiting briefly (≤2 s) for it to converge, so a lookup right after
        :func:`danvas.connect` doesn't race the hub's initial replay."""
        import time as _time
        try:
            return super().__getitem__(name)
        except KeyError:
            deadline = _time.monotonic() + 2.0
            while True:
                panel_id = self._client.find(name)
                if panel_id is not None:
                    return RemoteHandle(self, panel_id, name)
                if _time.monotonic() >= deadline:
                    raise
                _time.sleep(0.05)

    def __contains__(self, name):
        return super().__contains__(name) or self._client.find(name) is not None

    @property
    def sources(self):
        """The processes contributing panels to the canvas this one joined:
        ``{owner_label: panel_count}``, derived from the replica ("host" is
        the serving canvas itself; this process's label covers its own)."""
        counts = {}
        for entry in self._client.panels.values():
            owner = entry.get("owner")
            if owner:
                counts[owner] = counts.get(owner, 0) + 1
        for comp in self._bridge._components.values():
            counts[self._client.label] = counts.get(self._client.label, 0) + 1
        return counts

    # -- the shared plane (panels other processes own) --------------------------
    @property
    def shared(self):
        """Live mirror of the canvas this process joined: ``{id: entry}`` with
        ``entry = {"component", "props", "state", x/y/w/h}`` — every panel the
        connection may see, whoever owns it. Eventually consistent."""
        return self._client.panels

    def set_props(self, panel_id, **props):
        """Write any panel's properties by id (see :attr:`shared` for ids) —
        applies at the owning process through its real setters."""
        self._client.set_props(panel_id, **props)
        return self

    def subscribe(self, panel_id, fn=None):
        """React to any panel's input events without owning it."""
        return self._client.subscribe(panel_id, fn)

    def unsubscribe(self, panel_id):
        self._client.unsubscribe(panel_id)
        return self


def _dispatch_hub_frame(bridge, msg):
    """Route one hub frame at a source-side bridge: interactions on this
    process's panels go through the real dispatch machinery (handler threading
    modes and the authoritative echo included)."""
    kind = msg.get("type")
    if kind == "file_pull":
        # The hub is asking for a download token's bytes on a browser's
        # behalf (this process owns them). Reply file_meta + a FILE binary
        # envelope; decline tokens that aren't ours (another source's, or
        # expired). Role-gated tokens are declined over a hub — the hub
        # can't verify the per-token role, so fail closed.
        import json as _json
        req = msg.get("reqId")
        item = bridge.take_download(msg.get("token"))
        if item is None or item[2] is not None:
            bridge.broadcast({"type": "file_meta", "reqId": req, "ok": False})
            return
        filename, source, _role = item
        try:
            data = (bytes(source) if isinstance(source, (bytes, bytearray))
                    else open(source, "rb").read())
        except OSError:
            bridge.broadcast({"type": "file_meta", "reqId": req, "ok": False})
            return
        bridge.broadcast({"type": "file_meta", "reqId": req, "ok": True,
                          "filename": filename})
        rid = str(req).encode()
        bridge.broadcast_binary(bytes([6, len(rid)]) + rid + data)
        return
    if kind == "file_push":
        # An upload is arriving for one of this process's endpoints: the FILE
        # bytes follow on the same socket; stash the meta until they land.
        pushes = getattr(bridge, "_pending_pushes", None)
        if pushes is None:
            pushes = bridge._pending_pushes = {}
        pushes[msg.get("reqId")] = msg
        return
    comp = bridge._components.get(msg.get("id"))
    if comp is None:
        return
    if kind == "input":
        payload = msg.get("payload") or {}
        bridge._dispatch.submit(
            lambda c=comp, p=payload: bridge._dispatch_input(c, p, None))
    elif kind == "layout":
        bridge._dispatch.submit(
            lambda c=comp, m=dict(msg): bridge._dispatch_layout(c, m, None))


# -- serve(broker=True): the binary broker serves; this process is a source --

def _hub_binary(bridge, data):
    """Route a binary envelope from the hub: FILE completes a pending upload
    push; everything else is binary INPUT for this process's panels."""
    import json as _json
    import os as _os
    if len(data) >= 2 and data[0] == 6:                    # FILE
        req = bytes(data[2:2 + data[1]]).decode("utf-8", "replace")
        pushes = getattr(bridge, "_pending_pushes", {})
        push = pushes.pop(req, None)
        if push is None:
            return
        payload = bytes(data[2 + data[1]:])

        def _ack(ok, **extra):
            bridge.broadcast({"type": "file_ack", "reqId": req,
                              "ok": ok, **extra})

        comp = bridge.upload_component(push.get("token"))
        # Unknown endpoint (another source's) or role-gated: decline —
        # the hub can't verify per-endpoint roles, so fail closed.
        if comp is None or getattr(comp, "_role", None) is not None:
            _ack(False)
            return
        max_size = getattr(comp, "_max_size", None)
        if max_size and len(payload) > max_size:
            _ack(False, error="file too large")
            return
        filename = _os.path.basename(push.get("name") or "upload.bin")
        dest = getattr(comp, "_dest", None)
        info = {"name": filename, "size": len(payload),
                "content_type": push.get("content_type")
                or "application/octet-stream",
                "data": None if dest else payload, "path": None}
        if dest:
            from .server import _safe_upload_path
            try:
                target = _safe_upload_path(dest, filename)
                with open(target, "wb") as f:
                    f.write(payload)
                info["path"] = target
                info["name"] = _os.path.basename(target)
            except Exception:
                _ack(False, error="write failed")
                return
        bridge.deliver_upload(comp, info, viewer=None)
        _ack(True, name=info["name"], size=info["size"])
        return
    bridge._on_binary_input(None, data)


def _find_danvasd():
    """Locate the danvasd binary, in priority order: ``$DANVASD`` (explicit
    override), the copy bundled in the installed wheel (``danvas/_bin/``), a
    local cargo build (dev checkout), then ``PATH``.

    The bundled path is why a platform wheel makes ``serve()`` broker-by-
    default with no download: pip picks the wheel for the user's OS, the
    binary rides inside it, and this finds it offline. A pure ``py3-none-any``
    install (other platforms) has no ``_bin/`` and falls through to the
    embedded server."""
    import shutil
    exe = "danvasd.exe" if os.name == "nt" else "danvasd"
    cand = os.environ.get("DANVASD")
    if cand and os.path.exists(cand):
        return cand
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bin", exe)
    if os.path.exists(bundled):
        return bundled
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for profile in ("release", "debug"):
        p = os.path.join(here, "broker", "target", profile, exe)
        if os.path.exists(p):
            return p
    return shutil.which("danvasd")


class _BrokerUnavailable(RuntimeError):
    """The broker binary is absent or won't launch — the caller falls back to
    the embedded server (raised only from the broker launch path)."""


class BrokerHandle:
    """The running broker behind serve(broker=True): stop() ends it."""

    def __init__(self, proc, client):
        self.proc = proc
        self.client = client

    def stop(self):
        try:
            self.client.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass


def serve_via_broker(canvas, port=8000, open_browser=True, block=True,
                     password=None, passwords=None, host="127.0.0.1",
                     existing_port=None, persist=False):
    """EXPERIMENTAL: serve this canvas THROUGH the danvasd binary.

    The broker owns the port (frontend, browsers, retention, ledger, merging);
    this Python process dials in as the ``host`` source — so it can crash and
    restart while the UI survives. The existing bridge is transplanted onto
    the socket (class-swap onto :class:`_RemoteBridge`), so components,
    handlers, and live setters work unchanged.

    Known gaps vs the embedded server (why this isn't the default yet):
    managed shapes, chat, presence/cursors, ``on_request`` replies,
    roles/per-viewer overlays, upload/download endpoints, ``persist=``,
    hot reload, and the hosting button don't cross the hub yet.
    """
    import subprocess
    import time as _time
    import webbrowser

    import socket as _socket
    if existing_port is not None:
        # A broker is already running (the hot-reload monitor owns it) — dial
        # into it instead of spawning our own. The UI lives in that danvasd, so
        # this process restarting (an edit) never drops the browser: retention
        # holds the panels while we re-dial.
        port = existing_port
        proc = None
    else:
        binary = _find_danvasd()
        if binary is None:
            raise _BrokerUnavailable("danvasd binary not found")
        cmd = [binary, "--port", str(port), "--host", str(host or "127.0.0.1")]
        if password:
            cmd += ["--password", str(password)]
        env = dict(os.environ)
        if passwords:
            # Role logins ride the env contract both hubs share.
            env["DANVAS_ROLE_PASSWORDS"] = ",".join(
                f"{r}={pw}" for r, pw in passwords.items())
        proc = subprocess.Popen(cmd, env=env)
        deadline = _time.time() + 15
        while _time.time() < deadline:
            try:
                _socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                if proc.poll() is not None:
                    # Won't launch (wrong arch, corrupt, missing lib): not
                    # fatal — the auto path falls back to the embedded server.
                    raise _BrokerUnavailable(
                        f"danvasd exited on startup (code {proc.returncode})")
                _time.sleep(0.1)
        else:
            proc.terminate()
            raise _BrokerUnavailable("danvasd never opened its port")

    bridge = canvas._bridge
    login = password or (next(iter(passwords.values())) if passwords else None)
    client = SourceClient(f"127.0.0.1:{port}", label="host", password=login)
    # Transplant: the existing bridge (components already bound to it) becomes
    # socket-backed in place — every outbound path now rides the dial-in.
    bridge.__class__ = _RemoteBridge
    bridge._client = client

    def _replay():
        for comp in bridge._components.values():
            yield bridge.register_message(comp)
            state = comp.state_payload()
            if state:
                yield {"type": "update", "id": comp.id, "payload": state}
        for arrow in canvas._arrows:
            yield arrow.register_message()

    client._replay_hook = _replay
    client.on_frame(lambda m: _dispatch_hub_frame(bridge, m))
    client._binary_hook = lambda data: _hub_binary(bridge, data)
    # persist= is an OWNER-side feature (the owner serialises its own canvas
    # state) — it works the same through the broker. Restore BEFORE connecting
    # so the saved layout/values ride the initial replay (no default->restored
    # flicker); the autosave arms on hub-routed layout/input the same way.
    # (Free-form drawings are hub-native, so ink doesn't round-trip through the
    # broker — layout + input values do, which is the bulk of persist's value.)
    if persist:
        try:
            canvas._persist_setup(persist)
        except Exception:
            import traceback as _tb
            _tb.print_exc()
    client.connect()
    canvas._serving = True
    canvas._broker = BrokerHandle(proc, client)
    url = f"http://127.0.0.1:{port}"
    if proc is not None:
        print(f"[danvas] serving via danvasd at {url}"
              f"  (broker pid {proc.pid}; UI survives this process)")
    if open_browser:
        webbrowser.open(url)
    if not block:
        return canvas
    try:
        while True:
            _time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        canvas._broker.stop()
    return canvas


def connect(url, label="python", password=None, timeout=10.0):
    """Join a running canvas as a peer process: ``danvas.connect(url)``.

    Returns a connected :class:`RemoteCanvas` — the normal danvas API, with the
    frames going to the serving canvas instead of to browsers of this
    process's own. (On the returned canvas, ``connect(a, b)`` keeps its normal
    danvas meaning: an arrow between two panels.)
    """
    return RemoteCanvas(url, label=label, password=password).dial(timeout)
