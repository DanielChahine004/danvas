"""Shared inbound-message routing for panels that take ``canvas.send`` events.

Both :class:`~pycanvas.components.react.React` and
:class:`~pycanvas.components.custom.Custom` accept arbitrary browser messages and
fan them out to ``@panel.on(event)`` / ``@panel.on_message`` handlers, keyed by a
configurable field. That dispatch table is identical for both, so it lives here as
a mixin; React layers request/response routing (``on_request``) on top.
"""


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

    def on(self, event=None):
        """Decorator: handle inbound ``canvas.send`` messages.

        ``@panel.on("tick")`` fires only for messages whose ``event`` field (see
        ``event_key``) equals ``"tick"``; ``@panel.on()`` is a catch-all. The
        handler gets the full payload dict.
        """
        def deco(fn):
            self._routes.setdefault(event, []).append(fn)
            return fn
        return deco

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
