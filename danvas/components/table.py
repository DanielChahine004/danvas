"""Table: show tabular data as an interactive, native (React) canvas panel.

Accepts a pandas DataFrame/Series, a list of dicts, a list of rows
(lists/tuples), or a dict of columns. pandas is duck-typed, so it isn't a hard
dependency. The panel is a **native React subtree** (not a sandboxed iframe), so
it re-renders sharp at every zoom level — an iframe is rasterised and then scaled
when the canvas zooms, which blurs dense text. It is interactive in the browser:

- **column profile** under each header — the inferred dtype (``int``, ``float``,
  ``bool``, ``str``, ``mixed``) and a red ``n% null`` badge when values are
  missing; hover the header for fuller stats (unique count, min/max/mean/median);
- **click a header** to sort by that column (cycles ascending → descending →
  original order); numeric columns sort numerically, everything else by text;
- **filter box** hides rows that don't contain the typed text (across the whole
  dataset, not just the visible page);
- **pagination** for large tables: rows are shown ``PAGE_SIZE`` at a time with
  prev/next arrows and a typable page field (its up/down spinners step a page).
  Sorting and filtering apply to *all* rows, including those on other pages;
- **per-column distributions** under each header — a histogram for numeric
  columns, a top-values bar chart for categorical ones — always shown. Numeric
  charts caption their min, mean, and max; hovering a bar shows its count and the
  share of the column it represents. **Click a bar** to filter the table to that
  bin (numeric) or category (text); a chip in the toolbar shows the active filter
  and clears it on click.

Python normalises the data and computes the per-column profiles/distributions,
then hands the columns, rows, and that metadata to the React component as props;
the browser owns sort/filter/pagination over the full dataset. ``update(data)``
re-renders with fresh data.
"""

from collections import Counter

from . import _theme
from .react import React

# The full dataset is shipped to the panel and the browser renders one
# PAGE_SIZE-row page into the DOM at a time (a 600k-row table is far too much
# DOM to render at once). Sorting and filtering run over the whole dataset in
# React — not just the rendered page — so they cover unseen rows; pagination
# controls page through the result.
PAGE_SIZE = 2000

