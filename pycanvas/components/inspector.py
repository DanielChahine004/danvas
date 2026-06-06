"""Inspector: a live table of the canvas's components or the kernel namespace.

One spatial "variable explorer" with a source dropdown in its header to switch
between two views live:

- ``"components"`` (the default) lists every panel on the canvas with its name,
  type, current value and geometry. Reads state pycanvas already tracks, so
  building the table is cheap and safe on the event-loop thread (no kernel).
- ``"globals"`` lists the variables in the shared REPL namespace (the one from
  :meth:`Canvas.enable_repl`), name/type/value -- a notebook-style variable
  explorer, skipping modules and private/dunder names (but keeping ``canvas``).

The two views overlap only partly: a panel you assigned to a variable shows up
in both, but an anonymous panel (no variable) appears only under "components",
and your non-panel variables appear only under "globals".

The panel also has a name-search box and a type filter (both client-side).
Refresh from the panel's button, from Python via :meth:`refresh`, or
automatically with ``refresh=<seconds>``. Click any row to drill into that
object's fields and attributes in a detail view.
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
    """A safe, length-capped repr for a cell in the table.

    Sized containers (list/tuple/set/dict) are prefixed with their length, e.g.
    ``(3) [1, 2, 3]`` -- useful at a glance even when the repr is truncated.
    """
    try:
        text = repr(value)
    except Exception as exc:  # a component's repr should never break the table
        text = f"<repr error: {exc!r}>"
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        try:
            text = f"({len(value)}) {text}"
        except Exception:
            pass
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _object_fields(obj):
    """(name, value) pairs describing an object for the drill-down detail view.

    Containers expose their items; everything else exposes its component/arrow
    ``_props`` (the meaningful config: label, min, max, …) followed by its
    public, non-callable attributes. Private/dunder names and methods are
    skipped to keep the view readable.
    """
    if isinstance(obj, dict):
        return [(_short(k, 40), v) for k, v in obj.items()]
    if isinstance(obj, (list, tuple, set)):
        return [(str(i), v) for i, v in enumerate(obj)]
    fields = []
    seen = set()
    props = getattr(obj, "_props", None)
    if isinstance(props, dict):
        for k, v in props.items():
            fields.append((k, v))
            seen.add(k)
    for k in dir(obj):
        if k.startswith("_") or k in seen:
            continue
        try:
            v = getattr(obj, k)
        except Exception as exc:
            v = f"<error: {exc!r}>"
        if callable(v):
            continue
        fields.append((k, v))
        seen.add(k)
    # Fallback for objects whose state is all private (e.g. the Canvas, which
    # keeps everything under `_components`/`_named`/…): surface the instance
    # __dict__ so the row still drills into something useful. Skip dunders and
    # bound methods; keep single-underscore internals.
    if not fields:
        inst = getattr(obj, "__dict__", None)
        if isinstance(inst, dict):
            for k, v in inst.items():
                if k.startswith("__") or callable(v):
                    continue
                fields.append((k, v))
    return fields


class Inspector(BaseComponent):
    component = "Inspector"
    default_w = 520
    default_h = 320

    def __init__(self, label="inspector", refresh=None, source="components",
                 namespace=None):
        """``source`` is the *initial* view -- ``"components"`` (canvas panels) or
        ``"globals"`` (the shared REPL namespace); either way the panel's header
        dropdown switches between them live. ``namespace`` overrides the
        namespace used by ``"globals"`` mode (defaults to the one from
        :meth:`Canvas.enable_repl`, injected on insert). ``refresh`` is the
        auto-refresh period in seconds (``None`` = manual only); with a period
        set, a daemon thread rebuilds the table on that cadence while the canvas
        is serving and a browser is connected."""
        if source not in ("components", "globals"):
            raise ValueError("source must be 'components' or 'globals'")
        cols = _GLOBALS_COLS if source == "globals" else _COMPONENT_COLS
        super().__init__(label=label, rows="[]", cols=json.dumps(cols),
                         detail="", source=source)
        self._source = source
        self._canvas = None  # injected by Canvas.insert
        self._namespace = namespace  # injected by Canvas.insert if left None
        self._refresh_interval = refresh
        self._ticker = None
        self._ticker_stop = threading.Event()
        # Stable row-key -> object map, rebuilt each _build; the frontend sends a
        # row's `key` back to request its detail view (handles unnamed panels).
        self._row_targets = {}
        # Key of the row currently drilled into in the browser (or None). Tracked
        # so refresh -- manual or the auto ticker -- also re-pushes that object's
        # detail, keeping the open field view live as the object changes.
        self._open_detail_key = None

    def register_props(self):
        self._props["rows"] = self._build()
        return dict(self._props)

    def refresh(self):
        """Rebuild the table from current state and push it, live.

        If a row is currently drilled into, its detail view is rebuilt and
        pushed in the same update so the open fields stay current too.
        """
        payload = {"rows": self._build()}
        if self._open_detail_key:
            payload["detail"] = self._build_detail(self._open_detail_key)
        self._send_update(payload)

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
        action = payload.get("action")
        if action == "refresh":
            self.refresh()
        elif action == "source":
            self._set_source(payload.get("source"))
        elif action == "detail":
            # key=None means the browser closed the detail view (hit back); stop
            # tracking it so the ticker no longer rebuilds a hidden detail.
            key = payload.get("key")
            self._open_detail_key = key or None
            if key:
                self._send_update({"detail": self._build_detail(key)})

    def _set_source(self, source):
        """Switch the live view between "components" and "globals" and rebuild.

        Driven by the frontend's header dropdown; sends the new source, its
        column set and freshly built rows in one update.
        """
        if source not in ("components", "globals") or source == self._source:
            return
        self._source = source
        self._open_detail_key = None
        self._props["source"] = source
        cols = _GLOBALS_COLS if source == "globals" else _COMPONENT_COLS
        self._props["cols"] = json.dumps(cols)
        self._send_update({
            "source": source,
            "cols": self._props["cols"],
            "rows": self._build(),
        })

    def _build(self):
        if self._source == "globals":
            return self._build_globals()
        return self._build_components()

    def _build_components(self):
        self._row_targets = {}
        if self._canvas is None:
            return "[]"
        name_of = {id(c): n for n, c in self._canvas._named.items()}
        rows = []
        # Include every panel -- Repls, other Inspectors, and this Inspector
        # itself -- so the table is a complete picture of the canvas.
        for i, c in enumerate(self._canvas._components):
            name = name_of.get(id(c), "")
            # A stable click key even for unnamed panels (Repl-2, Inspector-3…).
            key = name or f"{c.component}-{i}"
            self._row_targets[key] = c
            rows.append({
                "key": key,
                "name": name,
                "type": c.component,
                "value": _short(c.value),
                "x": c.x,
                "y": c.y,
                "w": c.w,
                "h": c.h,
                "locked": c.locked,
            })
        # Arrows are canvas objects too, but connectors rather than panels: they
        # have a label and endpoints, no geometry. List them after the panels so
        # the table is a complete picture, with their value showing what they
        # link (``start -> end``) and the geometry columns left blank.
        for i, a in enumerate(self._canvas._arrows):
            name = name_of.get(id(a), "")
            key = name or f"Arrow-{i}"
            self._row_targets[key] = a
            rows.append({
                "key": key,
                "name": name,
                "type": "Arrow",
                "value": _short(f"{a.label or '?'}: "
                                f"{a.start._props.get('label') or a.start.id} → "
                                f"{a.end._props.get('label') or a.end.id}"),
                "x": "",
                "y": "",
                "w": "",
                "h": "",
                "locked": "",
            })
        return json.dumps(rows)

    def _build_globals(self):
        self._row_targets = {}
        ns = self._resolve_namespace()
        if not ns:
            return "[]"
        rows = []
        # Snapshot first: the namespace can mutate (e.g. a REPL cell running on
        # the kernel thread) while we iterate.
        for name, value in sorted(list(ns.items()), key=lambda kv: kv[0].lower()):
            # Skip noise: private/dunder names and imported modules. `canvas`
            # (the injected back-reference) is kept -- it's the most useful entry
            # for poking at the live board from the variable explorer.
            if name.startswith("_"):
                continue
            if isinstance(value, types.ModuleType):
                continue
            self._row_targets[name] = value
            rows.append({
                "key": name,
                "name": name,
                "type": type(value).__name__,
                "value": _short(value),
            })
        return json.dumps(rows)

    def _build_detail(self, key):
        """JSON detail (type, repr, field rows) for the row identified by ``key``.

        In globals mode the namespace can hold a fresh object under the same
        name since the table was built, so re-resolve by name there; in
        components mode use the row-key map captured during the last build.
        """
        if self._source == "globals":
            ns = self._resolve_namespace() or {}
            if key in ns:
                obj, found = ns[key], True
            else:
                obj, found = None, key in self._row_targets
                if found:
                    obj = self._row_targets[key]
        else:
            found = key in self._row_targets
            obj = self._row_targets.get(key)
        if not found:
            return json.dumps({"key": key, "name": key, "missing": True})
        fields = []
        try:
            for fname, fval in _object_fields(obj):
                fields.append({
                    "field": str(fname),
                    "type": type(fval).__name__,
                    "value": _short(fval, 200),
                })
        except Exception as exc:
            fields = [{"field": "<error>", "type": "", "value": repr(exc)}]
        return json.dumps({
            "key": key,
            "name": key,
            "type": type(obj).__name__,
            "repr": _short(obj, 300),
            "fields": fields,
        })

    def _resolve_namespace(self):
        """The namespace for globals mode: explicit/injected, else IPython's.

        Resolve IPython via ``from IPython import get_ipython`` rather than the
        bare ``get_ipython`` builtin. That builtin only exists *while a cell is
        executing*, so the auto-refresh ticker thread or a websocket handler
        running under ``serve(block=False)`` -- both off the main thread and
        outside cell execution -- would not see it, and globals mode would come
        up empty. The imported function returns the live shell singleton from
        any thread, at any time.
        """
        if self._namespace is not None:
            return self._namespace
        try:
            from IPython import get_ipython
        except ImportError:
            return None
        ip = get_ipython()
        return ip.user_ns if ip is not None else None
