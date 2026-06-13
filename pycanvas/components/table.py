"""Table: show tabular data as an interactive panel.

Accepts a pandas DataFrame/Series, a list of dicts, a list of rows
(lists/tuples), or a dict of columns. pandas is duck-typed, so it isn't a hard
dependency. The rendered panel is interactive in the browser:

- **column profile** under each header — the inferred dtype (``int``, ``float``,
  ``bool``, ``str``, ``mixed``) and a red ``n% null`` badge when values are
  missing; hover the header for fuller stats (unique count, min/max/mean/median);
- **click a header** to sort by that column (cycles ascending → descending →
  original order); numeric columns sort numerically, everything else by text;
- **filter box** hides rows that don't contain the typed text;
- **distributions toggle** reveals a per-column mini-chart under each header — a
  histogram for numeric columns, a top-values bar chart for categorical ones —
  computed in Python and drawn as inline SVG. **Click a bar** to filter the table
  to that bin (numeric) or category (text); a chip in the toolbar shows the
  active filter and clears it on click.

Everything runs inside the sandboxed ``Custom`` iframe with no extra
dependencies; ``update(data)`` re-renders with fresh data.
"""

import html as _html
from collections import Counter

from .custom import Custom
from ._doc import document

# Beyond this, only the first MAX_ROWS are rendered (the panel stays responsive);
# distributions are still computed over the full data so they stay accurate.
MAX_ROWS = 2000

_TABLE_CSS = (
    "body{margin:0;padding:0}"
    ".pc-wrap{display:flex;flex-direction:column;height:100vh;font-size:12px}"
    ".pc-bar{display:flex;align-items:center;gap:8px;padding:6px 8px;"
    "border-bottom:1px solid #e2e8f0;flex:none}"
    ".pc-filter{flex:1;min-width:60px;padding:3px 6px;border:1px solid #cbd5e1;"
    "border-radius:4px;font-size:12px}"
    ".pc-btn{padding:3px 8px;border:1px solid #cbd5e1;border-radius:4px;"
    "background:#f8fafc;cursor:pointer;font-size:11px;color:#334155}"
    ".pc-btn.on{background:#2563eb;color:#fff;border-color:#2563eb}"
    ".pc-count{color:#64748b;font-size:11px;white-space:nowrap}"
    ".pc-scroll{overflow:auto;flex:1}"
    # Auto-height (h="auto"): size the table to its content instead of pinning it
    # to the panel height — the wrap stops filling 100vh and the scroll area
    # grows naturally, so the measured height converges (no fit-loop flutter).
    "body.pc-auto-h .pc-wrap{height:auto}"
    "body.pc-auto-h .pc-scroll{overflow:visible;flex:none}"
    "table{border-collapse:collapse;width:100%}"
    "th,td{border:1px solid #e2e8f0;padding:4px 8px;text-align:left;"
    "white-space:nowrap}"
    "thead th{position:sticky;top:0;background:#f8fafc;font-weight:600;"
    "cursor:pointer;user-select:none}"
    ".pc-th-meta{font-weight:400;color:#94a3b8;font-size:10px;margin-top:1px;"
    "white-space:nowrap}"
    ".pc-th-null{color:#e11d48}"
    "thead tr.pc-dist th{position:static;background:#fff;cursor:default;"
    "font-weight:400;padding:3px 6px;vertical-align:bottom}"
    "tr:nth-child(even) td{background:#f8fafc}"
    "td{font-variant-numeric:tabular-nums}"
    ".pc-arrow{color:#94a3b8;font-size:10px;margin-left:3px}"
    ".pc-spark{width:100%;height:28px;display:block}"
    ".pc-spark rect{fill:#60a5fa;cursor:pointer}"
    ".pc-spark rect:hover{fill:#3b82f6}"
    ".pc-spark rect.pc-sel{fill:#1d4ed8}"
    ".pc-cap{color:#94a3b8;font-size:9px;margin-top:1px;display:flex;"
    "justify-content:space-between;gap:6px}"
    ".pc-note{padding:4px 8px;color:#94a3b8;font-size:11px;flex:none}"
    ".pc-hidden{display:none}"
)

