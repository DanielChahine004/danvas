"""Custom: an arbitrary-HTML panel rendered in a sandboxed iframe.

The HTML may be passed directly or loaded from a file. A small ``canvas`` helper
is injected into the iframe with a symmetric two-way channel:

  * ``canvas.send(data)``   -> Python   (delivered to your handlers)
  * ``canvas.onPush(fn)``   <- Python   (``panel.push(data)`` calls ``fn(data)``)

On the Python side, register handlers with ``@panel.on("event")`` to route by an
``event`` field, or ``@panel.on_message`` to receive every message.
"""

import json
import traceback

from .base import BaseComponent


class Custom(BaseComponent):
    component = "Custom"

    def __init__(self, html=None, path=None, name="custom", label=None, width=380,
                 height=320, event_key="event"):
        super().__init__(name=name, label=label, w=width, h=height)
        if path is not None:
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
        self._html = html or ""
        # Inbound ``canvas.send`` payloads are routed by ``payload[event_key]``.
        # Override the key if your HTML tags messages with a different field.
        self._event_key = event_key
        # event value -> [handlers]; the ``None`` slot holds catch-all handlers
        # (``on_message`` and ``on()`` with no event) that see every message.
        self._routes = {None: list(self._callbacks)}

    def _wrap(self, html):
        """Prepend the ``canvas`` helper, tagged with this component's id.

        ``send`` posts back to the app (tagged with the id so the bridge knows
        which panel spoke). ``onPush`` is the receive side: it subscribes to the
        ``message`` events that :meth:`push` delivers and hands your callback the
        raw payload, so the iframe never has to unwrap ``__pycanvas`` itself.
        """
        # json.dumps keeps the id safely quoted inside the script literal.
        cid = json.dumps(self.id)
        helper = (
            "<script>window.canvas={"
            "send:function(data){"
            f"parent.postMessage({{__pycanvas:{cid},data:data}},'*');"
            "},"
            "onPush:function(fn){window.addEventListener('message',function(e){"
            "if(e.data&&e.data.__pycanvas!==undefined){fn(e.data.__pycanvas);}"
            "});}"
            "};</script>"
        )
        return helper + html

    def register_props(self):
        props = dict(self._props)  # label, w, h
        props["html"] = self._wrap(self._html)
        return props

    def update(self, html):
        """Replace the panel's HTML content (reloads the iframe)."""
        self._html = html
        self._send_update({"html": self._wrap(html)})

    def push(self, data):
        """Stream live data into the panel's iframe *without* reloading it.

        In the iframe, receive it with ``canvas.onPush(fn)`` — ``fn`` is called
        with ``data`` (any JSON-serializable value) for each push. Unlike
        :meth:`update`, this keeps the iframe — and its focus, listeners, and
        scroll position — intact, so it suits high-rate streaming (e.g. video
        frames) and live two-way panels.
        """
        self._send_update({"post": data})

    # -- input routing (browser -> Python) -----------------------------------
    def on(self, event=None):
        """Decorator: handle inbound ``canvas.send`` messages.

        ``@panel.on("rotate")`` fires only for messages whose ``event`` field (see
        ``event_key``) equals ``"rotate"``; ``@panel.on()`` with no event is a
        catch-all that sees every message. The handler is called with the full
        payload dict. This is the built-in dispatcher, so a widget no longer needs
        to subclass and reimplement its own routing.
        """
        def deco(fn):
            self._routes.setdefault(event, []).append(fn)
            return fn
        return deco

    def on_message(self, fn):
        """Decorator: handle *every* inbound message (a catch-all ``on()``)."""
        self._routes.setdefault(None, []).append(fn)
        return fn

    def _handle_input(self, payload):
        with self._lock:
            self._value = payload
        event = payload.get(self._event_key) if isinstance(payload, dict) else None
        # Keyed handlers for this event, then the catch-all handlers.
        handlers = list(self._routes.get(event, []))
        if event is not None:
            handlers += self._routes.get(None, [])
        for cb in handlers:
            try:
                cb(payload)
            except Exception:
                traceback.print_exc()
