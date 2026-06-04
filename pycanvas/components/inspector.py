"""Inspector: a live table of the canvas's components or the kernel namespace.

A spatial "variable explorer" with two sources:

- ``source="components"`` (default) lists every panel on the canvas with its
  name, type, current value and geometry. Reads state pycanvas already tracks,
  so building the table is cheap and safe on the event-loop thread (no kernel).
- ``source="globals"`` lists the variables in the shared REPL namespace (the one
  from :meth:`Canvas.enable_repl`), name/type/value -- a notebook-style variable
  explorer, skipping modules and private/dunder names.

The panel has a name-search box and a type filter (both client-side). Refresh
from the panel's button, from Python via :meth:`refresh`, or automatically with
``refresh=<seconds>``.
"""

import json
import threading
import traceback
import types

from .base import BaseComponent

# Column sets sent to the frontend per source; the table renders exactly these.
_COMPONENT_COLS = ["name", "type", "value", "x", "y", "w", "h"]
_GLOBALS_COLS = ["name", "type", "value"]


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

    def __init__(self, label="inspector", refresh=None, source="components",
                 namespace=None):
        """``source`` is ``"components"`` (canvas panels) or ``"globals"`` (the
        shared REPL namespace). ``namespace`` overrides the namespace used by
        ``"globals"`` mode (defaults to the one from :meth:`Canvas.enable_repl`,
        injected on insert). ``refresh`` is the auto-refresh period in seconds
        (``None`` = manual only); with a period set, a daemon thread rebuilds the
        table on that cadence while the canvas is serving and a browser is
        connected."""
        if source not in ("components", "globals"):
            raise ValueError("source must be 'components' or 'globals'")
        cols = _GLOBALS_COLS if source == "globals" else _COMPONENT_COLS
        super().__init__(label=label, rows="[]", cols=json.dumps(cols))
        self._source = source
        self._canvas = None  # injected by Canvas.insert
        self._namespace = namespace  # injected by Canvas.insert if left None
        self._refresh_interval = refresh
        self._ticker = None
        self._ticker_stop = threading.Event()

    def register_props(self):
        self._props["rows"] = self._build()
        return dict(self._props)

    def refresh(self):
        """Rebuild the table from current state and push it, live."""
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
        if self._source == "globals":
            return self._build_globals()
        return self._build_components()

    def _build_components(self):
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

    def _build_globals(self):
        ns = self._resolve_namespace()
        if not ns:
            return "[]"
        rows = []
        # Snapshot first: the namespace can mutate (e.g. a REPL cell running on
        # the kernel thread) while we iterate.
        for name, value in sorted(list(ns.items()), key=lambda kv: kv[0].lower()):
            # Skip noise: private/dunder names, imported modules, and the
            # injected `canvas` back-reference.
            if name.startswith("_") or name == "canvas":
                continue
            if isinstance(value, types.ModuleType):
                continue
            rows.append({
                "name": name,
                "type": type(value).__name__,
                "value": _short(value),
            })
        return json.dumps(rows)

    def _resolve_namespace(self):
        """The namespace for globals mode: explicit/injected, else IPython's."""
        if self._namespace is not None:
            return self._namespace
        try:
            return get_ipython().user_ns  # type: ignore[name-defined]  # noqa: F821
        except NameError:
            return None
