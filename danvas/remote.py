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
        # one pipe, so just send. Binary payloads aren't relayed by the merge
        # fabric — drop them (same limitation as merged canvases).
        if msg is not None:
            self._client._send(msg)

    def broadcast_binary(self, data, exclude=None, roles=None):
        _log.debug("RemoteCanvas: binary media is not relayed through a hub; "
                   "frame dropped")

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
        # Live-announce inserts from the start: Canvas gates register_live on
        # _serving, and for a RemoteCanvas the dial-in session IS the serving
        # state. Frames sent before connect() drop at the (socket-less) client
        # and the on-connect replay reconstructs them, so it's always safe.
        self._serving = True

    # -- lifecycle -------------------------------------------------------------
    def connect(self, timeout=10.0):
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

    def _on_hub_frame(self, msg):
        kind = msg.get("type")
        comp = self._bridge._components.get(msg.get("id"))
        if comp is None:
            return
        if kind == "input":
            payload = msg.get("payload") or {}
            self._bridge._dispatch.submit(
                lambda c=comp, p=payload: self._bridge._dispatch_input(c, p, None))
        elif kind == "layout":
            self._bridge._dispatch.submit(
                lambda c=comp, m=dict(msg): self._bridge._dispatch_layout(c, m, None))

    # -- cross-process lookup: the danvas name is the identity ------------------
    def __getitem__(self, name):
        """``canvas["name"]`` resolves panels this process owns (the native
        component object) AND panels any peer owns (a :class:`RemoteHandle`
        proxying reads/writes/events over the wire). Own panels win a name
        collision; foreign names resolve through the connection's mirror."""
        try:
            return super().__getitem__(name)
        except KeyError:
            panel_id = self._client.find(name)
            if panel_id is not None:
                return RemoteHandle(self, panel_id, name)
            raise

    def __contains__(self, name):
        return super().__contains__(name) or self._client.find(name) is not None

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


def connect(url, label="python", password=None, timeout=10.0):
    """Join a running canvas as a peer process: ``danvas.connect(url)``.

    Returns a connected :class:`RemoteCanvas` — the normal danvas API, with the
    frames going to the serving canvas instead of to browsers of this
    process's own.
    """
    return RemoteCanvas(url, label=label, password=password).connect(timeout)
