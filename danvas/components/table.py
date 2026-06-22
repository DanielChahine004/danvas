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
_TABLE_SOURCE = """
function Component({ canvas, props }) {
  const cols = props.cols || [];
  const numeric = props.numeric || [];
  const profiles = props.profiles || [];
  const dists = props.dists || [];
  const PAGE = props.pageSize || 2000;

  // Local copy of rows so cell edits are visible immediately without waiting
  // for a Python round-trip. Resets whenever Python pushes new props.
  const [rows, setRows] = React.useState(props.rows || []);
  React.useEffect(() => { setRows(props.rows || []); }, [props.rows]);
  const total = rows.length;

  const [sortCol, setSortCol] = React.useState(-1);
  const [sortDir, setSortDir] = React.useState(0);  // 1 asc, -1 desc, 0 none
  const [q, setQ] = React.useState("");
  const [colFilter, setColFilter] = React.useState(null);
  const [page, setPage] = React.useState(1);
  const [showIdx, setShowIdx] = React.useState(false);
  const [hiddenCols, setHiddenCols] = React.useState(new Set());
  const [colMenuOpen, setColMenuOpen] = React.useState(false);
  const [showSel, setShowSel] = React.useState(false);
  const [selectedRows, setSelectedRows] = React.useState(new Set());
  const selAllRef = React.useRef(null);
  const editable = !!props.editable;
  const [editMode, setEditMode] = React.useState(false);
  const [editCell, setEditCell] = React.useState(null);  // {ri, ci}
  const [editVal, setEditVal] = React.useState("");
  const editRef = React.useRef(null);

  // One lowercased haystack per row for the free-text filter. The \\u0001
  // separator keeps a query from matching across a cell boundary.
  const hay = React.useMemo(
    () => rows.map((r) => r.join("\\u0001").toLowerCase()), [rows]);

  // view: the row indices left after filtering, in sorted order. Recomputed only
  // when an input changes, then a page of it is rendered.
  const view = React.useMemo(() => {
    const ql = q.toLowerCase();
    const idx = [];
    for (let i = 0; i < total; i++) {
      if (ql && hay[i].indexOf(ql) < 0) continue;
      if (colFilter) {
        let t = rows[i][colFilter.col];
        if (t == null) t = "";
        if (colFilter.num) {
          const v = parseFloat(t);
          if (isNaN(v) || v < colFilter.lo || v > colFilter.hi) continue;
        } else if (t !== colFilter.val) continue;
      }
      idx.push(i);
    }
    if (sortCol >= 0 && sortDir !== 0) {
      const c = sortCol, num = numeric[c], dir = sortDir;
      idx.sort((a, b) => {
        let va = rows[a][c], vb = rows[b][c];
        if (num) {
          va = parseFloat(va); vb = parseFloat(vb);
          if (isNaN(va)) va = -Infinity;
          if (isNaN(vb)) vb = -Infinity;
        } else { va = ("" + va).toLowerCase(); vb = ("" + vb).toLowerCase(); }
        if (va < vb) return -dir;
        if (va > vb) return dir;
        return 0;
      });
    }
    return idx;
  }, [q, colFilter, sortCol, sortDir, rows, hay, numeric, total]);

  const npages = Math.max(1, Math.ceil(view.length / PAGE));
  const pg = Math.min(Math.max(1, page), npages);  // clamp for render
  const pageRows = view.slice((pg - 1) * PAGE, (pg - 1) * PAGE + PAGE);

  React.useEffect(() => {
    if (!selAllRef.current) return;
    const nSelVis = view.filter((ri) => selectedRows.has(ri)).length;
    selAllRef.current.indeterminate = nSelVis > 0 && nSelVis < view.length;
  }, [selectedRows, view]);

  function clickHeader(i) {
    if (sortCol !== i) { setSortCol(i); setSortDir(1); return; }
    const nd = sortDir === 1 ? -1 : (sortDir === -1 ? 0 : 1);
    setSortDir(nd);
    if (nd === 0) setSortCol(-1);
  }
  function pickBar(c, bar, num) {
    const name = cols[c];
    const cf = num
      ? { col: c, num: true, lo: Number(bar.lo), hi: Number(bar.hi),
          label: name + " \\u2208 [" + bar.lo + ", " + bar.hi + "]" }
      : { col: c, num: false, val: bar.val, label: name + " = " + bar.val };
    setColFilter((p) => (p && p.col === c && p.label === cf.label) ? null : cf);
    setPage(1);
  }
  function barSelected(c, bar, num) {
    if (!colFilter || colFilter.col !== c) return false;
    return num
      ? (colFilter.num && colFilter.lo === Number(bar.lo) && colFilter.hi === Number(bar.hi))
      : (!colFilter.num && colFilter.val === bar.val);
  }
  function gotoPage(v) { if (!isNaN(v)) setPage(Math.min(npages, Math.max(1, v))); }
  function toggleCol(i) {
    setHiddenCols((prev) => {
      const s = new Set(prev);
      s.has(i) ? s.delete(i) : s.add(i);
      return s;
    });
  }
  const visCols = cols.map((_, i) => i).filter((i) => !hiddenCols.has(i));
  function toggleRow(ri) {
    const s = new Set(selectedRows);
    s.has(ri) ? s.delete(ri) : s.add(ri);
    setSelectedRows(s);
    canvas.send({ selected: [...s] });
  }
  function toggleAllVisible() {
    const nSelVis = view.filter((ri) => selectedRows.has(ri)).length;
    const s = new Set(selectedRows);
    if (nSelVis === view.length) { view.forEach((ri) => s.delete(ri)); }
    else { view.forEach((ri) => s.add(ri)); }
    setSelectedRows(s);
    canvas.send({ selected: [...s] });
  }
  function clearSelection() { setSelectedRows(new Set()); canvas.send({ selected: [] }); }

  function startEdit(ri, ci) {
    if (!editMode) return;
    setEditCell({ ri, ci });
    setEditVal(rows[ri][ci] == null ? "" : rows[ri][ci]);
    setTimeout(() => editRef.current && editRef.current.select(), 0);
  }
  function commitEdit() {
    if (!editCell) return;
    const { ri, ci } = editCell;
    setRows((prev) => {
      const next = prev.map((r) => r.slice());
      next[ri][ci] = editVal;
      return next;
    });
    canvas.send({ edited: { row: ri, col: ci, value: editVal } });
    setEditCell(null);
  }
  function cancelEdit() { setEditCell(null); }
  React.useEffect(() => { if (!editMode) setEditCell(null); }, [editMode]);

  function spark(c, dist) {
    if (!dist || !dist.bars || !dist.bars.length) return null;
    const n = dist.bars.length, bw = 120 / n;
    return (
      <svg className="pc-spark" viewBox="0 0 120 28" preserveAspectRatio="none">
        {dist.bars.map((bar, i) => {
          const bh = Math.max(1, bar.h * 26);
          return (
            <rect key={i} x={i * bw} y={28 - bh} width={Math.max(1, bw - 1)} height={bh}
                  className={barSelected(c, bar, dist.num) ? "pc-sel" : ""}
                  onClick={() => pickBar(c, bar, dist.num)}>
              <title>{bar.title}</title>
            </rect>
          );
        })}
      </svg>
    );
  }

  const count = view.length === total
    ? total.toLocaleString() + " rows"
    : view.length.toLocaleString() + " / " + total.toLocaleString();

  return (
    <div className={"pc-tbl" + (editMode ? " pc-editable" : "")}>
      <style>{`__CSS__`}</style>
      <div className="pc-bar">
        <input className="pc-filter" placeholder="filter rows\\u2026" value={q}
               onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <button className={"pc-btn" + (showIdx ? " on" : "")} title="show row index"
                onClick={() => setShowIdx((v) => !v)}>#</button>
        <button className={"pc-btn" + (showSel ? " on" : "")} title="row selection"
                onClick={() => setShowSel((v) => !v)}>sel</button>
        {editable
          ? <button className={"pc-btn" + (editMode ? " on" : "")} title="toggle cell editing"
                    onClick={() => setEditMode((v) => !v)}>{"\\u270e"}</button>
          : null}
        <div className="pc-col-wrap">
          <button className={"pc-btn" + (hiddenCols.size ? " on" : "")} title="show/hide columns"
                  onClick={() => setColMenuOpen((v) => !v)}>cols {"\\u25be"}</button>
          {colMenuOpen && (
            <>
              <div className="pc-overlay" onClick={() => setColMenuOpen(false)} />
              <div className="pc-col-menu">
                {cols.map((c, i) => (
                  <label key={i} className="pc-col-item">
                    <input type="checkbox" checked={!hiddenCols.has(i)}
                           disabled={!hiddenCols.has(i) && visCols.length === 1}
                           onChange={() => toggleCol(i)} />
                    {c || ("col " + i)}
                  </label>
                ))}
              </div>
            </>
          )}
        </div>
        {colFilter
          ? <button className="pc-btn pc-chip on" onClick={() => { setColFilter(null); setPage(1); }}>
              {colFilter.label + "  \\u2715"}
            </button>
          : null}
        {showSel && selectedRows.size > 0
          ? <button className="pc-btn pc-chip on" onClick={clearSelection}>
              {selectedRows.size + " selected  \\u2715"}
            </button>
          : null}
        <span className="pc-count">{count}</span>
        {npages > 1
          ? <div className="pc-pager">
              <button className="pc-pg" title="previous page"
                      onClick={() => gotoPage(pg - 1)}>{"\\u2039"}</button>
              <input className="pc-page" type="number" min={1} max={npages} value={pg}
                     title="page \\u2014 type a number or use the up/down arrows"
                     onChange={(e) => gotoPage(parseInt(e.target.value, 10))} />
              <span className="pc-pages">{"/ " + npages.toLocaleString()}</span>
              <button className="pc-pg" title="next page"
                      onClick={() => gotoPage(pg + 1)}>{"\\u203a"}</button>
            </div>
          : null}
      </div>
      <div className="pc-scroll">
        <table>
          <thead>
            <tr className="pc-head">
              {showSel
                ? <th className="pc-sel-col">
                    <input type="checkbox" ref={selAllRef}
                           checked={view.length > 0 && view.every((ri) => selectedRows.has(ri))}
                           onChange={toggleAllVisible} />
                  </th>
                : null}
              {showIdx
                ? <th className="pc-idx" title="row index (0-based)"
                      onClick={() => { setSortCol(-1); setSortDir(0); }}>#</th>
                : null}
              {visCols.map((i) => (
                <th key={i} data-num={numeric[i] ? 1 : 0}
                    title={profiles[i] ? profiles[i].tip : ""}
                    onClick={() => clickHeader(i)}>
                  {cols[i]}
                  <span className="pc-arrow">
                    {sortCol === i ? (sortDir === 1 ? "\\u25B2" : sortDir === -1 ? "\\u25BC" : "") : ""}
                  </span>
                  <div className="pc-th-meta"
                       dangerouslySetInnerHTML={{ __html: profiles[i] ? profiles[i].meta : "" }} />
                </th>
              ))}
            </tr>
            <tr className="pc-dist">
              {showSel ? <th className="pc-sel-col"></th> : null}
              {showIdx ? <th className="pc-idx"></th> : null}
              {visCols.map((i) => (
                <th key={i}>
                  {spark(i, dists[i])}
                  {dists[i] && dists[i].cap
                    ? <div className="pc-cap">
                        {dists[i].cap.map((s, k) => <span key={k}>{s}</span>)}
                      </div>
                    : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((ri) => (
              <tr key={ri}>
                {showSel
                  ? <td className="pc-sel-col">
                      <input type="checkbox" checked={selectedRows.has(ri)}
                             onChange={() => toggleRow(ri)} />
                    </td>
                  : null}
                {showIdx ? <td className="pc-idx">{ri}</td> : null}
                {rows[ri].map((cell, ci) => {
                  if (hiddenCols.has(ci)) return null;
                  const isEditing = editCell && editCell.ri === ri && editCell.ci === ci;
                  return (
                    <td key={ci} className={isEditing ? "pc-editing" : ""}
                        onClick={() => startEdit(ri, ci)}>
                      {isEditing
                        ? <input ref={editRef} value={editVal}
                            onChange={(e) => setEditVal(e.target.value)}
                            onBlur={commitEdit}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
                              if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
                            }} />
                        : (cell == null ? "" : cell)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
""".replace("__CSS__", _TABLE_CSS)


class Table(React):
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

    @property
    def selected(self):
        """The 0-based indices of currently selected rows in the original data."""
        with self._lock:
            return list(self._selected)

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
        # int if every value is a whole number (after coercing strings).
        allint = True
        for v in present:
            try:
                if float(v) != int(float(v)):
                    allint = False
                    break
            except (TypeError, ValueError):
                allint = False
                break
        return "int" if allint else "float"
    types = {type(v).__name__ for v in present}
    return "str" if types <= {"str"} else "mixed"


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