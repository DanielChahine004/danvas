"""Inspector: a live table of the canvas's components.

A spatial "variable explorer" -- it lists every panel on the canvas with its
name, type, current value and geometry. It reads state pycanvas already tracks,
so building the table is cheap and safe on the event-loop thread (no kernel).
Refresh from the panel's button or from Python via :meth:`refresh`.
"""

import json
import threading
import traceback

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

    def __init__(self, label="inspector", refresh=None):
        """``refresh`` is the auto-refresh period in seconds (``None`` = manual
        only, via the panel's button or :meth:`refresh`). With a period set, a
        daemon thread rebuilds the table on that cadence while the canvas is
        serving and at least one browser is connected."""
        super().__init__(label=label, rows="[]")
        self._canvas = None  # injected by Canvas.insert
        self._refresh_interval = refresh
        self._ticker = None
        self._ticker_stop = threading.Event()

    def register_props(self):
        self._props["rows"] = self._build()
        return dict(self._props)

    def refresh(self):
        """Rebuild the table from current component state and push it, live."""
        self._send_update({"rows": self._build()})

    # -- auto-refresh ticker (started/stopped via Canvas attach hooks) --------
    def _on_attached(self):
        """Start the ticker once the canvas reference is wired (if enabled)."""
        if self._refresh_interval and self._ticker is None:
            self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
            self._ticker.start()

    def _on_removed(self):
        """Stop the ticker when the panel is pulled off the canvas."""
        self._ticker_stop.set()

    def _tick_loop(self):
        # wait() returns True the moment _on_removed sets the event, so removal
        # ends the loop promptly instead of after a full interval.
        while not self._ticker_stop.wait(self._refresh_interval):
            canvas = self._canvas
            if canvas is None:
                continue
            # Skip work when nobody's watching: no server, or no open browser.
            if not getattr(canvas, "_serving", False):
                continue
            if not canvas._bridge._connections:
                continue
            try:
                self.refresh()
            except Exception:
                traceback.print_exc()

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
