"""Base class shared by all PyCanvas components."""

import functools
import inspect
import math
import threading
import traceback
import warnings

from .._flags import LAYOUT_FLAGS
from ..kernel import DedicatedKernel, spawn


def _mark_threaded(fn):
    """Tag a callback so the dispatcher runs it on its own thread (``spawn``).

    Sets a marker attribute on the callable. Bound methods and builtins can't
    take attributes, so those fall back to a thin ``functools.wraps`` wrapper
    (which preserves the signature, so the viewer-arity detection still works).
    """
    try:
        fn._pc_threaded = True
        return fn
    except (AttributeError, TypeError):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        wrapper._pc_threaded = True
        return wrapper


_HANDLER_QUEUES = ("fifo", "latest")


def _mark_dedicated(fn, queue_mode="fifo"):
    """Tag a callback for a :class:`~pycanvas.kernel.DedicatedKernel`.

    Like :func:`_mark_threaded` but for handlers that should run on a
    persistent per-handler thread rather than a freshly spawned one per call.
    The kernel is created lazily on first dispatch and lives for the app's
    lifetime. ``queue_mode`` sets the backpressure policy on that thread's
    own queue (``"fifo"`` or ``"latest"`` — see :class:`DedicatedKernel`).
    """
    if queue_mode not in _HANDLER_QUEUES:
        raise ValueError(
            f"queue must be one of {_HANDLER_QUEUES}, got {queue_mode!r}"
        )
    try:
        fn._pc_dedicated = True
        fn._pc_handler_queue = queue_mode
        return fn
    except (AttributeError, TypeError):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        wrapper._pc_dedicated = True
        wrapper._pc_handler_queue = queue_mode
        return wrapper


