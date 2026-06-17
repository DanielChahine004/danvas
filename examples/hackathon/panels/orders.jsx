const STATUS = { pending:["pending","st-pend"], fulfilled:["fulfilled","st-ok"],
                 rejected:["not fulfilled","st-no"], retracted:["retracted","st-ret"] };
const COLS = [
  { key:"id",     label:"#",      num:true,  get:o=>o.id },
  { key:"team",   label:"Team",   num:false, get:o=>o.team },
  { key:"item",   label:"Item",   num:false, get:o=>o.item },
  { key:"qty",    label:"Qty",    num:true,  get:o=>o.qty },
  { key:"cost",   label:"Cost",   num:true,  get:o=>o.price * o.qty },
  { key:"status", label:"Status", num:false, get:o=>o.status },
];
const FILTERS = [["all","All"],["pending","Pending"],["fulfilled","Fulfilled"],["rejected","Not fulfilled"]];

function Component({ canvas, props }) {
  const log = props.log || [];

  const [q, setQ] = React.useState("");
  const [status, setStatus] = React.useState("all");
  const [sortKey, setSortKey] = React.useState("id");
  const [sortDir, setSortDir] = React.useState(-1);  // newest first by default
  const [editId, setEditId] = React.useState(null);
  const [editQ, setEditQ] = React.useState("");

  const counts = React.useMemo(() => {
    const c = { all: log.length, pending:0, fulfilled:0, rejected:0 };
    for (const o of log) c[o.status] = (c[o.status] || 0) + 1;
    return c;
  }, [log]);

  const view = React.useMemo(() => {
    const ql = q.trim().toLowerCase();
    let rows = log.filter(o => {
      if (status !== "all" && o.status !== status) return false;
      if (ql && (o.team + " " + o.item).toLowerCase().indexOf(ql) < 0) return false;
      return true;
    });
    const col = COLS.find(c => c.key === sortKey);
    if (col) {
      rows = rows.slice().sort((a, b) => {
        let va = col.get(a), vb = col.get(b);
        if (col.num) { va = +va; vb = +vb; }
        else { va = ("" + va).toLowerCase(); vb = ("" + vb).toLowerCase(); }
        if (va < vb) return -sortDir;
        if (va > vb) return sortDir;
        return 0;
      });
    }
    return rows;
  }, [log, q, status, sortKey, sortDir]);

  function clickHeader(key) {
    if (sortKey !== key) { setSortKey(key); setSortDir(1); }
    else setSortDir(d => -d);
  }
  function startEdit(o) { setEditId(o.id); setEditQ(String(o.qty)); }
  function saveEdit(o) {
    const n = parseInt(editQ, 10);
    if (!isNaN(n) && n > 0 && n !== o.qty) canvas.send({ action:"order_edit", id:o.id, qty:n });
    setEditId(null);
  }

  return (
    <div className="pc-ord">
      <div className="hd">📋 Orders</div>

      <div className="bar">
        <input className="filter" placeholder="Filter by team or item…" value={q}
          onChange={e=>setQ(e.target.value)} />
        {FILTERS.map(([key, label]) => (
          <button key={key} className={"chip" + (status===key ? " on" : "")}
            onClick={()=>setStatus(key)}>{label} ({counts[key] || 0})</button>
        ))}
      </div>

      <div className="scroll">
        <table>
          <thead>
            <tr>
              {COLS.map(c => (
                <th key={c.key} onClick={()=>clickHeader(c.key)}>
                  {c.label}<span className="arr">{sortKey===c.key ? (sortDir===1 ? " ▲" : " ▼") : ""}</span>
                </th>
              ))}
              <th className="acth">Actions</th>
            </tr>
          </thead>
          <tbody>
            {view.length === 0
              ? <tr><td className="empty" colSpan={COLS.length + 1}>No orders match.</td></tr>
              : view.map(o => {
                  const [label, cls] = STATUS[o.status] || STATUS.pending;
                  return (
                    <tr key={o.id}>
                      <td className="rt">{o.id}</td>
                      <td>{o.team}</td>
                      <td>{o.item}</td>
                      <td className="rt">
                        {editId === o.id
                          ? <input className="qedit" type="number" min="1" value={editQ} autoFocus
                              onChange={e=>setEditQ(e.target.value)} onBlur={()=>saveEdit(o)}
                              onKeyDown={e=>{ if(e.key==="Enter") saveEdit(o); if(e.key==="Escape") setEditId(null); }} />
                          : o.qty}
                      </td>
                      <td className="rt">${(o.price * o.qty).toLocaleString()}</td>
                      <td><span className={"badge " + cls}>{label}</span></td>
                      <td className="acts">
                        {o.status === "pending" &&
                          <button className="act ed" title="Edit quantity" onClick={()=>startEdit(o)}>✎</button>}
                        {(o.status === "pending" || o.status === "rejected") &&
                          <button className="act ok" title="Fulfil — deducts stock"
                            onClick={()=>canvas.send({ action:"order_fulfil", id:o.id })}>✓</button>}
                        {(o.status === "pending" || o.status === "fulfilled") &&
                          <button className="act no" title="Mark not fulfilled — refunds budget"
                            onClick={()=>canvas.send({ action:"order_reject", id:o.id })}>×</button>}
                      </td>
                    </tr>
                  );
                })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
