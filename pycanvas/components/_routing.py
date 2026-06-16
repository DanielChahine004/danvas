"""Shared inbound-message routing for panels that take ``canvas.send`` events.

Both :class:`~pycanvas.components.react.React` and
:class:`~pycanvas.components.custom.Custom` accept arbitrary browser messages and
fan them out to ``@panel.on(event)`` / ``@panel.on_message`` handlers, keyed by a
configurable field. That dispatch table is identical for both, so it lives here as
a mixin; React layers request/response routing (``on_request``) on top.
"""

import sys


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

    def on(self, event=None, *, fields=None):
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
        """
        def deco(fn):
            handler = self._with_fields(event, fn, fields) if fields else fn
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
                _warn(f"[pycanvas] dropped {event!r}: field {name!r}="
                      f"{out[name]!r} is not a valid {getattr(typ, '__name__', typ)}")
                return None
        return out

    def on_message(self, fn):
        """Decorator: handle *every* inbound message (a catch-all ``on()``)."""
        self._routes.setdefault(None, []).append(fn)
        return fn

    def _handle_input(self, payload, viewer=None):
        with self._lock:
            self._value = payload
        event = payload.get(self._event_key) if isinstance(payload, dict) else None
        handlers = list(self._routes.get(event, []))
        if event is not None:
            handlers += self._routes.get(None, [])
        self._dispatch_callbacks(handlers, (payload,), viewer)
