"""Inspector: a live table of the canvas's components or the kernel namespace.

One spatial "variable explorer" with a source dropdown in its header to switch
between two views live:

- ``"components"`` (the default) lists every panel on the canvas with its name,
  label (its displayed caption — same as the name unless one was set
  separately), type, current value and geometry. Reads state danvas already
  tracks, so
  building the table is cheap and safe on the event-loop thread.
- ``"globals"`` lists the variables in the namespace passed to ``serve(namespace=...)``
  or ``Inspector(namespace=...)``, name/type/value -- a notebook-style variable
  explorer, skipping modules and private/dunder names (but keeping ``canvas``).
- ``"system"`` shows host telemetry -- CPU / RAM (and an NVIDIA GPU when
  ``pynvml`` is present) plus the live thread list -- for a quick health read.
- ``"canvas"`` shows *this* danvas instance instead: the process's own RSS and
  host headroom, the panel / shape / arrow counts (the canvas's "weight"), and
  the connected-viewer count -- the practical "how heavy is this, and how much
  room is left to serve it" view.

The components and globals views overlap only partly: a panel you assigned to a
variable shows up in both, but an anonymous panel (no variable) appears only
under "components", and your non-panel variables appear only under "globals".

The panel also has a name-search box and a type filter (both client-side).
Refresh from the panel's button, from Python via :meth:`refresh`, or
automatically with ``refresh=<seconds>``. Click any row to drill into that
object's fields and attributes in a detail view.

Rendered as a native React panel (mounted by ReactHost): the table, drill-down
detail, search/filter and source dropdown are a React component authored here in
JSX. State is the per-panel channel — Python pushes ``rows``/``cols``/``detail``/
``source`` as props and the panel sends ``{action: …}`` back — so unlike Chat it
needs no shared-room API, only ``canvas.viewport`` for the live view readout.
"""

import json
import threading
import traceback
import types

from . import _theme
from .react import React

# Column sets sent to the frontend per source; the table renders exactly these.
_COMPONENT_COLS = ["name", "label", "type", "value", "visible", "x", "y", "w", "h"]
_GLOBALS_COLS   = ["name", "type", "value"]
_SYSTEM_COLS    = ["name", "type", "value"]
_CANVAS_COLS    = ["name", "type", "value"]

# The view modes the header dropdown switches between, and the columns each shows.
_VIEWS = ("components", "globals", "system", "canvas")
_COLS_BY_VIEW = {
    "components": _COMPONENT_COLS,
    "globals":    _GLOBALS_COLS,
    "system":     _SYSTEM_COLS,
    "canvas":     _CANVAS_COLS,
}

