"""Custom: an arbitrary-HTML panel rendered in a sandboxed iframe.

The HTML may be passed directly or loaded from a file, and ``css``/``js`` may be
supplied as separate strings — they are composed into a single document under
the hood (a ``<style>`` block, your markup, then a ``<script>`` block), so a
snippet copied from a site like uiverse.io drops in without hand-assembling a
page::

    panel = canvas.custom(html=markup, css=styles, js=behaviour)

A small ``canvas`` helper is injected into the iframe with a symmetric two-way
channel:

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
    default_w = 380
    default_h = 320

    def __init__(self, html=None, path=None, css=None, js=None, name="custom",
                 label=None, w=None, h=None, event_key="event"):
        # ``w``/``h`` are optional overrides; when omitted the panel falls back
        # to ``default_w``/``default_h`` (set per subclass) via BaseComponent.
        size = {k: v for k, v in (("w", w), ("h", h)) if v is not None}
        super().__init__(name=name, label=label, **size)
        if path is not None:
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
        # The three pieces are kept separate so any one can be replaced later
        # (see update); they are composed into one document on the way out.
        self._html = html or ""
        self._css = css or ""
        self._js = js or ""
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
            "};"
            # Ctrl/Cmd+wheel inside the iframe would otherwise trigger the
            # *browser's* page zoom (tldraw can't preventDefault an event in a
            # sandboxed frame). Swallow it here and forward the delta + cursor to
            # the parent, which zooms the canvas at that point instead — so the
            # gesture matches scrolling over the bare canvas. Capture phase so we
            # win over any content (e.g. Plotly) wheel handler; plain wheel (no
            # modifier) is left alone so panels can still scroll their content.
            "window.addEventListener('wheel',function(e){"
            "if(e.ctrlKey||e.metaKey){e.preventDefault();"
            "parent.postMessage({__pycanvas_wheel:{x:e.clientX,y:e.clientY,d:e.deltaY}},'*');}"
            "},{passive:false,capture:true});"
            "</script>"
        )
        return helper + html

    @staticmethod
    def compose(html="", css="", js=""):
        """Assemble separate HTML/CSS/JS strings into one iframe document.

        Used internally whenever ``css`` or ``js`` is given, but also callable
        directly when you want the composed string itself. The wrapper adds a
        minimal reset and centers the content in the frame.
        """
        return (
            "<style>"
            "* { box-sizing: border-box; margin: 0; padding: 0;"
            " font-family: system-ui, sans-serif; }"
            "body { background: transparent; display: flex;"
            " justify-content: center; align-items: center;"
            " min-height: 100vh; overflow: hidden; }"
            f"{css}"
            "</style>"
            f"{html}"
            f"<script>{js}</script>"
        )

    def _document(self):
        """The full document for the iframe: composed only when css/js exist,
        so a complete page passed as ``html`` alone is left untouched."""
        if self._css or self._js:
            return self.compose(self._html, self._css, self._js)
        return self._html

    def register_props(self):
        props = dict(self._props)  # label, w, h
        props["html"] = self._wrap(self._document())
        return props

    def update(self, html=None, css=None, js=None):
        """Replace the panel's content (reloads the iframe).

        Each piece left as ``None`` keeps its current value, so e.g.
        ``panel.update(css=new_css)`` restyles without touching the markup.
        """
        if html is not None:
            self._html = html
        if css is not None:
            self._css = css
        if js is not None:
            self._js = js
        self._send_update({"html": self._wrap(self._document())})

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
