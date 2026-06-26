"""Shared inbound-message routing for panels that take ``canvas.send`` events.

Both :class:`~danvas.components.react.React` and
:class:`~danvas.components.custom.Custom` accept arbitrary browser messages and
fan them out to ``@panel.on(event)`` / ``@panel.on_message`` handlers, keyed by a
configurable field. That dispatch table is identical for both, so it lives here as
a mixin; React layers request/response routing (``on_request``) on top.
"""

import sys

from .base import _mark_dedicated, _mark_threaded


def _warn(msg):
    """Print a routing diagnostic to the kernel's real stderr.

    Mirrors ``bridge._diag``: handlers run on a background dispatch thread, so a
    plain ``print`` inside ipykernel can be misattributed to a finished cell.
    Writing to ``sys.__stderr__`` skips the per-cell redirection.
    """
    stream = sys.__stderr__
    if stream is None:
        return
    try:
        stream.write(msg + "\n")
        stream.flush()
    except (ValueError, OSError):
        pass


class _EventRouter:
    """Mixin: route inbound payloads to per-event handlers.

    The host calls :meth:`_init_routing` from its ``__init__`` (after
    ``BaseComponent.__init__`` has set ``_callbacks``/``_lock``/``_value`` and
    provides ``_dispatch_callbacks``). Handlers registered via ``on_change`` ride
    along as catch-alls.
    """

    def _init_routing(self, event_key="event"):
        # Inbound payloads are routed by payload[event_key]; the None slot holds
        # catch-all handlers (on_message / on() with no event), seeded with any
        # on_change callbacks already on the base component.
        self._event_key = event_key
        self._routes = {None: list(self._callbacks)}
        self._binary_handlers = []

    def on(self, event=None, *, fields=None, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: handle inbound ``canvas.send`` messages.

        ``@panel.on("tick")`` fires only for messages whose ``event`` field (see
        ``event_key``) equals ``"tick"``; ``@panel.on()`` is a catch-all. The
        handler gets the full payload dict (plus the sender's ``viewer`` if it
        declares a second parameter, like ``on_change``).

        ``fields`` is an optional ``{name: type}`` map that gives validation a
        home: each named field present in the payload is coerced through its
        callable before the handler runs (``fields={"qty": int, "price": float}``),
        so the handler receives real numbers instead of strings off the wire. A
        value that can't be coerced drops that message (the handler isn't called)
        and logs why — so a malformed field from the browser can't crash the
        handler with a ``ValueError``. Fields absent from the payload are left
        untouched (default them in the handler), and non-dict payloads bypass
        coercion::

            @panel.on("award", fields={"id": str, "points": int})
            def _(msg):                 # msg["points"] is an int here
                teams[msg["id"]]["points"] += msg["points"]

        See :meth:`on_change <danvas.components.base.BaseComponent.on_change>`
        for the full ``threaded`` / ``dedicated`` / ``queue`` semantics.
        ``threaded`` and ``dedicated`` are mutually exclusive.
        """
        if threaded and dedicated:
            raise ValueError("threaded and dedicated are mutually exclusive")
        def deco(fn):
            handler = self._with_fields(event, fn, fields) if fields else fn
            if dedicated:
                handler = _mark_dedicated(handler, queue)
            elif threaded:
                handler = _mark_threaded(handler)
            self._routes.setdefault(event, []).append(handler)
            return fn   # return the original so it stays usable/named by the caller
        return deco

    def _with_fields(self, event, fn, fields):
        """Wrap ``fn`` so the named payload fields are coerced before it runs.

        The wrapper takes ``(payload, viewer)`` so the dispatcher always hands it
        the viewer; it forwards to ``fn`` with or without the viewer to match
        ``fn``'s own arity (the same opt-in rule as ``on_change``). A coercion
        failure drops the message rather than calling ``fn``.
        """
        accepts_viewer = self._accepts_viewer(fn, 1)

        def handler(payload, viewer=None):
            coerced = self._coerce_fields(event, payload, fields)
            if coerced is None:
                return  # a field failed to coerce; message dropped (logged below)
            return fn(coerced, viewer) if accepts_viewer else fn(coerced)

        return handler

    @staticmethod
    def _coerce_fields(event, payload, fields):
        """Coerce ``payload``'s named fields through their types.

        Returns the coerced copy, or ``None`` if a present field can't be coerced
        (the caller drops the message). Non-dict payloads and absent fields pass
        through unchanged.
        """
        if not isinstance(payload, dict):
            return payload
        out = dict(payload)
        for name, typ in fields.items():
            if name not in out:
                continue
            try:
                out[name] = typ(out[name])
            except (TypeError, ValueError):
                _warn(f"[danvas] dropped {event!r}: field {name!r}="
                      f"{out[name]!r} is not a valid {getattr(typ, '__name__', typ)}")
                return None
        return out

    def on_message(self, fn=None, *, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: handle *every* inbound message (a catch-all ``on()``).

        See :meth:`on_change <danvas.components.base.BaseComponent.on_change>`
        for the full ``threaded`` / ``dedicated`` / ``queue`` semantics.
        ``threaded`` and ``dedicated`` are mutually exclusive.
        """
        if threaded and dedicated:
            raise ValueError("threaded and dedicated are mutually exclusive")
        def register(f):
            if dedicated:
                self._routes.setdefault(None, []).append(_mark_dedicated(f, queue))
            else:
                self._routes.setdefault(None, []).append(
                    _mark_threaded(f) if threaded else f)
            return f
        return register(fn) if fn is not None else register

    def on_binary(self, fn=None, *, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: handle raw binary data sent by ``canvas.sendBinary()``
        in a Custom or React panel.

        The handler receives ``data: bytes`` (and optionally ``viewer``)::

            @panel.on_binary
            def got_frame(data: bytes, viewer):
                frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                ...

        See :meth:`on_change <danvas.components.base.BaseComponent.on_change>`
        for the full ``threaded`` / ``dedicated`` / ``queue`` semantics.
        ``threaded`` and ``dedicated`` are mutually exclusive.
        """
        if threaded and dedicated:
            raise ValueError("threaded and dedicated are mutually exclusive")
        def register(f):
            if dedicated:
                self._binary_handlers.append(_mark_dedicated(f, queue))
            else:
                self._binary_handlers.append(_mark_threaded(f) if threaded else f)
            return f
        return register(fn) if fn is not None else register

    def _receive_binary(self, data: bytes, viewer=None):
        """Called by the bridge when an inbound binary frame arrives for this panel."""
        self._dispatch_callbacks(list(self._binary_handlers), (data,), viewer)

    def _handle_input(self, payload, viewer=None):
        with self._lock:
            self._value = payload
        event = payload.get(self._event_key) if isinstance(payload, dict) else None
        handlers = list(self._routes.get(event, []))
        if event is not None:
            handlers += self._routes.get(None, [])
        if not handlers:
            self._warn_unrouted(payload)   # nothing will fire — diagnose, once
        self._dispatch_callbacks(handlers, (payload,), viewer)

    def _warn_unrouted(self, payload):
        """Warn (once per panel) when an inbound message matched no handler at all.

        The silent-drop case: the panel has ``@on(event)`` handlers but the
        message routed to none of them and there's no catch-all ``on_message``,
        almost always because the routing field doesn't match — e.g. the JSX
        sends ``{action: ...}`` while the panel still routes on the default
        ``event_key="event"``. We have the real payload here, so when one of its
        *values* matches a registered event name we can name the exact fix; a
        panel with a catch-all never reaches this (its handler list isn't empty),
        and a handler-less panel stays quiet (no named routes to mismatch).
        """
        keyed = [k for k in self._routes if k is not None]
        if not keyed or getattr(self, "_warned_unrouted", False):
            return
        self._warned_unrouted = True
        routed = payload.get(self._event_key) if isinstance(payload, dict) else None
        hint = ""
        if isinstance(payload, dict):
            for k, v in payload.items():
                if k != self._event_key and v in keyed:
                    hint = (f" — payload[{k!r}]=={v!r} matches a handler; "
                            f"did you mean event_key={k!r}?")
                    break
        _warn(f"[danvas] {type(self).__name__} {getattr(self, 'name', None)!r}: an "
              f"inbound message matched no handler (event_key={self._event_key!r} "
              f"→ {routed!r}; registered events {sorted(keyed)}){hint}")