class BaseComponent:
    """A bidirectional canvas component.

    Subclasses set ``component`` (the type string sent to the browser) and
    implement :meth:`update`. State (``_value``) is written by the asyncio
    WebSocket handler thread and read from user threads, so access is guarded
    by a per-component lock.
    """

    component = "Base"

    # Default panel size in pixels, mirroring the frontend's getDefaultProps so
    # ``comp.w``/``comp.h`` always read a real number (and can be incremented)
    # even when size isn't given to ``insert``. Override per component.
    default_w = 240
    default_h = 96

    # Send-queue policy under backpressure (a slow/late browser). Any component
    # may pass ``queue=`` to choose how its own updates behave when they outpace
    # the connection:
    #   "fifo"   -> every update is delivered in order, nothing dropped (default;
    #               right for controls/labels where each value matters).
    #   "latest" -> keep only the newest pending value per viewer, dropping stale
    #               ones (right for live media/telemetry; VideoFeed's default).
    # Dict updates under "latest" merge newest-per-key so partial updates (e.g.
    # set_layout) aren't lost; binary frames replace wholesale.
    _QUEUE_POLICIES = ("fifo", "latest")

    def __init__(self, name=None, label=None, queue="fifo", **props):
        if queue not in self._QUEUE_POLICIES:
            raise ValueError(
                f"queue must be one of {self._QUEUE_POLICIES}, got {queue!r}"
            )
        self._queue = queue
        self.id = None
        # ``name`` is the unique identity / ``canvas.<name>`` handle (Canvas.insert
        # may still override it). ``label`` is only the caption shown on the panel
        # and defaults to the name, so naming a component is enough to caption it.
        self.name = name
        self._props = dict(props)
        caption = label if label is not None else name
        if caption is not None:
            self._props["label"] = caption
        # Ensure size is always present so w/h read/increment without surprises.
        self._props.setdefault("w", self.default_w)
        self._props.setdefault("h", self.default_h)
        self._value = None
        self._callbacks = []
        self._layout_callbacks = []
        # Dedicated-handler kernels: id(callback) -> DedicatedKernel. Created
        # lazily on first dispatch so no thread is started for a handler that
        # never fires. Keyed by id(cb) because the callback objects are the
        # stable identity. Only written/read from the dispatch thread.
        self._dedicated_kernels = {}
        # Role-based access: _roles limits which viewer roles see this panel;
        # empty means all roles. _lock_for makes the panel non-interactive for
        # the listed roles on connect (operable=False sent per-client).
        self._roles = []
        self._lock_for = []
        self._bridge = None
        self._lock = threading.Lock()
        # Optional canvas placement (x, y) in canvas coordinates; None = let the
        # frontend auto-cascade. Set by Canvas.insert. Width/height are passed
        # through register_props instead (they are real shape props).
        self._position = None
        # Panels placed below= or right_of= this one; populated by Canvas.insert
        # so height/y changes cascade through the layout chain automatically.
        self._below_deps = []
        self._right_of_deps = []
        # Rotation in degrees (clockwise). Defaults to 0 (unrotated) so it can be
        # read and incremented. Like position, it is a top-level shape field.
        self._rotation = 0
        # Opacity: 0.0 = fully transparent, 1.0 = fully opaque. tldraw top-level
        # field (same tier as x/y/rotation). Omitted from messages at the default
        # so existing wire protocol is unchanged.
        self._opacity = 1.0
        # Lock / chrome flags, all defaulted from the single table in _flags.py:
        # ``locked`` (full lock, top-level tldraw isLocked); ``draggable`` /
        # ``resizable`` / ``operable`` / ``grabbable`` (interaction-preserving
        # locks carried in the shape's tldraw ``meta``); ``frame`` (the card
        # chrome). See pycanvas/_flags.py for the per-flag semantics, the wire
        # keys, and the property docstrings generated at the bottom of this file.
        for _flag in LAYOUT_FLAGS.values():
            setattr(self, _flag.attr, _flag.default)
        # Per-viewer layout overlays (the layout twin of React's prop overlays):
        # role / client id -> {x, y, w, h, rotation, <flag name>}. Merged onto the
        # base geometry for that viewer (precedence shared < role < client), so
        # ``set_layout(roles=...)`` and a user's drag-to-their-own-layer persist
        # and replay on reconnect.
        self._role_layout = {}
        self._client_layout = {}

    # -- wiring (called by Canvas.insert) ------------------------------------
    def _bind(self, component_id, bridge):
        self.id = component_id
        self._bridge = bridge

    # -- read ----------------------------------------------------------------
    @property
    def value(self):
        with self._lock:
            return self._value

    @property
    def queue(self):
        """This component's send-queue policy (``"fifo"`` or ``"latest"``).

        Settable on any component so its backpressure behaviour can be chosen
        without a constructor argument, e.g. ``plot.queue = "latest"`` to drop
        stale telemetry for slow viewers. See the class docstring for semantics.
        """
        return self._queue

    @queue.setter
    def queue(self, policy):
        if policy not in self._QUEUE_POLICIES:
            raise ValueError(
                f"queue must be one of {self._QUEUE_POLICIES}, got {policy!r}"
            )
        self._queue = policy

    # -- role-based visibility -----------------------------------------------
    @property
    def roles(self):
        """The viewer roles allowed to see this panel (``[]`` means all roles).

        This is the live form of the ``roles=`` argument on the factory
        (``canvas.react(..., roles=[...])``). Read-only; use :meth:`add_role` /
        :meth:`remove_role` to change it so connected viewers update too.
        """
        return list(self._roles)

    def add_role(self, *roles):
        """Allow these viewer roles to see the panel, live.

        Appends to the role allowlist; roles already present are ignored. A
        viewer currently connected under a newly added role is sent the panel
        immediately, and later connections get it via the normal replay — so a
        panel can be revealed to a role created after the server started (e.g. a
        team whose password the admin just set). Returns ``self``.
        """
        added = [r for r in roles if r not in self._roles]
        self._roles.extend(added)
        if added and self._bridge is not None:
            self._bridge.register_live(self, only_roles=set(added))
        return self

    def remove_role(self, *roles):
        """Disallow these viewer roles from seeing the panel, live.

        Removes them from the allowlist and tells any viewer currently connected
        under a removed role to drop the panel. Roles not present are ignored.
        Note that emptying the allowlist entirely means "visible to all roles",
        so removing the last role *shows* the panel to everyone rather than
        hiding it — keep at least one role (or re-add ``roles=``) to stay
        restricted. Returns ``self``.
        """
        removed = [r for r in roles if r in self._roles]
        for r in removed:
            self._roles.remove(r)
        # Only drop it live while the panel is still role-restricted; if the
        # allowlist is now empty the panel is visible to everyone, so those
        # viewers should keep it.
        if removed and self._roles and self._bridge is not None:
            for r in removed:
                self._bridge.send_to_role(r, {"type": "remove", "id": self.id})
        return self

    # -- layout (read public state; writes move/resize live) -----------------
    @property
    def x(self):
        return self._position[0] if self._position else None

    @x.setter
    def x(self, value):
        self.set_layout(x=value)

    @property
    def y(self):
        return self._position[1] if self._position else None

    @y.setter
    def y(self, value):
        self.set_layout(y=value)

    @property
    def w(self):
        return self._props.get("w")

    @w.setter
    def w(self, value):
        # ``comp.w = "auto"`` is the live form of ``w="auto"`` at insert: fit the
        # width to the content's natural width rather than shipping the literal
        # string to the frontend. Only Custom-based panels can measure their
        # content, so the base implementation explains why it can't.
        if value == "auto":
            self._set_auto_w()
        else:
            self._set_fixed_w(value)

    def _set_auto_w(self):
        """Turn on content-fit width (``w="auto"``).

        Overridden by :class:`~pycanvas.Custom`, the only panel whose content is
        measurable in the browser. The base panel can't fit its width, so this
        warns and leaves the width as-is.
        """
        warnings.warn(
            f"w='auto' is only supported on Custom-based panels; "
            f"{type(self).__name__} keeps its current width", stacklevel=3,
        )

    def _set_fixed_w(self, value):
        """Set an explicit pixel width. Custom overrides this to also leave
        auto-width mode, so a numeric assignment after ``w="auto"`` sticks."""
        self.set_layout(w=value)

    @property
    def h(self):
        return self._props.get("h")

    @h.setter
    def h(self, value):
        # ``comp.h = "auto"`` is the live form of ``h="auto"`` at insert: fit the
        # height to the rendered content rather than shipping the literal string
        # to the frontend (which expects a number). Only Custom-based panels can
        # measure their content, so the base implementation just explains why it
        # can't and leaves the height unchanged.
        if value == "auto":
            self._set_auto_h()
        else:
            self._set_fixed_h(value)

    def _set_auto_h(self):
        """Turn on content-fit height (``h="auto"``).

        Overridden by :class:`~pycanvas.Custom` (and its subclasses — markdown,
        table, image, …), the only panels whose content is measurable in the
        browser. The base panel has a fixed height it can't fit, so this warns
        and leaves the height as-is rather than failing or silently breaking.
        """
        warnings.warn(
            f"h='auto' is only supported on Custom-based panels (custom, "
            f"markdown, table, image, …); {type(self).__name__} keeps its "
            f"current height", stacklevel=3,
        )

    def _set_fixed_h(self, value):
        """Set an explicit pixel height. Custom-based panels override this to
        also leave auto-height mode, so a numeric assignment after ``h="auto"``
        sticks instead of being overridden by the iframe's fit loop."""
        self.set_layout(h=value)

    @property
    def rotation(self):
        """Rotation in degrees (clockwise); 0 if unrotated."""
        return self._rotation

    @rotation.setter
    def rotation(self, value):
        self.set_layout(rotation=value)

    @property
    def opacity(self):
        """Panel opacity: 0.0 = fully transparent, 1.0 = fully opaque."""
        return self._opacity

    @opacity.setter
    def opacity(self, value):
        self.set_layout(opacity=value)

    # The lock/chrome flag properties (``locked``, ``draggable``, ``resizable``,
    # ``operable``, ``grabbable``, ``frame``) are generated from LAYOUT_FLAGS at
    # the bottom of this module — one read-back property + a setter that routes
    # through set_layout for each. See pycanvas/_flags.py.

    # -- registration / initial sync ----------------------------------------
    def register_props(self):
        """Props sent in the ``register`` message to build the shape."""
        return dict(self._props)

    def register_props_for(self, role=None, client_id=None):
        """Register props for one connecting viewer.

        The base ignores ``role``/``client_id`` and returns the shared props;
        components with per-viewer prop overlays (e.g. :meth:`React.update` with
        ``roles=``) override this so a reconnecting viewer replays the slice it
        should see (precedence shared < role < client, mirroring ``set_view``).
        """
        return self.register_props()

    def state_payload(self):
        """Current state pushed right after register (None = nothing)."""
        return None

    # -- write (Python -> browser) -------------------------------------------
    def update(self, *args, **kwargs):  # pragma: no cover - overridden
        raise NotImplementedError

    def _send_update(self, payload):
        if self._bridge is None:
            return
        msg = {"type": "update", "id": self.id, "payload": payload}
        if self._queue == "latest":
            # Drop stale pending updates; merge newest-per-key (see bridge).
            self._bridge.broadcast_conflated(self.id, msg=msg)
        else:
            self._bridge.broadcast(msg)

    def _send_update_to(self, payload, role=None, client_id=None):
        """Send an update to specific viewers only, leaving broadcast state alone.

        Routes by login ``role`` (a name or list, matching ``serve(passwords=)``)
        and/or ``client_id`` (an id from ``canvas.viewers``). Backs the
        per-recipient writes (e.g. :meth:`React.update_for`); a no-op before the
        server is running, like the other sends.
        """
        if self._bridge is None:
            return
        msg = {"type": "update", "id": self.id, "payload": payload}
        if client_id is not None:
            self._bridge.send_to_client(client_id, msg)
        if role is not None:
            for r in ([role] if isinstance(role, str) else role):
                self._bridge.send_to_role(r, msg)

    def _send_binary(self, type_code, payload):
        """Push raw bytes to the browser as a binary frame, keyed by this id.

        For high-rate media (e.g. video frames): the payload skips base64/JSON
        and is fed straight into a Blob/ArrayBuffer on the frontend. ``payload``
        must be ``bytes``; ``type_code`` selects the frontend handler. Under the
        ``latest`` queue policy a stale pending frame is dropped in favour of the
        newest, so a fast feed can't back up a slow viewer.
        """
        if self._bridge is None:
            return
        from ..bridge import encode_binary_frame
        frame = encode_binary_frame(type_code, self.id, payload)
        if self._queue == "latest":
            self._bridge.broadcast_conflated(self.id, data=frame)
        else:
            self._bridge.broadcast_binary(frame)

    # -- live layout (Python -> browser) -------------------------------------
    def move(self, x, y):
        """Move this panel to ``(x, y)`` in canvas coordinates, live."""
        self.set_layout(x=x, y=y)

    def resize(self, w=None, h=None):
        """Resize this panel, live. Either dimension may be omitted."""
        self.set_layout(w=w, h=h)

    def rotate(self, degrees):
        """Rotate this panel to ``degrees`` (clockwise), live."""
        self.set_layout(rotation=degrees)

    def lock(self):
        """Fully lock the panel (no move, resize, or interaction), live."""
        self.set_layout(locked=True)

    def unlock(self):
        """Release a full lock so the panel responds normally again, live."""
        self.set_layout(locked=False)

    def pin(self):
        """Pin in place and fix size, but keep controls interactive, live.

        Shorthand for ``set_layout(draggable=False, resizable=False)`` — unlike
        :meth:`lock`, sliders and buttons on the panel still work.
        """
        self.set_layout(draggable=False, resizable=False)

    def unpin(self):
        """Allow dragging and resizing again, live."""
        self.set_layout(draggable=True, resizable=True)

    # -- stacking order (z-index) --------------------------------------------
    def to_front(self):
        """Raise this panel above every other panel, live.

        Persists across reload: a reconnecting client rebuilds the panel on top.
        """
        self._send_order("front")

    def to_back(self):
        """Lower this panel beneath every other panel, live.

        Persists across reload: a reconnecting client rebuilds the panel at the
        bottom of the stack.
        """
        self._send_order("back")

    def forward(self):
        """Raise this panel one step up the stack, live.

        A single overlap-aware nudge (tldraw semantics); not persisted across a
        reload — use :meth:`to_front` for a durable change.
        """
        self._send_order("forward")

    def backward(self):
        """Lower this panel one step down the stack, live.

        A single overlap-aware nudge (tldraw semantics); not persisted across a
        reload — use :meth:`to_back` for a durable change.
        """
        self._send_order("backward")

    def _send_order(self, op):
        if self._bridge is None:
            return
        self._bridge.reorder_component(self.id, op)

    def set_layout(self, x=None, y=None, w=None, h=None, rotation=None,
                   opacity=None,
                   locked=None, draggable=None, resizable=None, operable=None,
                   grabbable=None, frame=None, *, roles=None, client_id=None):
        """Update position, size, rotation and/or lock state in one live message.

        Any argument left as ``None`` is unchanged. ``x``/``y`` are the canvas
        position, ``rotation`` (degrees) the angle, ``w``/``h`` the size.
        ``locked`` is a full lock (blocks interaction *and* programmatic updates);
        ``draggable``/``resizable``/``operable``/``grabbable`` are
        interaction-preserving locks carried in the shape's tldraw ``meta``
        (``operable=False`` makes controls inert to the user while value updates
        keep rendering); ``frame`` toggles the card chrome.

        Scope it to specific viewers with ``roles=`` and/or ``client_id=`` (just
        like :meth:`React.update`): the change is stored as a per-viewer layout
        *overlay* on the shared geometry (precedence shared < role < client) and
        pushed to just those viewers — it persists and replays on reconnect, so a
        role can have its own placement/size. Omit both to set the shared layout
        for everyone. A user dragging/resizing a panel writes back to whichever
        layer their layout currently comes from (their client/role overlay if any,
        else the shared base), so hand-arranged layouts stick. Returns ``self``.
        """
        fields = {}
        for key, val in (("x", x), ("y", y), ("w", w), ("h", h),
                         ("rotation", rotation), ("opacity", opacity),
                         ("locked", locked), ("draggable", draggable),
                         ("resizable", resizable), ("operable", operable),
                         ("grabbable", grabbable), ("frame", frame)):
            if val is not None:
                fields[key] = val
        if not fields:
            return self
        payload = self._layout_payload(fields)
        if roles is None and client_id is None:
            self._store_base_layout(fields)
            if payload:
                self._send_update(payload)
            return self
        if roles is not None:
            for r in ([roles] if isinstance(roles, str) else roles):
                self._role_layout.setdefault(r, {}).update(fields)
                self._send_update_to(payload, role=r)
        if client_id is not None:
            self._client_layout.setdefault(client_id, {}).update(fields)
            self._send_update_to(payload, client_id=client_id)
        return self

    def _layout_payload(self, fields):
        """Normalised layout ``fields`` -> the wire ``update`` payload (rotation
        to radians for tldraw, flag names to their wire keys)."""
        payload = {}
        for key in ("x", "y", "w", "h"):
            if key in fields:
                payload[key] = fields[key]
        if "rotation" in fields:
            payload["rotation"] = math.radians(fields["rotation"])
        if "opacity" in fields:
            payload["opacity"] = float(fields["opacity"])
        for name in LAYOUT_FLAGS:
            if name in fields:
                payload[LAYOUT_FLAGS[name].wire] = bool(fields[name])
        return payload

    def _store_base_layout(self, fields):
        """Write normalised layout ``fields`` into the shared base state, so they
        replay for every viewer (the position fill-in matches the old inline
        behaviour: a lone x or y keeps the other coordinate)."""
        if "x" in fields or "y" in fields:
            prev_x, prev_y = self._position or (None, None)
            new_x = fields.get("x", prev_x)
            new_y = fields.get("y", prev_y)
            if new_x is not None and new_y is not None:
                self._position = (new_x, new_y)
        if "w" in fields:
            self._props["w"] = fields["w"]
        if "h" in fields:
            self._props["h"] = fields["h"]
        if "rotation" in fields:
            self._rotation = fields["rotation"]
        if "opacity" in fields:
            self._opacity = float(fields["opacity"])
        for name in LAYOUT_FLAGS:
            if name in fields:
                setattr(self, LAYOUT_FLAGS[name].attr, bool(fields[name]))

    def _has_viewer_overlays(self):
        """True when this panel has any per-role/per-client override, so its
        register frame must be built per viewer rather than shared once (see
        ``Bridge.register_live``). Subclasses with prop overlays extend this."""
        return bool(self._role_layout or self._client_layout)

    def _layout_overlay_for(self, role=None, client_id=None):
        """The per-viewer layout override (role then client merged), or ``{}``.
        Used by the bridge to replay a viewer's own placement/size on connect."""
        overlay = {}
        if role is not None:
            for r in ([role] if isinstance(role, str) else role):
                overlay.update(self._role_layout.get(r, {}))
        if client_id is not None:
            overlay.update(self._client_layout.get(client_id, {}))
        return overlay

    # -- layout read-back (browser -> Python) --------------------------------
    def on_layout(self, fn):
        """Decorator: callback fired when the user moves/resizes this panel.

        Called with the component after its stored geometry has been updated
        from the browser. Use it to react to (or persist) hand-arranged layouts.
        """
        self._layout_callbacks.append(fn)
        return fn

    def _apply_remote_layout(self, msg, viewer=None):
        """Update stored geometry from a user drag/resize in the browser.

        Writes back to the layer this viewer's layout currently comes from — their
        per-client overlay, else their per-role overlay, else the shared base — so
        a hand-arranged layout sticks, and in a role-based canvas a drag rearranges
        only the dragger's role rather than everyone's. Does not broadcast (the
        change already happened in that browser). ``rotation`` arrives in radians
        (tldraw) and is stored as degrees, matching the rest of the Python API.
        ``on_layout`` handlers fire with the component, plus the mover's ``viewer``
        when they declare a second parameter.
        """
        fields = {}
        x = msg.get("x")
        y = msg.get("y")
        if x is not None and y is not None:
            fields["x"], fields["y"] = x, y
        if msg.get("w") is not None:
            fields["w"] = msg["w"]
        if msg.get("h") is not None:
            fields["h"] = msg["h"]
        if msg.get("rotation") is not None:
            fields["rotation"] = math.degrees(msg["rotation"])
        viewer = viewer or {}
        role = viewer.get("role")
        cid = viewer.get("id")
        if cid is not None and cid in self._client_layout:
            self._client_layout[cid].update(fields)
        elif role is not None and role in self._role_layout:
            self._role_layout[role].update(fields)
        else:
            self._store_base_layout(fields)
        self._dispatch_callbacks(self._layout_callbacks, (self,), viewer)

    # -- input (browser -> Python) -------------------------------------------
    def on_change(self, fn=None, *, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: register a callback fired on input from the browser.

        **Default** — runs on a shared dispatch thread. Fast handlers (state
        updates, canvas calls) belong here; a slow one delays the handlers
        queued behind it.

        **``threaded=True``** — spawns a new daemon thread *per call*. Keeps
        the shared dispatch thread free for other handlers. Right for
        occasional slow work (an HTTP call, a ``time.sleep``). The handler
        may run concurrently with itself if calls arrive faster than it
        finishes, so guard any shared state you write.

        **``dedicated=True``** — launches one persistent daemon thread for
        *this handler only*, started on its first invocation. All calls are
        routed to that thread's own queue, so the handler is always serialised
        (no concurrent self-calls) and the shared dispatch thread is never
        blocked. Right for handlers that fire rapidly and do non-trivial work::

            @speed.on_change(dedicated=True, queue="latest")
            def _(v):
                result = heavy_compute(v)   # own thread; only the latest drag fires
                status.update(result)

        ``queue`` controls backpressure on the dedicated thread's queue:

        - ``"fifo"`` (default) — every call is queued and run in order.
        - ``"latest"`` — only the most recent *pending* call is kept; the
          thread runs to completion first, then picks up only the latest one,
          dropping any that piled up in between.

        ``threaded`` and ``dedicated`` are mutually exclusive.
        """
        if threaded and dedicated:
            raise ValueError("threaded and dedicated are mutually exclusive")
        def register(f):
            if dedicated:
                self._callbacks.append(_mark_dedicated(f, queue))
            elif threaded:
                self._callbacks.append(_mark_threaded(f))
            else:
                self._callbacks.append(f)
            return f
        return register(fn) if fn is not None else register

    @staticmethod
    def _accepts_viewer(fn, n_call_args):
        """Whether ``fn`` has room for a trailing ``viewer`` beyond ``n_call_args``.

        True when the callable declares more explicit positional parameters than
        the fixed args we pass — the signal that a handler opted in to receiving
        the viewer dict. Unintrospectable callables (some builtins/C funcs) report
        False, so they're called without it. Shared by the fire-and-forget
        callback path and the single-answer request path so both detect arity the
        same way.
        """
        try:
            params = [
                p for p in inspect.signature(fn).parameters.values()
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                              inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            return len(params) > n_call_args
        except (ValueError, TypeError):
            return False

    def _dispatch_callbacks(self, callbacks, call_args, viewer):
        """Call each callback, appending viewer as a final arg when its signature accepts it.

        Detects whether the callback has more explicit positional parameters
        than ``call_args`` supplies — if so, passes ``viewer`` (a dict with
        ``id``, ``name``, ``color``, ``role``) as the extra argument. Backwards
        compatible: existing one-arg handlers are never changed.

        Three dispatch paths, chosen by marker attributes set at registration:

        - ``_pc_dedicated`` — routes to a per-handler :class:`DedicatedKernel`
          (created lazily here on first dispatch, keyed by ``id(cb)``). The
          kernel's own queue serialises calls to this handler without blocking
          the shared dispatch thread.
        - ``_pc_threaded`` — spawns a fresh daemon thread per call via
          :func:`spawn`. Keeps the dispatch thread free; may run concurrently.
        - *(default)* — called inline on the shared dispatch thread; FIFO and
          blocking (a slow handler stalls the queue behind it).
        """
        for cb in callbacks:
            args = (*call_args, viewer or {}) \
                if self._accepts_viewer(cb, len(call_args)) else call_args
            if getattr(cb, "_pc_dedicated", False):
                k = self._dedicated_kernels.get(id(cb))
                if k is None:
                    mode = getattr(cb, "_pc_handler_queue", "fifo")
                    k = DedicatedKernel(mode=mode)
                    self._dedicated_kernels[id(cb)] = k
                k.submit(lambda c=cb, a=args: c(*a))
            elif getattr(cb, "_pc_threaded", False):
                # Run on its own daemon thread so a slow handler doesn't hold up
                # the rest; spawn() logs any exception (default-arg capture so
                # the loop variable isn't shared across iterations).
                spawn(lambda c=cb, a=args: c(*a), name=f"pc-handler-{self.name}")
            else:
                try:
                    cb(*args)
                except Exception:
                    traceback.print_exc()

    def _handle_input(self, payload, viewer=None):
        if "value" in payload:
            with self._lock:
                self._value = payload["value"]
        self._dispatch_callbacks(self._callbacks, (self.value,), viewer)


def _flag_property(name, flag):
    """Build the read/write property for one lock/chrome flag.

    The getter returns the backing attribute; the setter routes through
    :meth:`BaseComponent.set_layout` so the change is broadcast live and the
    stored state stays consistent.
    """
    def getter(self):
        return getattr(self, flag.attr)

    def setter(self, value):
        self.set_layout(**{name: bool(value)})

    return property(getter, setter, doc=flag.doc)


# Attach the flag properties declared in _flags.py onto BaseComponent. Defining
# them here (rather than by hand) keeps the wire key, default, and docstring in
# one table — adding a flag is a single entry there.
for _name, _flag in LAYOUT_FLAGS.items():
    setattr(BaseComponent, _name, _flag_property(_name, _flag))
