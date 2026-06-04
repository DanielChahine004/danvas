"""Inspector: a live table of the canvas's components.

A spatial "variable explorer" -- it lists every panel on the canvas with its
name, type, current value and geometry. It reads state pycanvas already tracks,
so building the table is cheap and safe on the event-loop thread (no kernel).
Refresh from the panel's button or from Python via :meth:`refresh`.
"""

import json

from .base import BaseComponent


def _short(value, limit=80):
    """A safe, length-capped repr for a cell in the table."""
    try:
        text = repr(value)
    except Exception as exc:  # a component's repr should never break the table
        text = f"<repr error: {exc!r}>"
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Inspector(BaseComponent):
    component = "Inspector"
    default_w = 520
    default_h = 320

    def __init__(self, label="inspector"):
        super().__init__(label=label, rows="[]")
        self._canvas = None  # injected by Canvas.insert

    def register_props(self):
        self._props["rows"] = self._build()
        return dict(self._props)

    def refresh(self):
        """Rebuild the table from current component state and push it, live."""
        self._send_update({"rows": self._build()})

    def _handle_input(self, payload):
        if payload.get("action") == "refresh":
            self.refresh()

    def _build(self):
        if self._canvas is None:
            return "[]"
        name_of = {id(c): n for n, c in self._canvas._named.items()}
        rows = []
        for c in self._canvas._components:
            if c is self:
                continue
            rows.append({
                "name": name_of.get(id(c), ""),
                "type": c.component,
                "value": _short(c.value),
                "x": c.x,
                "y": c.y,
                "w": c.w,
                "h": c.h,
                "locked": c.locked,
            })
        return json.dumps(rows)