# Plain string (not an f-string) so the braces don't need escaping. Operates on
# the single table in the iframe: sort on header click, live filter, toggle the
# distribution row. Sorting reorders the tbody rows; the original order is kept
# so a third click on a column restores it.
_TABLE_JS = """
(function(){
  var tbl = document.querySelector('table'); if(!tbl) return;
  var tbody = tbl.tBodies[0];
  var rows = Array.prototype.slice.call(tbody.rows);
  var heads = Array.prototype.slice.call(tbl.querySelectorAll('thead tr.pc-head th'));
  var state = {col:-1, dir:0};
  function val(tr, i, num){
    var t = (tr.cells[i] ? tr.cells[i].textContent : '');
    return num ? parseFloat(t) : t.toLowerCase();
  }
  heads.forEach(function(th, i){
    th.addEventListener('click', function(){
      var num = th.getAttribute('data-num') === '1';
      if(state.col !== i){ state.col = i; state.dir = 1; }
      else { state.dir = state.dir === 1 ? -1 : (state.dir === -1 ? 0 : 1); }
      heads.forEach(function(h){ var a=h.querySelector('.pc-arrow'); if(a) a.textContent=''; });
      if(state.dir === 0){ state.col = -1; rows.forEach(function(r){ tbody.appendChild(r); }); return; }
      var arrow = th.querySelector('.pc-arrow'); if(arrow) arrow.textContent = state.dir===1?'\\u25B2':'\\u25BC';
      var s = rows.slice().sort(function(a,b){
        var va=val(a,i,num), vb=val(b,i,num);
        if(num){ if(isNaN(va)) va=-Infinity; if(isNaN(vb)) vb=-Infinity; }
        if(va<vb) return -state.dir; if(va>vb) return state.dir; return 0;
      });
      s.forEach(function(r){ tbody.appendChild(r); });
    });
  });
  var filter = document.querySelector('.pc-filter');
  var count = document.querySelector('.pc-count');
  var chip = document.querySelector('.pc-chip');
  var total = rows.length;
  function setCount(v){ count.textContent = (v===total ? total+' rows' : v+' / '+total); }
  // colFilter: an optional column-aware predicate set by clicking a dist bar.
  // null when inactive; otherwise {col, num, lo, hi} or {col, val}.
  var colFilter = null, selRect = null;
  function apply(){
    var q = filter ? filter.value.toLowerCase() : '', vis = 0;
    rows.forEach(function(r){
      var show = !q || r.textContent.toLowerCase().indexOf(q) >= 0;
      if(show && colFilter){
        var cell = r.cells[colFilter.col];
        var t = cell ? cell.textContent : '';
        if(colFilter.num){
          var v = parseFloat(t);
          show = !isNaN(v) && v >= colFilter.lo && v <= colFilter.hi;
        } else { show = (t === colFilter.val); }
      }
      r.classList.toggle('pc-hidden', !show); if(show) vis++;
    });
    setCount(vis);
  }
  function clearColFilter(){
    colFilter = null;
    if(selRect){ selRect.classList.remove('pc-sel'); selRect = null; }
    if(chip){ chip.classList.add('pc-hidden'); chip.classList.remove('on'); }
    apply();
  }
  if(filter) filter.addEventListener('input', apply);
  if(chip) chip.addEventListener('click', clearColFilter);
  // Click a distribution bar to filter the column to that bin / category.
  Array.prototype.forEach.call(tbl.querySelectorAll('.pc-spark rect'), function(rect){
    if(rect.getAttribute('data-col') === null) return;
    rect.addEventListener('click', function(){
      if(selRect === rect){ clearColFilter(); return; }
      if(selRect) selRect.classList.remove('pc-sel');
      selRect = rect; rect.classList.add('pc-sel');
      var col = +rect.getAttribute('data-col');
      var nm = rect.getAttribute('data-name') || ('col ' + col);
      var label;
      if(rect.getAttribute('data-num') === '1'){
        var lo = parseFloat(rect.getAttribute('data-lo'));
        var hi = parseFloat(rect.getAttribute('data-hi'));
        colFilter = {col:col, num:true, lo:lo, hi:hi};
        label = nm + ' \\u2208 [' + rect.getAttribute('data-lo') + ', ' + rect.getAttribute('data-hi') + ']';
      } else {
        var val = rect.getAttribute('data-val');
        colFilter = {col:col, num:false, val:val};
        label = nm + ' = ' + val;
      }
      if(chip){ chip.textContent = label + '  \\u2715'; chip.classList.remove('pc-hidden'); chip.classList.add('on'); }
      apply();
    });
  });
  setCount(total);
  var distRow = tbl.querySelector('thead tr.pc-dist');
  var distBtn = document.querySelector('.pc-dist');
  if(distBtn && distRow){
    distBtn.addEventListener('click', function(){
      var hidden = distRow.classList.toggle('pc-hidden');
      distBtn.classList.toggle('on', !hidden);
    });
  }
})();
"""