# The React component: a port of the former native InspectorView/DetailView,
# driven by ``canvas.send`` (actions back to Python) and ``canvas.viewport`` (the
# live framing readout) instead of a built-in editor. Authored as a plain string
# so its JSX braces survive — nothing is substituted. Reads the table/detail data
# the owner pushes as ``props.rows``/``props.cols``/``props.detail``/``props.source``
# — each accepted as EITHER a JSON string (what this Python component sends,
# matching the former shape props) or plain JSON (what a non-Python SDK naturally
# sends); tolerant parsing here is the polyglot contract, so no SDK has to know
# which fields were historically double-encoded.
_INSPECTOR_SOURCE = r"""
function ViewReadout({ canvas }) {
  // The current viewport (canvas centre + zoom) — the x/y/zoom serve(view=...)
  // and set_view() take. canvas.viewport calls back live as the camera moves.
  const [v, setV] = React.useState(null);
  React.useEffect(() => (canvas.viewport ? canvas.viewport(setV) : undefined), []);
  if (!v) return null;
  return (
    <div
      style={{
        marginTop: 6, fontSize: 11, fontFamily: "ui-monospace, monospace",
        color: "var(--pc-muted)", userSelect: "text", WebkitUserSelect: "text",
        cursor: "text",
      }}
      title="current viewport — pass these to serve(view=...) or canvas.set_view() to fix this view"
    >
      view: x={v.x} y={v.y} zoom={v.zoom.toFixed(2)}
    </div>
  );
}

// Drill-down: an object's type/repr header plus a field/type/value table.
function DetailView({ selected, detail, onBack, onRefresh, controlStyle }) {
  const [query, setQuery] = React.useState("");
  const [typeFilter, setTypeFilter] = React.useState("all");
  // Reset filters when drilling into a different object.
  React.useEffect(() => { setQuery(""); setTypeFilter("all"); }, [selected]);

  const allFields = detail && Array.isArray(detail.fields) ? detail.fields : [];
  const types = ["all", ...Array.from(new Set(allFields.map((f) => f.type))).sort()];
  const selectable = { userSelect: "text", WebkitUserSelect: "text", cursor: "text" };
  const q = query.toLowerCase();
  const fields = allFields.filter(
    (f) =>
      (typeFilter === "all" || f.type === typeFilter) &&
      (!q || String(f.field ?? "").toLowerCase().includes(q))
  );
  const filtered = q !== "" || typeFilter !== "all";
  return (
    <>
      <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
        <button style={{ ...controlStyle, cursor: "pointer" }} onClick={onBack}>← back</button>
        <span style={{ flex: 1, minWidth: 0, fontSize: 13, fontWeight: 600,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {selected}
          {detail && <span style={{ fontWeight: 400, color: "var(--pc-faint)" }}> : {detail.type}</span>}
        </span>
        <button style={{ ...controlStyle, cursor: "pointer" }} onClick={onRefresh}>Refresh</button>
      </div>
      {detail && !detail.missing && allFields.length > 0 && (
        <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
          <input placeholder="search field…" value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ ...controlStyle, flex: 1, minWidth: 0 }} />
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} style={controlStyle}>
            {types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {!detail ? (
          <div style={{ fontSize: 12, color: "var(--pc-faint2)", padding: 6 }}>loading…</div>
        ) : detail.missing ? (
          <div style={{ fontSize: 12, color: "var(--pc-faint2)", padding: 6 }}>no longer available</div>
        ) : (
          <>
            <div style={{ fontSize: 12, fontFamily: "ui-monospace, monospace",
              color: "var(--pc-detail-text)", background: "var(--pc-detail-bg)",
              border: "1px solid var(--pc-detail-border)", borderRadius: 4,
              padding: "4px 6px", marginBottom: 6, wordBreak: "break-all", ...selectable }}>
              {detail.repr}
            </div>
            <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {["field", "type", "value"].map((c) => (
                    <th key={c} style={{ textAlign: "left", padding: "2px 6px",
                      borderBottom: "1px solid var(--pc-border-mid)", color: "var(--pc-muted)",
                      position: "sticky", top: 0, background: "var(--pc-bg)" }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {fields.length === 0 ? (
                  <tr>
                    <td colSpan={3} style={{ padding: 6, color: "var(--pc-faint2)", fontStyle: "italic" }}>
                      {filtered ? "no matching fields" : "no fields — see repr above"}
                    </td>
                  </tr>
                ) : (
                  fields.map((f, i) => (
                    <tr key={i}>
                      <td style={{ padding: "2px 6px", borderBottom: "1px solid var(--pc-border-soft)", ...selectable }}>{f.field}</td>
                      <td style={{ padding: "2px 6px", borderBottom: "1px solid var(--pc-border-soft)", color: "var(--pc-faint)" }}>{f.type}</td>
                      <td style={{ padding: "2px 6px", borderBottom: "1px solid var(--pc-border-soft)",
                        fontFamily: "ui-monospace, monospace", ...selectable }}>{f.value}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </>
        )}
      </div>
    </>
  );
}

function Component({ canvas, props }) {
  const [query, setQuery] = React.useState("");
  const [typeFilter, setTypeFilter] = React.useState("all");
  // Which row is drilled into (its key), or null for the table view.
  const [selected, setSelected] = React.useState(null);

  // Tolerant reads: a JSON string (Python) or plain JSON (other SDKs).
  const asJson = (v, fallback) => {
    if (v == null || v === "") return fallback;
    if (typeof v !== "string") return v;
    try { return JSON.parse(v) ?? fallback; } catch { return fallback; }
  };
  const rows = asJson(props.rows, []);
  let cols = ["name", "label", "type", "value", "x", "y", "w", "h"];
  const parsedCols = asJson(props.cols, null);
  if (Array.isArray(parsedCols) && parsedCols.length) cols = parsedCols;

  const controlStyle = {
    fontSize: 12, padding: "3px 6px", border: "1px solid var(--pc-border-mid)",
    borderRadius: 6, background: "var(--pc-input-bg)", color: "var(--pc-text)",
  };

  // --- detail (drill-down) view -------------------------------------------
  if (selected != null) {
    const detail = asJson(props.detail, null);
    // Only show detail once it's arrived for the row we clicked (avoid stale).
    const ready = detail && detail.key === selected;
    return (
      <DetailView
        selected={selected}
        detail={ready ? detail : null}
        onBack={() => { setSelected(null); canvas.send({ action: "detail", key: null }); }}
        onRefresh={() => canvas.send({ action: "detail", key: selected })}
        controlStyle={controlStyle} />
    );
  }

  const types = ["all", ...Array.from(new Set(rows.map((r) => r.type))).sort()];
  const q = query.toLowerCase();
  const shown = rows.filter(
    (r) =>
      (typeFilter === "all" || r.type === typeFilter) &&
      (!q || String(r.name ?? "").toLowerCase().includes(q))
  );

  const openDetail = (r) => {
    const key = r.key ?? r.name;
    if (!key) return;
    setSelected(key);
    canvas.send({ action: "detail", key });
  };

  const source = props.source || "components";
  const switchSource = (next) => {
    if (next === source) return;
    setTypeFilter("all"); // the type set differs between the two views
    setQuery("");
    canvas.send({ action: "source", source: next });
  };

  return (
    <>
      <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
        <select value={source} onChange={(e) => switchSource(e.target.value)} style={controlStyle} title="what to inspect">
          <option value="components">panels</option>
          <option value="canvas">canvas</option>
          <option value="globals">globals</option>
          <option value="system">system</option>
        </select>
        <input placeholder="search name…" value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ ...controlStyle, flex: 1, minWidth: 0 }} />
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} style={controlStyle}>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <button style={{ ...controlStyle, cursor: "pointer" }} onClick={() => canvas.send({ action: "refresh" })}>Refresh</button>
        <button style={{ ...controlStyle, cursor: "pointer" }}
          title="open the live dispatch-trace panel"
          onClick={() => canvas.send({ action: "trace" })}>Trace</button>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c} style={{ textAlign: "left", padding: "2px 6px",
                  borderBottom: "1px solid var(--pc-border-mid)", color: "var(--pc-muted)",
                  position: "sticky", top: 0, background: "var(--pc-bg)" }}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} onClick={() => openDetail(r)} style={{ cursor: "pointer" }} title="click to inspect fields">
                {cols.map((c) => (
                  <td key={c} style={{ padding: "2px 6px", borderBottom: "1px solid var(--pc-border-soft)",
                    fontFamily: c === "value" ? "ui-monospace, monospace" : "inherit" }}>
                    {String(r[c] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ViewReadout canvas={canvas} />
    </>
  );
}
"""


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
            # `_props` is the registration-time snapshot, so a value it shares
            # with a live attribute can go stale -- e.g. a Slider keeps its
            # current position in the `.value` property while `_props["value"]`
            # stays the initial default. Prefer the live attribute when one
            # exists so the detail view tracks changes instead of freezing.
            if not k.startswith("_"):
                try:
                    live = getattr(obj, k)
                    if not callable(live):
                        v = live
                except Exception:
                    pass
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