# Scoped under `.pc-tbl` (a native node shares the page, so bare `table`/`th`
# selectors would leak). The grid keeps its own light "notebook" look rather than
# following the canvas theme, matching how the same data renders in a notebook.
_TABLE_CSS = """
.pc-tbl{display:flex;flex-direction:column;height:100%;font-size:12px;
 background:#fff;color:#0f172a;border-radius:4px;overflow:hidden;box-sizing:border-box}
.pc-tbl .pc-bar{display:flex;align-items:center;gap:8px;padding:6px 8px;
 border-bottom:1px solid #e2e8f0;flex:none}
.pc-tbl .pc-filter{flex:1;min-width:60px;padding:3px 6px;border:1px solid #cbd5e1;
 border-radius:4px;font-size:12px}
.pc-tbl .pc-btn{padding:3px 8px;border:1px solid #cbd5e1;border-radius:4px;
 background:#f8fafc;cursor:pointer;font-size:11px;color:#334155}
.pc-tbl .pc-btn.on{background:#2563eb;color:#fff;border-color:#2563eb}
.pc-tbl .pc-count{color:#64748b;font-size:11px;white-space:nowrap}
.pc-tbl .pc-scroll{overflow:auto;flex:1}
.pc-tbl table{border-collapse:collapse;width:100%}
.pc-tbl th,.pc-tbl td{border:1px solid #e2e8f0;padding:4px 8px;text-align:left;
 white-space:nowrap}
.pc-tbl thead th{position:sticky;top:0;background:#f8fafc;font-weight:600;
 cursor:pointer;user-select:none}
.pc-tbl .pc-th-meta{font-weight:400;color:#94a3b8;font-size:10px;margin-top:1px;
 white-space:nowrap}
.pc-tbl .pc-th-null{color:#e11d48}
.pc-tbl .pc-th-type-mix{color:#64748b;font-size:10px}
.pc-tbl thead tr.pc-dist th{position:static;background:#fff;cursor:default;
 font-weight:400;padding:3px 6px;vertical-align:bottom}
.pc-tbl tbody tr:nth-child(even) td{background:#f8fafc}
.pc-tbl td{font-variant-numeric:tabular-nums}
.pc-tbl .pc-arrow{color:#94a3b8;font-size:10px;margin-left:3px}
.pc-tbl .pc-spark{width:100%;height:28px;display:block}
.pc-tbl .pc-spark rect{fill:#60a5fa;cursor:pointer}
.pc-tbl .pc-spark rect:hover{fill:#3b82f6}
.pc-tbl .pc-spark rect.pc-sel{fill:#1d4ed8}
.pc-tbl .pc-cap{color:#94a3b8;font-size:9px;margin-top:1px;display:flex;
 justify-content:space-between;gap:6px}
.pc-tbl .pc-pager{display:flex;align-items:center;gap:3px;flex:none}
.pc-tbl .pc-pg{border:1px solid #cbd5e1;border-radius:4px;background:#f8fafc;
 cursor:pointer;color:#334155;font-size:13px;line-height:1;padding:1px 7px}
.pc-tbl .pc-pg:hover{background:#eef2f7}
.pc-tbl .pc-page{width:46px;padding:3px 4px;border:1px solid #cbd5e1;
 border-radius:4px;font-size:11px;text-align:right}
.pc-tbl .pc-pages{color:#64748b;font-size:11px;white-space:nowrap}
.pc-tbl th.pc-idx,.pc-tbl td.pc-idx{color:#94a3b8;text-align:right;font-size:11px;
 min-width:2.5ch}
.pc-tbl th.pc-idx{cursor:default;font-weight:500;user-select:none}
.pc-tbl .pc-col-wrap{position:relative}
.pc-tbl .pc-col-menu{position:absolute;top:calc(100% + 4px);left:0;z-index:100;
 background:#fff;border:1px solid #e2e8f0;border-radius:6px;
 box-shadow:0 4px 12px rgba(0,0,0,.1);padding:4px 0;
 min-width:140px;max-height:220px;overflow-y:auto}
.pc-tbl .pc-col-item{display:flex;align-items:center;gap:6px;padding:4px 10px;
 cursor:pointer;font-size:12px;color:#334155;white-space:nowrap;user-select:none}
.pc-tbl .pc-col-item:hover{background:#f1f5f9}
.pc-tbl .pc-col-item input[type=checkbox]{cursor:pointer;accent-color:#2563eb;
 flex:none}
.pc-tbl .pc-overlay{position:fixed;inset:0;z-index:99}
.pc-tbl th.pc-sel-col,.pc-tbl td.pc-sel-col{width:28px;min-width:28px;
 padding:2px 6px;text-align:center}
.pc-tbl th.pc-sel-col{cursor:pointer;font-weight:400}
.pc-tbl th.pc-sel-col input,.pc-tbl td.pc-sel-col input{cursor:pointer;
 accent-color:#2563eb}
.pc-tbl.pc-editable tbody td{cursor:text}
.pc-tbl td.pc-editing{padding:0}
.pc-tbl td.pc-editing input{width:100%;padding:4px 8px;border:none;
 outline:2px solid #2563eb;outline-offset:-2px;font:inherit;
 background:#eff6ff;box-sizing:border-box}
"""

# The React component. Written as a plain string (no str.format/f-string) so its
# JSX braces are left intact; only the __CSS__ marker is substituted below. props
# carries cols / numeric / rows (display strings) / profiles / dists / pageSize;
# all sort/filter/pagination state lives in React, operating over the full data.
from . import _jsx

_TABLE_SOURCE = _jsx.load("table").replace("__CSS__", _TABLE_CSS)


