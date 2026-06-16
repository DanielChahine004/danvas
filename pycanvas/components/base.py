"""Base class shared by all PyCanvas components."""

import inspect
import math
import threading
import traceback
import warnings

from .._flags import LAYOUT_FLAGS


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
        # Rotation in degrees (clockwise). Defaults to 0 (unrotated) so it can be
        # read and incremented. Like position, it is a top-level shape field.
        self._rotation = 0
        # Lock / chrome flags, all defaulted from the single table in _flags.py:
        # ``locked`` (full lock, top-level tldraw isLocked); ``draggable`` /
        # ``resizable`` / ``operable`` / ``grabbable`` (interaction-preserving
        # locks carried in the shape's tldraw ``meta``); ``frame`` (the card
        # chrome). See pycanvas/_flags.py for the per-flag semantics, the wire
        # keys, and the property docstrings generated at the bottom of this file.
        for _flag in LAYOUT_FLAGS.values():
            setattr(self, _flag.attr, _flag.default)

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

    # The lock/chrome flag properties (``locked``, ``draggable``, ``resizable``,
    # ``operable``, ``grabbable``, ``frame``) are generated from LAYOUT_FLAGS at
    # the bottom of this module — one read-back property + a setter that routes
    # through set_layout for each. See pycanvas/_flags.py.

    # -- registration / initial sync ----------------------------------------
    def register_props(self):
        """Props sent in the ``register`` message to build the shape."""
        return dict(self._props)

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
                   locked=None, draggable=None, resizable=None, operable=None,
                   grabbable=None, frame=None):
        """Update position, size, rotation and/or lock state in one live message.

        Any argument left as ``None`` is unchanged. Stored state is updated so a
        reconnecting client replays the new layout. ``x``/``y`` travel as the
        panel's canvas position, ``rotation`` (degrees) as its angle. ``locked``
        is a full lock (also blocks interaction *and* programmatic updates);
        ``draggable``/``resizable``/``operable``/``grabbable`` are
        interaction-preserving locks carried in the shape's tldraw ``meta``
        (``operable=False`` makes controls inert to the user while value updates
        keep rendering). ``w``/``h`` are shape props.
        """
        payload = {}
        if x is not None:
            payload["x"] = x
        if y is not None:
            payload["y"] = y
        if x is not None or y is not None:
            prev_x, prev_y = self._position or (None, None)
            new_x = x if x is not None else prev_x
            new_y = y if y is not None else prev_y
            if new_x is not None and new_y is not None:
                self._position = (new_x, new_y)
        if w is not None:
            self._props["w"] = w
            payload["w"] = w
        if h is not None:
            self._props["h"] = h
            payload["h"] = h
        if rotation is not None:
            self._rotation = rotation
            payload["rotation"] = math.radians(rotation)  # tldraw uses radians
        # The boolean lock/chrome flags are uniform: store the attribute and put
        # the wire key on the payload. Driven by LAYOUT_FLAGS so a new flag needs
        # only a table entry plus its keyword above.
        flag_values = {
            "locked": locked, "draggable": draggable, "resizable": resizable,
            "operable": operable, "grabbable": grabbable, "frame": frame,
        }
        for name, value in flag_values.items():
            if value is None:
                continue
            flag = LAYOUT_FLAGS[name]
            setattr(self, flag.attr, bool(value))
            payload[flag.wire] = bool(value)
        if payload:
            self._send_update(payload)

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

        Does not broadcast back (the change originated there). ``rotation``
        arrives in radians (tldraw) and is stored as degrees, matching the rest
        of the Python API. ``on_layout`` handlers fire with the component, plus
        the mover's ``viewer`` dict when they declare a second parameter.
        """
        x = msg.get("x")
        y = msg.get("y")
        if x is not None and y is not None:
            self._position = (x, y)
        if msg.get("w") is not None:
            self._props["w"] = msg["w"]
        if msg.get("h") is not None:
            self._props["h"] = msg["h"]
        if msg.get("rotation") is not None:
            self._rotation = math.degrees(msg["rotation"])
        self._dispatch_callbacks(self._layout_callbacks, (self,), viewer)

    # -- input (browser -> Python) -------------------------------------------
    def on_change(self, fn):
        """Decorator: register a callback fired on input from the browser."""
        self._callbacks.append(fn)
        return fn

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
        """
        for cb in callbacks:
            try:
                if self._accepts_viewer(cb, len(call_args)):
                    cb(*call_args, viewer or {})
                else:
                    cb(*call_args)
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