class Inspector(React):
    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {"rows": "list|json-str -- the table rows",
                 "cols": "list[str]|json-str",
                 "detail": "object|json-str -- {key, type, repr, fields: "
                           "[{field, type, value}], missing?}",
                 "source": "str -- components|globals|system|canvas"},
        "updates": {"data_patch": "merge changed data fields"},
        "events": [{"action": "refresh|trace"},
                   {"action": "source", "source": "str"},
                   {"action": "detail", "key": "str|null"}],
    }
    default_w = 520
    default_h = 320

    def __init__(self, name="inspector", refresh=None, source="components",
                 namespace=None, color=None, label=None):
        """``source`` is the *initial* view -- one of ``"components"`` (canvas
        panels), ``"canvas"`` (this instance's memory footprint + weight),
        ``"globals"`` (variables from the script namespace) or ``"system"``
        (host CPU/RAM/GPU/threads); the header dropdown switches between them
        live. ``namespace`` overrides the namespace used by ``"globals"`` mode
        (defaults to the one from ``serve(namespace=...)``, injected on insert).
        ``refresh`` is the auto-refresh period in seconds (``None`` = manual
        only); with a period set, a daemon thread rebuilds the table on that
        cadence while the canvas is serving and a browser is connected."""
        if source not in _VIEWS:
            raise ValueError("source must be one of: " + ", ".join(_VIEWS))
        cols = _COLS_BY_VIEW[source]
        # The table/detail data rides in the React props (``data`` JSON blob),
        # replayed on reconnect. ``source`` here is the *view mode*, distinct from
        # React's own ``source`` (the JSX); tracked internally as ``self._view``.
        super().__init__(source=_INSPECTOR_SOURCE, name=name, label=label,
                         props={"rows": "[]", "cols": json.dumps(cols),
                                "detail": "", "source": source})
        self._init_color(color)
        # The table is a fixed-height, scrollable box (its JSX uses
        # ``flex:1; overflow:auto``), so turn off React's default auto-height —
        # otherwise the panel grows to fit every row and a manual resize is
        # overridden, leaving no way to make it scroll.
        self._auto_h = False
        # A dev panel: deleting it in the browser closes it (re-open from the
        # toolbar / a fresh canvas.inspector()) rather than going to the
        # graveyard — see Bridge._graveyard.
        self._ephemeral = True
        self._view = source
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
        # Build the table fresh at register time so a (re)connecting client sees
        # current state baked into the React ``data`` prop.
        self._data["rows"] = self._build()
        return super().register_props()

    def refresh(self):
        """Rebuild the table from current state and push it, live.

        If a row is currently drilled into, its detail view is rebuilt and
        pushed in the same update so the open fields stay current too.
        """
        payload = {"rows": self._build()}
        if self._open_detail_key:
            payload["detail"] = self._build_detail(self._open_detail_key)
        self.update(**payload)

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

    def _handle_input(self, payload, viewer=None):
        action = payload.get("action")
        if action == "refresh":
            self.refresh()
        elif action == "source":
            self._set_view(payload.get("source"))
        elif action == "detail":
            # key=None means the browser closed the detail view (hit back); stop
            # tracking it so the ticker no longer rebuilds a hidden detail.
            key = payload.get("key")
            self._open_detail_key = key or None
            if key:
                self.update(detail=self._build_detail(key))
        elif action == "trace":
            self._open_trace()

    def _open_trace(self):
        """Toggle the live dispatch-trace panel beside this inspector.

        The Inspector is where you go to see what the canvas *is*; the trace panel
        shows what it's *doing*. Launching it from here keeps the two together
        without forcing a live, indented call tree into the Inspector's
        snapshot-table model. Clicking again closes the panel (and detaches its
        dispatch tap), so the button opens and closes it."""
        canvas = self._canvas
        if canvas is None:
            return
        panel = getattr(self, "_trace_panel", None)
        if panel is not None and panel.id in canvas._bridge._components:
            tap = getattr(panel, "_dispatch_tap", None)
            if tap is not None:
                canvas.off_dispatch(tap)
            canvas.remove(panel)
            self._trace_panel = None
            return
        try:
            self._trace_panel = canvas.trace(right_of=self, gap=20)
        except Exception:
            traceback.print_exc()

    def _set_view(self, source):
        """Switch the live view between the inspector's modes (panels, canvas,
        globals, system).

        Driven by the frontend's header dropdown; sends the new view, its column
        set and freshly built rows in one update.
        """
        if source not in _VIEWS or source == self._view:
            return
        self._view = source
        self._open_detail_key = None
        self.update(source=source, cols=json.dumps(_COLS_BY_VIEW[source]),
                    rows=self._build())

    def _build(self):
        builder = {
            "globals": self._build_globals,
            "system":  self._build_system,
            "canvas":  self._build_canvas,
        }.get(self._view, self._build_components)
        return builder()

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
                # The displayed caption. Defaults to the name, so the two columns
                # match unless a distinct ``label`` was given.
                "label": c._props.get("label", ""),
                "type": c.component,
                "value": _short(c.value),
                "visible": c.visible,
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
                # Arrows have no label prop; their caption is the drawn ``text``.
                "label": a.text or "",
                "type": "Arrow",
                "value": _short(f"{a.text or '?'}: "
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

    def _build_system(self):
        """Rows for the "system" view: CPU/RAM/GPU metrics + active threads.

        CPU and RAM require ``psutil`` (optional); GPU requires ``pynvml``
        (optional, NVIDIA only). Active threads are always shown via
        ``threading.enumerate()`` with no extra dependencies.
        """
        self._row_targets = {}
        rows = []

        # --- CPU / RAM (psutil) ----------------------------------------------
        try:
            import psutil
            cpu_pct = psutil.cpu_percent(interval=None)
            cpu_count = psutil.cpu_count(logical=True)
            mem = psutil.virtual_memory()
            used_gb  = mem.used  / 1024 ** 3
            total_gb = mem.total / 1024 ** 3
            cpu_data = {"percent": cpu_pct, "logical_cores": cpu_count,
                        "physical_cores": psutil.cpu_count(logical=False)}
            mem_data = {"used_gb": round(used_gb, 2), "total_gb": round(total_gb, 2),
                        "percent": mem.percent, "available_gb": round(mem.available / 1024**3, 2)}
            self._row_targets["cpu"] = cpu_data
            self._row_targets["ram"] = mem_data
            rows.append({"key": "cpu", "name": "cpu", "type": "metric",
                         "value": f"{cpu_pct:.1f}%  ({cpu_count} logical cores)"})
            rows.append({"key": "ram", "name": "ram", "type": "metric",
                         "value": f"{used_gb:.1f} / {total_gb:.1f} GB  ({mem.percent:.0f}%)"})
        except ImportError:
            hint = {"hint": "pip install psutil"}
            self._row_targets["_psutil"] = hint
            rows.append({"key": "_psutil", "name": "cpu / ram", "type": "hint",
                         "value": "install psutil for CPU & RAM metrics"})

        # --- GPU (pynvml, NVIDIA only) ----------------------------------------
        try:
            import pynvml
            pynvml.nvmlInit()
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                gpu_name = pynvml.nvmlDeviceGetName(h)
                if isinstance(gpu_name, bytes):
                    gpu_name = gpu_name.decode()
                util = pynvml.nvmlDeviceGetUtilizationRates(h)
                mi   = pynvml.nvmlDeviceGetMemoryInfo(h)
                gpu_data = {"name": gpu_name, "util_gpu": util.gpu, "util_mem": util.memory,
                            "mem_used_gb": round(mi.used / 1024**3, 1),
                            "mem_total_gb": round(mi.total / 1024**3, 1)}
                key = f"gpu:{i}"
                self._row_targets[key] = gpu_data
                rows.append({"key": key, "name": key, "type": "metric",
                             "value": (f"{gpu_name}  {util.gpu}% util  "
                                       f"{gpu_data['mem_used_gb']}/{gpu_data['mem_total_gb']} GB vram")})
        except Exception:
            pass  # pynvml not installed or no NVIDIA GPU — silently omit

        # --- Active threads --------------------------------------------------
        pc_markers = {"danvas", "asyncio", "danvas", "_ticker", "_tick_loop",
                      "uvicorn", "starlette", "watchdog", "AnyIO", "_run", "_scan"}
        main_thread = threading.main_thread()
        for t in sorted(threading.enumerate(), key=lambda t: t.name.lower()):
            if t is main_thread:
                kind = "main"
            elif any(m.lower() in t.name.lower() for m in pc_markers):
                kind = "danvas"
            elif t.daemon:
                kind = "daemon"
            else:
                kind = "thread"
            key = f"thread:{t.ident}"
            self._row_targets[key] = t
            rows.append({"key": key, "name": t.name, "type": kind,
                         "value": f"alive={t.is_alive()}  ident={t.ident}"})

        return json.dumps(rows)

    def _build_canvas(self):
        """Rows for the "canvas" view: this danvas instance's own footprint and
        the weight of what it's serving.

        Distinct from the host-wide "system" view -- the memory here is *this
        process's* RSS (not the whole machine), and the panel/shape/arrow counts
        plus connected-viewer count are the things that actually grow serving
        cost. The practical answer to "how heavy is this canvas, and how much
        room is left to serve it". Each row drills down: process ram into the
        byte figures + host headroom, the weight rows into a per-type breakdown,
        viewers into the live roster.
        """
        self._row_targets = {}
        rows = []
        canvas = self._canvas

        # --- this process's memory footprint (psutil) ------------------------
        try:
            import psutil
            rss = psutil.Process().memory_info().rss
            vm = psutil.virtual_memory()
            rss_mb = rss / 1024 ** 2
            avail_gb = vm.available / 1024 ** 3
            # Share of *currently free* host RAM this process holds -- a rough
            # "how much of the remaining room am I using" gauge for headroom.
            denom = rss + vm.available
            pct_free = (rss / denom * 100) if denom else 0.0
            data = {"rss_mb": round(rss_mb, 1), "rss_bytes": rss,
                    "pct_of_host_total": round(rss / vm.total * 100, 2),
                    "pct_of_host_free": round(pct_free, 2),
                    "host_available_gb": round(avail_gb, 2),
                    "host_total_gb": round(vm.total / 1024 ** 3, 2)}
            self._row_targets["process_ram"] = data
            rows.append({"key": "process_ram", "name": "process ram", "type": "metric",
                         "value": f"{rss_mb:.0f} MB rss  ·  {avail_gb:.1f} GB host free "
                                  f"({pct_free:.1f}% of free)"})
        except ImportError:
            hint = {"hint": "pip install psutil"}
            self._row_targets["_psutil"] = hint
            rows.append({"key": "_psutil", "name": "process ram", "type": "hint",
                         "value": "install psutil for the process memory footprint"})

        # --- canvas weight: what actually grows serving cost -----------------
        if canvas is not None:
            def _counts(objs, type_of):
                by = {}
                for o in objs:
                    t = type_of(o)
                    by[t] = by.get(t, 0) + 1
                return dict(sorted(by.items(), key=lambda kv: -kv[1]))

            panels = list(canvas.components)
            shapes = list(canvas.shapes)
            arrows = list(canvas.arrows)
            # Break geo shapes down by their sub-type (rectangle/ellipse/…); other
            # shapes by their kind (text/note/line/frame/…).
            shape_kind = lambda s: (s._props.get("geo", s._type)
                                    if s._type == "geo" else s._type)

            def weight_row(key, label, items, by):
                self._row_targets[key] = {"total": len(items), **by}
                detail = "  ".join(f"{n}×{t}" for t, n in by.items()) or "—"
                rows.append({"key": key, "name": label, "type": "weight",
                             "value": f"{len(items)}   ({detail})"})

            # Break panels down by Python class (Label/Slider/Inspector/…); the
            # wire ``.component`` is "React" for most built-ins, so it would just
            # report "N×React" here — the class name is the useful split.
            weight_row("panels", "panels", panels, _counts(panels, lambda c: type(c).__name__))
            weight_row("shapes", "shapes", shapes, _counts(shapes, shape_kind))
            # Arrows are all one kind, so no per-type split is worth showing.
            self._row_targets["arrows"] = {"total": len(arrows)}
            rows.append({"key": "arrows", "name": "arrows", "type": "weight",
                         "value": str(len(arrows))})

            # --- connected viewers: serving cost scales with these -----------
            viewers = canvas.viewers
            self._row_targets["viewers"] = viewers
            names = ", ".join(str(v.get("name") or v.get("id", "?")) for v in viewers)
            rows.append({"key": "viewers", "name": "viewers", "type": "metric",
                         "value": f"{len(viewers)}" + (f"   ({names})" if viewers else "")})

        return json.dumps(rows)

    def _build_detail(self, key):
        """JSON detail (type, repr, field rows) for the row identified by ``key``.

        In globals mode the namespace can hold a fresh object under the same
        name since the table was built, so re-resolve by name there; in
        components mode use the row-key map captured during the last build.
        """
        if self._view == "globals":
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

        Falls back to ``canvas._namespace`` (set via ``serve(namespace=...)``)
        so that the Inspector spawned via the toolbar button (which never
        receives an explicit namespace) can still show the script's globals.
        """
        if self._namespace is not None:
            return self._namespace
        try:
            from IPython import get_ipython
        except ImportError:
            pass
        else:
            ip = get_ipython()
            if ip is not None:
                return ip.user_ns
        canvas = self._canvas
        if canvas is not None:
            return canvas._namespace
        return None