class Table(React):
    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {"cols": "list[str]", "rows": "list[list]",
                 "numeric": "list[bool]", "pageSize": "number",
                 "dists": "list|null -- per-column distribution bars",
                 "profiles": "list|null -- per-column summary stats",
                 "editable": "bool",
                 "selected": "list[int]|absent -- programmatic row selection; "
                             "applied silently (no selected event echoed)"},
        "updates": {"data_patch": "merge changed data fields"},
        "events": [{"selected": "list[number] -- selected row indexes"},
                   {"edited": "object {row: number, col: number, value}"}],
    }
    default_w = 520
    default_h = 360

    def __init__(self, data, name="table", label=None, w=None, h=None, color=None,
                 editable=False):
        cols, rows = _normalize(data)
        props = _table_props(cols, rows)
        props["_th"] = _theme.derive(color) if color is not None else {}
        if editable:
            props["editable"] = True
        super().__init__(source=_TABLE_SOURCE, name=name, label=label, w=w, h=h,
                         props=props)
        self._init_color(color)
        self._cols = cols  # kept so _handle_input can resolve ci → col name
        self._selected = []
        self._select_callbacks = []
        self._edit_callbacks = []

    def _handler_sources(self):
        yield from super()._handler_sources()
        yield ("select", self._select_callbacks)
        yield ("edit", self._edit_callbacks)

    @property
    def selected(self):
        """The 0-based indices of currently selected rows in the original data.

        Assignable: ``table.selected = [0, 3]`` selects those rows in every
        browser (and ``[]`` clears). Programmatic selection is silent — it
        never fires ``on_select``, matching how a Python push of a control's
        value never fires ``on_change``."""
        with self._lock:
            return list(self._selected)

    @selected.setter
    def selected(self, indices):
        idx = sorted({int(i) for i in indices})
        n = len(self._data.get("rows") or [])
        bad = [i for i in idx if i < 0 or i >= n]
        if bad:
            raise IndexError(f"row index {bad[0]} out of range (table has {n} rows)")
        with self._lock:
            self._selected = idx
        React.update(self, selected=idx)

    def on_edit(self, fn=None, *, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: called with ``(row, col, value)`` when the user edits a cell.

        ``row`` is the 0-based index into the original data (same as the ``#``
        column). ``col`` is the column name string. ``value`` is always a string
        — coerce to int/float in your callback if needed. Only fires when
        ``editable=True`` was passed to the constructor.
        See :meth:`on_change <danvas.components.base.BaseComponent.on_change>`
        for the full ``threaded`` / ``dedicated`` / ``queue`` semantics.
        """
        def register(f):
            return self._register_callback(self._edit_callbacks, f, threaded, dedicated, queue)
        return register(fn) if fn is not None else register

    def on_select(self, fn=None, *, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: called with a list of selected row indices on each selection change.

        The indices are 0-based positions in the original Python data structure
        (the same values shown in the ``#`` index column). Fires on every checkbox
        toggle, including when the selection is cleared (empty list).
        See :meth:`on_change <danvas.components.base.BaseComponent.on_change>`
        for the full ``threaded`` / ``dedicated`` / ``queue`` semantics.
        ``threaded`` and ``dedicated`` are mutually exclusive.
        """
        def register(f):
            return self._register_callback(self._select_callbacks, f, threaded, dedicated, queue)
        return register(fn) if fn is not None else register

    def _handle_input(self, payload, viewer=None):
        if isinstance(payload, dict) and "selected" in payload:
            with self._lock:
                self._selected = list(payload["selected"])
            self._dispatch_callbacks(self._select_callbacks, (list(self._selected),), viewer)
            return
        if isinstance(payload, dict) and "edited" in payload:
            e = payload["edited"]
            row = e.get("row")
            ci = e.get("col")
            value = e.get("value", "")
            with self._lock:
                cols = self._cols
            col = cols[ci] if ci is not None and ci < len(cols) else str(ci)
            self._dispatch_callbacks(self._edit_callbacks, (row, col, value), viewer)
            return
        super()._handle_input(payload, viewer)

    def update(self, data):
        """Replace the table contents, live."""
        cols, rows = _normalize(data)
        with self._lock:
            self._cols = cols
        super().update(**_table_props(cols, rows))


# -- input normalization -----------------------------------------------------
def _is_seq(v):
    """True for a column-like value: iterable but not a string/bytes scalar."""
    return hasattr(v, "__iter__") and not isinstance(v, (str, bytes))


def _normalize(data):
    """Coerce any supported input to ``(columns, rows)`` with string headers.

    ``columns`` is always a list of header labels (synthesized as ``0, 1, …`` for
    headerless row lists); ``rows`` is a list of value lists.
    """
    # pandas Series -> one-column frame (Series has to_frame but no columns).
    if hasattr(data, "to_frame") and not hasattr(data, "columns"):
        data = data.to_frame()
    # pandas DataFrame (or anything exposing columns + values, duck-typed).
    if hasattr(data, "columns") and hasattr(data, "values") \
            and not callable(getattr(data, "values", None)):
        cols = [str(c) for c in data.columns]
        rows = [list(r) for r in data.values.tolist()]
        index = getattr(data, "index", None)
        # Prepend the index as a leading column unless it's a trivial 0..n-1
        # RangeIndex (which carries no information).
        if index is not None and type(index).__name__ != "RangeIndex":
            cols = [getattr(index, "name", None) or ""] + cols
            rows = [[i] + r for i, r in zip(list(index), rows)]
        return cols, rows
    if isinstance(data, dict):
        values = list(data.values())
        # Dict of columns: {col: [values...]} — every value is a (non-string)
        # sequence, so the keys are headers and the sequences are the columns.
        if values and all(_is_seq(v) for v in values):
            cols = [str(c) for c in data.keys()]
            rows = [list(r) for r in zip(*[list(data[c]) for c in data])]
            return cols, rows
        # Otherwise a flat mapping (e.g. hyperparameters {"lr": 3e-4, ...}) —
        # render it as a two-column key/value table, which reads far better than
        # a one-row wide table.
        return ["key", "value"], [[str(k), v] for k, v in data.items()]
    # list of dicts -> union of keys in first-seen order.
    if isinstance(data, (list, tuple)) and data and isinstance(data[0], dict):
        cols = []
        for row in data:
            for k in row:
                if k not in cols:
                    cols.append(k)
        rows = [[row.get(c, "") for c in cols] for row in data]
        return [str(c) for c in cols], rows
    # list of rows (lists/tuples or scalars): synthesize integer headers.
    if isinstance(data, (list, tuple)):
        rows = [list(r) if isinstance(r, (list, tuple)) else [r] for r in data]
        ncol = max((len(r) for r in rows), default=0)
        return [str(i) for i in range(ncol)], rows
    raise TypeError(f"can't render {type(data).__name__} as a table")


# -- props (Python computes everything the React panel needs) ----------------
def _str(v):
    """A cell's display string. The browser renders it via React (text nodes are
    escaped), so the payload carries raw strings, not HTML."""
    return "" if v is None else str(v)


def _pct(p):
    """Format a percentage: whole numbers at/above 1% (and exactly 0), one
    decimal in between (so a tiny but non-zero share doesn't round to ``0``)."""
    if p == 0 or p >= 1:
        return f"{p:.0f}"
    return f"{p:.1f}"


def _is_number(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v)
            return True
        except ValueError:
            return False
    return False


def _table_props(cols, rows):
    """Build the prop dict the React table renders from: headers, the numeric
    flags, every row as display strings, and each column's profile + distribution.
    """
    cols = [str(c) for c in cols]
    ncol = len(cols)
    # Classify each column: numeric if the majority of its non-empty cells parse
    # as numbers. Drives numeric sorting and which distribution chart to draw.
    numeric = []
    for i in range(ncol):
        present = [r[i] for r in rows if i < len(r) and r[i] not in (None, "")]
        numeric.append(bool(present)
                       and sum(_is_number(v) for v in present) >= 0.6 * len(present))
    profiles = [
        _column_profile([r[i] for r in rows if i < len(r)], numeric[i])
        for i in range(ncol)
    ]
    dists = [
        _distribution([r[i] for r in rows if i < len(r)], numeric[i])
        for i in range(ncol)
    ]
    data_rows = [[_str(r[i]) if i < len(r) else "" for i in range(ncol)]
                 for r in rows]
    return {"cols": cols, "numeric": numeric, "rows": data_rows,
            "profiles": profiles, "dists": dists, "pageSize": PAGE_SIZE}


def _column_profile(values, numeric):
    """Summarize one column for the header: a compact ``meta`` line shown under
    the column name and a fuller ``tip`` string surfaced on hover.

    ``meta`` carries the inferred dtype plus a missing-value badge (a small HTML
    fragment, rendered into the header); ``tip`` adds unique counts and (for
    numeric columns) min/max/mean/median. Never raises — a single odd column
    shouldn't break the header.
    """
    try:
        total = len(values)
        present = [v for v in values if v not in (None, "")]
        missing = total - len(present)
        unique = len({str(v) for v in present})
        dtype = _dtype_label(present, numeric)

        meta = dtype
        tip = [f"{dtype}", f"{unique:,} unique", f"{len(present):,} / {total:,} filled"]
        if missing:
            pct = missing / total * 100 if total else 0
            meta += f' · <span class="pc-th-null">{_pct(pct)}% null</span>'
            tip.append(f"{missing:,} null")

        # For mixed columns show a per-type breakdown so the user can see the
        # composition at a glance (e.g. "3 int · 2 str · 1 bool").
        if dtype == "mixed":
            counts = _type_counts(values)  # includes nulls
            parts = " · ".join(f"{n} {t}" for t, n in counts)
            meta += f" <span class=\"pc-th-type-mix\">({parts})</span>"
            tip.append(parts)

        if numeric:
            nums = []
            for v in present:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    pass
            if nums:
                srt = sorted(nums)
                n = len(srt)
                mean = sum(srt) / n
                median = (srt[n // 2] if n % 2
                          else (srt[n // 2 - 1] + srt[n // 2]) / 2)
                tip.append(f"min {srt[0]:g}  max {srt[-1]:g}")
                tip.append(f"mean {mean:g}  median {median:g}")
        return {"meta": meta, "tip": "  ·  ".join(tip)}
    except Exception:
        return {"meta": "", "tip": ""}


def _dtype_label(present, numeric):
    """Infer a short dtype label from a column's non-empty values."""
    if not present:
        return "empty"
    if all(isinstance(v, bool) for v in present):
        return "bool"
    if numeric:
        allint = True
        for v in present:
            try:
                f = float(v)
                if f != int(f):
                    allint = False
            except (TypeError, ValueError):
                return "mixed"
        return "int" if allint else "float"
    types = {type(v).__name__ for v in present}
    return "str" if types <= {"str"} else "mixed"


def _type_counts(values):
    """Count values by their Python type, using readable short names.

    Returns an ordered list of ``(label, count)`` pairs sorted by count
    descending, covering only types that are present.
    """
    buckets: dict[str, int] = {}
    for v in values:
        if v is None:
            label = "null"
        elif isinstance(v, bool):
            label = "bool"
        elif isinstance(v, int):
            label = "int"
        elif isinstance(v, float):
            label = "float"
        elif isinstance(v, str):
            label = "str"
        elif isinstance(v, (list, tuple)):
            label = "list"
        elif isinstance(v, dict):
            label = "dict"
        else:
            label = type(v).__name__
        buckets[label] = buckets.get(label, 0) + 1
    return sorted(buckets.items(), key=lambda x: -x[1])


# -- per-column distribution data (rendered as inline SVG in the panel) -------
def _distribution(values, numeric):
    """A compact summary of one column's values for the React panel to draw.

    Numeric columns get a histogram (with a min/mean/max caption); everything
    else a top-values bar chart. Returns ``{num, bars, cap}`` where each bar has
    its height (0..1), a hover ``title`` (count + share), and the predicate a
    click turns into a column filter (``lo``/``hi`` for bins, ``val`` for
    categories). ``None`` on any failure, so an odd column never breaks the table.
    """
    try:
        if numeric:
            return _numeric_hist(values)
        return _category_bars(values)
    except Exception:
        return None


def _numeric_hist(values, bins=12):
    nums = []
    for v in values:
        if v in (None, ""):
            continue
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            pass
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    total = len(nums)
    mean = sum(nums) / total
    if hi == lo:
        return {"num": True, "cap": [f"{lo:g}"],
                "bars": [{"h": 1.0, "lo": f"{lo:g}", "hi": f"{hi:g}",
                          "title": f"{lo:g}: {total} (100%)"}]}
    counts = [0] * bins
    span = hi - lo
    for v in nums:
        counts[min(int((v - lo) / span * bins), bins - 1)] += 1
    mx = max(counts) or 1
    bars = []
    for i in range(bins):
        blo = lo + span * i / bins
        bhi = lo + span * (i + 1) / bins
        c = counts[i]
        bars.append({"h": c / mx, "lo": f"{blo:g}", "hi": f"{bhi:g}",
                     "title": f"{blo:g} – {bhi:g}: {c} ({_pct(c / total * 100)}%)"})
    # Caption: min (left), mean (centre), max (right) — the histogram's x-axis
    # ends are the min/max, with the mean called out between them.
    return {"num": True, "bars": bars,
            "cap": [f"{lo:g}", f"μ {mean:g}", f"{hi:g}"]}


def _category_bars(values, top=8):
    counts = Counter(str(v) for v in values if v not in (None, ""))
    if not counts:
        return None
    total = sum(counts.values())
    common = counts.most_common(top)
    mx = common[0][1] or 1
    bars = [{"h": c / mx, "val": str(label),
             "title": f"{label}: {c} ({_pct(c / total * 100)}%)"}
            for label, c in common]
    return {"num": False, "bars": bars, "cap": [f"{len(counts):,} unique"]}