class Table(Custom):
    component = "Custom"
    default_w = 520
    default_h = 360

    def __init__(self, data, name="table", label=None, w=None, h=None):
        super().__init__(html=self._render(data), name=name, label=label,
                         w=w, h=h)

    def update(self, data):
        """Replace the table contents, live."""
        super().update(self._render(data))

    def _render(self, data):
        cols, rows = _normalize(data)
        return document(_table_body(cols, rows), _TABLE_CSS)


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


# -- rendering ---------------------------------------------------------------
def _cell(v):
    return _html.escape("" if v is None else str(v))


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


def _column_profile(values, numeric):
    """Summarize one column for the header: a compact ``meta`` line shown under
    the column name and a fuller ``tip`` string surfaced on hover.

    ``meta`` carries the inferred dtype plus a missing-value badge; ``tip`` adds
    unique counts and (for numeric columns) min/max/mean/median. Never raises —
    a single odd column shouldn't break the header.
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
            shown = f"{pct:.0f}" if pct >= 1 else f"{pct:.1f}"
            meta += f' · <span class="pc-th-null">{shown}% null</span>'
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


def _table_body(cols, rows):
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
    head_cells = "".join(
        f'<th data-num="{1 if numeric[i] else 0}" title="{_cell(profiles[i]["tip"])}">'
        f'{_cell(c)}<span class="pc-arrow"></span>'
        f'<div class="pc-th-meta">{profiles[i]["meta"]}</div></th>'
        for i, c in enumerate(cols)
    )
    dist_cells = "".join(
        f"<th>{_distribution([r[i] for r in rows if i < len(r)], numeric[i], i, cols[i])}</th>"
        for i in range(ncol)
    )

    shown = rows[:MAX_ROWS]
    body_rows = "".join(
        "<tr>" + "".join(
            f"<td>{_cell(r[i]) if i < len(r) else ''}</td>" for i in range(ncol)
        ) + "</tr>"
        for r in shown
    )
    note = (f'<div class="pc-note">showing first {MAX_ROWS:,} of '
            f'{len(rows):,} rows</div>' if len(rows) > MAX_ROWS else "")

    return (
        '<div class="pc-wrap">'
        '<div class="pc-bar">'
        '<input class="pc-filter" placeholder="filter rows…">'
        '<button class="pc-btn pc-dist">distributions</button>'
        '<button class="pc-btn pc-chip pc-hidden"></button>'
        '<span class="pc-count"></span>'
        '</div>'
        + note +
        '<div class="pc-scroll"><table>'
        f'<thead><tr class="pc-head">{head_cells}</tr>'
        f'<tr class="pc-dist pc-hidden">{dist_cells}</tr></thead>'
        f'<tbody>{body_rows}</tbody>'
        '</table></div>'
        '</div>'
        f'<script>{_TABLE_JS}</script>'
    )


# -- per-column distribution charts (inline SVG) -----------------------------
def _distribution(values, numeric, col, name):
    """A compact SVG chart summarizing one column's values.

    Numeric columns get a histogram; everything else a top-values bar chart.
    ``col``/``name`` are threaded onto each bar as data attributes so the
    frontend can turn a bar click into a column-aware filter. Returns an empty
    string on any failure so a single odd column never breaks the whole table.
    """
    try:
        if numeric:
            return _numeric_hist(values, col, name)
        return _category_bars(values, col, name)
    except Exception:
        return ""


def _bars_svg(heights, titles, width=120, height=28, attrs=None):
    """Render bar heights (0..1) as a stretch-to-fit SVG, one ``<title>`` each.

    ``attrs[i]`` is an optional pre-built attribute string (``data-*`` etc.)
    appended to bar ``i``'s ``<rect>``, carrying the filter predicate for clicks.
    """
    n = len(heights) or 1
    bw = width / n
    rects = []
    for i, hgt in enumerate(heights):
        bh = max(1.0, hgt * (height - 2))
        x = i * bw
        title = f"<title>{_cell(titles[i])}</title>" if i < len(titles) else ""
        attr = f" {attrs[i]}" if attrs and i < len(attrs) else ""
        rects.append(
            f'<rect x="{x:.1f}" y="{height - bh:.1f}" width="{max(1.0, bw - 1):.1f}" '
            f'height="{bh:.1f}"{attr}>{title}</rect>'
        )
    return (f'<svg class="pc-spark" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none">{"".join(rects)}</svg>')


def _numeric_hist(values, col, name, bins=12):
    nums = []
    for v in values:
        if v in (None, ""):
            continue
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            pass
    if not nums:
        return ""
    nm = _cell(name)
    lo, hi = min(nums), max(nums)
    if hi == lo:
        attrs = [f'data-col="{col}" data-num="1" data-lo="{lo:g}" data-hi="{hi:g}" '
                 f'data-name="{nm}"']
        svg = _bars_svg([1.0], [f"{lo:g} × {len(nums)}"], attrs=attrs)
        return svg + f'<div class="pc-cap"><span>{lo:g}</span></div>'
    counts = [0] * bins
    span = hi - lo
    for v in nums:
        idx = int((v - lo) / span * bins)
        counts[min(idx, bins - 1)] += 1
    mx = max(counts) or 1
    heights = [c / mx for c in counts]
    titles = [str(c) for c in counts]
    # Each bin carries its [lo, hi] edges so a click filters rows to that range.
    attrs = []
    for i in range(bins):
        blo = lo + span * i / bins
        bhi = lo + span * (i + 1) / bins
        attrs.append(f'data-col="{col}" data-num="1" data-lo="{blo:g}" '
                     f'data-hi="{bhi:g}" data-name="{nm}"')
    cap = f'<div class="pc-cap"><span>{lo:g}</span><span>{hi:g}</span></div>'
    return _bars_svg(heights, titles, attrs=attrs) + cap


def _category_bars(values, col, name, top=8):
    counts = Counter(str(v) for v in values if v not in (None, ""))
    if not counts:
        return ""
    nm = _cell(name)
    common = counts.most_common(top)
    mx = common[0][1] or 1
    heights = [c / mx for _, c in common]
    titles = [f"{label}: {c}" for label, c in common]
    # Each bar carries its exact category value so a click filters to that value.
    attrs = [f'data-col="{col}" data-val="{_cell(label)}" data-name="{nm}"'
             for label, _ in common]
    cap = (f'<div class="pc-cap"><span>{len(counts):,} unique</span></div>')
    return _bars_svg(heights, titles, attrs=attrs) + cap
