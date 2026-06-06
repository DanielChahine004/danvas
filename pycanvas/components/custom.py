"""Custom: an arbitrary-HTML panel rendered in a sandboxed iframe.

The HTML may be passed directly or loaded from a file. A small ``canvas.send()``
helper is injected so the panel can emit data back to Python; register handlers
with ``@panel.on_message``.
"""

import json

from .base import BaseComponent


class Custom(BaseComponent):
    component = "Custom"

    def __init__(self, html=None, path=None, name="custom", label=None, width=380,
                 height=320):
        super().__init__(name=name, label=label, w=width, h=height)
        if path is not None:
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
        self._html = html or ""

    def _wrap(self, html):
        """Prepend the canvas.send() helper, tagged with this component's id."""
        # json.dumps keeps the id safely quoted inside the script literal.
        cid = json.dumps(self.id)
        helper = (
            "<script>window.canvas={send:function(data){"
            f"parent.postMessage({{__pycanvas:{cid},data:data}},'*');"
            "}};</script>"
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

        The iframe receives a ``message`` event whose ``data.__pycanvas`` is
        ``data`` (any JSON-serializable value). Unlike :meth:`update`, this keeps
        the iframe — and its focus, listeners, and scroll position — intact, so
        it suits high-rate streaming (e.g. video frames) and live two-way panels.
        Listen for it in your HTML::

            window.addEventListener('message', (e) => {
                if (e.data && e.data.__pycanvas !== undefined) {
                    handle(e.data.__pycanvas)
                }
            })
        """
        self._send_update({"post": data})

    # Custom panels deliver structured data, not a single ``value``.
    def on_message(self, fn):
        """Decorator: register a handler fired with the data the panel sends."""
        self._callbacks.append(fn)
        return fn

    def _handle_input(self, payload):
        with self._lock:
            self._value = payload
        for cb in self._callbacks:
            try:
                cb(payload)
            except Exception:
                import traceback

                traceback.print_exc()
