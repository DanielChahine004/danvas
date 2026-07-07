
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
  // Programmatic selection: Python pushed `selected` (table.selected = [...]).
  // Applied silently — no echo back — mirroring how a Python push of a value
  // never re-fires on_change.
  React.useEffect(() => {
    if (props.selected == null) return;
    setSelectedRows(new Set(props.selected));
    if (props.selected.length) setShowSel(true);
  }, [props.selected]);
  const selAllRef = React.useRef(null);
  const editable = !!props.editable;
  const [editMode, setEditMode] = React.useState(false);
  const [editCell, setEditCell] = React.useState(null);  // {ri, ci}
  const [editVal, setEditVal] = React.useState("");
  const editRef = React.useRef(null);

  // One lowercased haystack per row for the free-text filter. The \u0001
  // separator keeps a query from matching across a cell boundary.
  const hay = React.useMemo(
    () => rows.map((r) => r.join("\u0001").toLowerCase()), [rows]);

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
          label: name + " \u2208 [" + bar.lo + ", " + bar.hi + "]" }
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
        <input className="pc-filter" placeholder="filter rows\u2026" value={q}
               onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <button className={"pc-btn" + (showIdx ? " on" : "")} title="show row index"
                onClick={() => setShowIdx((v) => !v)}>#</button>
        <button className={"pc-btn" + (showSel ? " on" : "")} title="row selection"
                onClick={() => setShowSel((v) => !v)}>sel</button>
        {editable
          ? <button className={"pc-btn" + (editMode ? " on" : "")} title="toggle cell editing"
                    onClick={() => setEditMode((v) => !v)}>{"\u270e"}</button>
          : null}
        <div className="pc-col-wrap">
          <button className={"pc-btn" + (hiddenCols.size ? " on" : "")} title="show/hide columns"
                  onClick={() => setColMenuOpen((v) => !v)}>cols {"\u25be"}</button>
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
              {colFilter.label + "  \u2715"}
            </button>
          : null}
        {showSel && selectedRows.size > 0
          ? <button className="pc-btn pc-chip on" onClick={clearSelection}>
              {selectedRows.size + " selected  \u2715"}
            </button>
          : null}
        <span className="pc-count">{count}</span>
        {npages > 1
          ? <div className="pc-pager">
              <button className="pc-pg" title="previous page"
                      onClick={() => gotoPage(pg - 1)}>{"\u2039"}</button>
              <input className="pc-page" type="number" min={1} max={npages} value={pg}
                     title="page \u2014 type a number or use the up/down arrows"
                     onChange={(e) => gotoPage(parseInt(e.target.value, 10))} />
              <span className="pc-pages">{"/ " + npages.toLocaleString()}</span>
              <button className="pc-pg" title="next page"
                      onClick={() => gotoPage(pg + 1)}>{"\u203a"}</button>
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
                    {sortCol === i ? (sortDir === 1 ? "\u25B2" : sortDir === -1 ? "\u25BC" : "") : ""}
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
