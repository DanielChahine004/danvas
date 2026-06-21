"""Live leaderboard with role-based access.

Admin (password "admin"):  sees the entry form + the leaderboard.
Viewer (password "view"):  sees the leaderboard only (read-only).

Both views live on the same port — which password you type determines what you see.
"""

import danvas

canvas = danvas.Canvas()

scores = {}  # team -> points


def scores_prop():
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [{"name": n, "points": p} for n, p in ranked]


_FORM_SOURCE = """
function Component({ canvas, props }) {
  const [name, setName] = React.useState("");
  const [pts,  setPts]  = React.useState("");
  const [msg,  setMsg]  = React.useState("");

  function submit() {
    const trimmed = name.trim();
    const n = parseInt(pts, 10);
    if (!trimmed) { setMsg("Enter a team name."); return; }
    if (isNaN(n))  { setMsg("Points must be a number."); return; }
    canvas.send({ name: trimmed, points: n });
    setMsg("✓ " + trimmed + " → " + n + " pts");
    setName(""); setPts("");
  }

  function onKey(e) { if (e.key === "Enter") submit(); }

  const inp = {
    padding:"6px 10px", borderRadius:"6px",
    border:"1px solid var(--pc-border,#30363d)",
    background:"var(--pc-surface,#1b2230)",
    color:"inherit", fontSize:"13px", width:"100%", boxSizing:"border-box",
  };

  return (
    <div style={{padding:"16px",display:"flex",flexDirection:"column",gap:"10px",
                 fontFamily:"system-ui,sans-serif",color:"var(--pc-text,#e6edf3)"}}>
      <div style={{fontWeight:700,fontSize:"15px"}}>Add / update team</div>
      <input placeholder="Team name" value={name} style={inp}
             onChange={e => setName(e.target.value)} onKeyDown={onKey} />
      <input placeholder="Points" type="number" value={pts} style={inp}
             onChange={e => setPts(e.target.value)} onKeyDown={onKey} />
      <button onClick={submit}
              style={{padding:"7px 16px",borderRadius:"6px",fontWeight:600,
                      cursor:"pointer",background:"#2563eb",color:"#fff",
                      border:"none",fontSize:"13px"}}>
        Submit
      </button>
      {msg && <div style={{fontSize:"12px",color:"#94a3b8"}}>{msg}</div>}
    </div>
  );
}
"""

_BOARD_SOURCE = """
function Component({ props }) {
  const rows = props.rows || [];

  const MEDAL = ["🥇","🥈","🥉"];
  const MEDAL_BG = ["#78350f","#334155","#431407"];  // gold / silver / bronze tints

  const styles = `
    .lb { width:100%; border-collapse:collapse; font-family:system-ui,sans-serif;
          font-size:13px; color:var(--pc-text,#e6edf3); }
    .lb th { padding:8px 12px; text-align:left; font-size:11px; font-weight:600;
             color:#64748b; border-bottom:1px solid var(--pc-border,#30363d);
             text-transform:uppercase; letter-spacing:.05em; }
    .lb td { padding:10px 12px; border-bottom:1px solid #1e293b; }
    .lb tr:last-child td { border-bottom:none; }
    .lb .pts { font-variant-numeric:tabular-nums; font-weight:700; text-align:right; }
    .lb .rank { width:36px; text-align:center; font-size:18px; }
    .lb .name { font-weight:500; }
    .medal-row td { font-weight:600; }
  `;

  return (
    <div style={{height:"100%",overflow:"auto",padding:"8px"}}>
      <style>{styles}</style>
      <div style={{fontWeight:700,fontSize:"15px",padding:"8px 4px 12px",
                   color:"var(--pc-text,#e6edf3)"}}>
        Leaderboard
      </div>
      {rows.length === 0
        ? <div style={{color:"#64748b",padding:"8px 4px",fontSize:"13px"}}>
            No teams yet — add one on the left.
          </div>
        : <table className="lb">
            <thead>
              <tr>
                <th className="rank">#</th>
                <th>Team</th>
                <th style={{textAlign:"right"}}>Points</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const medal = i < 3;
                return (
                  <tr key={row.name}
                      className={medal ? "medal-row" : ""}
                      style={medal ? {background: MEDAL_BG[i] + "55"} : {}}>
                    <td className="rank">{medal ? MEDAL[i] : i + 1}</td>
                    <td className="name">{row.name}</td>
                    <td className="pts">{row.points.toLocaleString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
      }
    </div>
  );
}
"""

form  = canvas.react(_FORM_SOURCE, name="form",  x=40,  y=40,  w=260, h=220,
                     roles=["admin"])   # only admin sees the entry form
board = canvas.react(_BOARD_SOURCE, name="board", x=320, y=40,  w=400, h=500,
                     props={"rows": []})  # visible to all roles


@form.on_message
def on_submit(msg, viewer):
    print(f"[{viewer['role']}] {viewer['name']} set {msg['name']} → {msg['points']}")
    scores[msg["name"]] = msg["points"]
    board.update(rows=scores_prop())


canvas.serve(
    port=8000,
    host="0.0.0.0",
    passwords={
        "admin":  "admin",
        "viewer": "view",
    },
    tunnel=True,  # open a public URL via danvas.app (for easy sharing and testing
)
