"""Stock management system with role-based access.

Admin (password "admin"):  restocks items, monitors all user budgets and requests.
User  (password "user"):   browses live inventory, requests items from their budget.

Both roles see the inventory board in real time. The action panel on the right
is role-specific — admin and user panels sit at the same position so each role
sees the full canvas without gaps.
"""

import pycanvas

canvas = pycanvas.Canvas()

STARTING_BUDGET = 2000

inventory = {
    "Laptop":   {"stock": 5,  "price": 1200},
    "Monitor":  {"stock": 8,  "price":  350},
    "Keyboard": {"stock": 15, "price":   80},
    "Mouse":    {"stock": 20, "price":   45},
    "Headset":  {"stock": 10, "price":  150},
}

budgets = {}   # viewer name → remaining budget
req_log = []   # newest-first: {user, item, qty, cost}


def ensure_user(name):
    if name not in budgets:
        budgets[name] = STARTING_BUDGET
    return budgets[name]


def inv_rows():
    return [{"item": k, "stock": v["stock"], "price": v["price"]}
            for k, v in inventory.items()]


def user_rows():
    return [{"name": n, "budget": b} for n, b in budgets.items()]


def push_all():
    stock_board.update(rows=inv_rows())
    admin_panel.update(rows=inv_rows(), users=user_rows(), log=req_log[:20])
    user_panel.update(rows=inv_rows(), users=user_rows(), log=req_log[:10])


# ── Inventory board (all roles) ──────────────────────────────────────────────
# CSS is scoped under .pc-sb to avoid collisions with the user panel, which
# shares some class names (.irow, .ico, etc.) but with different styles.

_BOARD_CSS = """
.pc-sb{padding:16px;height:100%;overflow-y:auto;box-sizing:border-box;
       font-family:system-ui,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-sb .hd{font-size:16px;font-weight:700;margin-bottom:16px;
           display:flex;align-items:center;gap:8px}
.pc-sb .irow{display:flex;align-items:center;gap:12px;padding:10px 4px;
             border-bottom:1px solid var(--pc-border,#30363d);transition:opacity .2s}
.pc-sb .irow:last-child{border-bottom:none}
.pc-sb .irow.dim{opacity:0.4}
.pc-sb .ico{font-size:20px;width:36px;height:36px;border-radius:8px;flex-shrink:0;
            display:flex;align-items:center;justify-content:center;
            background:var(--pc-off-bg,#eee)33}
.pc-sb .iname{flex:1;font-size:13px;font-weight:600}
.pc-sb .iprice{font-size:13px;font-weight:700;color:var(--pc-muted,#666);
               font-variant-numeric:tabular-nums;min-width:58px;text-align:right}
.pc-sb .chip{padding:3px 9px;border-radius:999px;font-size:11px;font-weight:700}
.pc-sb .in {background:rgba(20,83,45,.35);color:#4ade80;border:1px solid rgba(74,222,128,.2)}
.pc-sb .low{background:rgba(120,53,15,.35);color:#fbbf24;border:1px solid rgba(251,191,36,.2)}
.pc-sb .out{background:rgba(127,29,29,.35);color:#f87171;border:1px solid rgba(248,113,113,.2)}
"""

_BOARD_SOURCE = """
function Component({ props }) {
  const rows  = props.rows || [];
  const ICONS = {Laptop:"💻",Monitor:"🖥️",Keyboard:"⌨️",Mouse:"🖱️",Headset:"🎧"};
  function chip(n) {
    if (n === 0) return <span className="chip out">Out of stock</span>;
    if (n <= 2)  return <span className="chip low">{n} left</span>;
    return             <span className="chip in">{n} in stock</span>;
  }
  return (
    <div className="pc-sb">
      <style>{`__BOARD_CSS__`}</style>
      <div className="hd">📦 Inventory</div>
      {rows.map(r => (
        <div key={r.item} className={"irow" + (r.stock === 0 ? " dim" : "")}>
          <div className="ico">{ICONS[r.item] || "📦"}</div>
          <span className="iname">{r.item}</span>
          <span className="iprice">${r.price.toLocaleString()}</span>
          {chip(r.stock)}
        </div>
      ))}
    </div>
  );
}
""".replace("__BOARD_CSS__", _BOARD_CSS)


# ── Admin panel (admin only) ──────────────────────────────────────────────────

_ADMIN_CSS = """
.pc-sa{padding:16px;height:100%;overflow-y:auto;box-sizing:border-box;
       font-family:system-ui,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-sa .hd{font-size:16px;font-weight:700;margin-bottom:18px}
.pc-sa .sec{margin-bottom:18px}
.pc-sa .sec-hd{font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
               color:#64748b;display:flex;align-items:center;gap:8px;margin-bottom:10px}
.pc-sa .sec-hd::after{content:"";flex:1;height:1px;background:var(--pc-border,#30363d)}
.pc-sa .inp{padding:8px 10px;border-radius:8px;font-size:13px;width:100%;box-sizing:border-box;
            border:1.5px solid var(--pc-border,#30363d);background:var(--pc-input-bg,#1b2230);
            color:inherit;outline:none;transition:border-color .15s}
.pc-sa .inp:focus{border-color:#3b82f6}
.pc-sa .btn{margin-top:10px;width:100%;padding:9px;border-radius:8px;font-size:13px;
            font-weight:600;cursor:pointer;border:none;background:#2563eb;color:#fff;
            transition:background .12s}
.pc-sa .btn:hover{background:#1d4ed8}
.pc-sa .urow{padding:7px 0;border-bottom:1px solid var(--pc-border,#30363d)}
.pc-sa .urow:last-child{border-bottom:none}
.pc-sa .urow-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.pc-sa .uname{font-size:12px;font-weight:600}
.pc-sa .ubud{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums;color:#4ade80}
.pc-sa .bbar{height:4px;border-radius:99px;background:var(--pc-off-bg,#333);overflow:hidden}
.pc-sa .bfill{height:100%;border-radius:99px;
              background:linear-gradient(90deg,#2563eb,#7c3aed);transition:width .5s}
.pc-sa .litem{display:flex;gap:8px;padding:5px 0;border-bottom:1px solid var(--pc-border,#30363d);
              font:12px ui-monospace,monospace;color:#94a3b8}
.pc-sa .litem:last-child{border-bottom:none}
.pc-sa .ldot{color:#3b82f6;flex-shrink:0}
.pc-sa .hi{color:var(--pc-text,#e6edf3);font-weight:600}
.pc-sa .msg{margin-top:8px;padding:6px 10px;border-radius:7px;font-size:12px}
.pc-sa .ok{background:rgba(20,83,45,.35);color:#4ade80}
.pc-sa .er{background:rgba(127,29,29,.35);color:#f87171}
"""

_ADMIN_SOURCE = """
function Component({ canvas, props }) {
  const rows  = props.rows  || [];
  const users = props.users || [];
  const log   = props.log   || [];
  const [item,  setItem]  = React.useState("");
  const [stock, setStock] = React.useState("");
  const [msg,   setMsg]   = React.useState(null);
  const START = 2000;

  function submit() {
    const n = parseInt(stock, 10);
    if (!item)              { setMsg({t:"Select an item.", ok:false}); return; }
    if (isNaN(n) || n < 0) { setMsg({t:"Enter a valid quantity.", ok:false}); return; }
    canvas.send({ action:"restock", item, stock:n });
    setMsg({t:"✓ " + item + " restocked to " + n, ok:true});
    setItem(""); setStock("");
  }

  return (
    <div className="pc-sa">
      <style>{`__ADMIN_CSS__`}</style>
      <div className="hd">⚙️ Admin Controls</div>

      <div className="sec">
        <div className="sec-hd">Restock item</div>
        <select value={item} onChange={e=>setItem(e.target.value)} className="inp">
          <option value="">— select item —</option>
          {rows.map(r=>(
            <option key={r.item} value={r.item}>{r.item}  ({r.stock} in stock)</option>
          ))}
        </select>
        <input type="number" min="0" placeholder="New quantity" value={stock}
          onChange={e=>setStock(e.target.value)}
          onKeyDown={e=>{if(e.key==="Enter") submit();}}
          className="inp" style={{marginTop:8}} />
        <button onClick={submit} className="btn">Set stock</button>
        {msg && <div className={"msg " + (msg.ok ? "ok" : "er")}>{msg.t}</div>}
      </div>

      <div className="sec">
        <div className="sec-hd">User budgets</div>
        {users.length === 0
          ? <div style={{color:"#64748b",fontSize:"12px",padding:"4px 0"}}>No users connected yet.</div>
          : users.map(u => {
              const pct = Math.max(0, Math.min(100, Math.round(u.budget / START * 100)));
              return (
                <div key={u.name} className="urow">
                  <div className="urow-top">
                    <span className="uname">{u.name}</span>
                    <span className="ubud">${u.budget.toLocaleString()}</span>
                  </div>
                  <div className="bbar"><div className="bfill" style={{width:pct+"%"}} /></div>
                </div>
              );
            })
        }
      </div>

      <div className="sec">
        <div className="sec-hd">Request log</div>
        {log.length === 0
          ? <div style={{color:"#64748b",fontSize:"12px",padding:"4px 0"}}>No requests yet.</div>
          : log.map((r,i) => (
              <div key={i} className="litem">
                <span className="ldot">▸</span>
                <span>
                  <span className="hi">{r.user}</span>
                  {" · "}{r.qty}× {r.item}{" · "}
                  <span className="hi">${r.cost.toLocaleString()}</span>
                </span>
              </div>
            ))
        }
      </div>
    </div>
  );
}
""".replace("__ADMIN_CSS__", _ADMIN_CSS)


# ── User panel (user only) ────────────────────────────────────────────────────

_USER_CSS = """
.pc-su{padding:16px;height:100%;overflow-y:auto;box-sizing:border-box;
       font-family:system-ui,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-su .hd{font-size:16px;font-weight:700;margin-bottom:18px}
.pc-su .sec{margin-bottom:18px}
.pc-su .sec-hd{font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
               color:#64748b;display:flex;align-items:center;gap:8px;margin-bottom:10px}
.pc-su .sec-hd::after{content:"";flex:1;height:1px;background:var(--pc-border,#30363d)}
.pc-su .irow{display:flex;align-items:center;gap:10px;padding:8px;border-radius:8px;
             cursor:pointer;border:1.5px solid transparent;transition:background .1s,border-color .1s}
.pc-su .irow:hover{background:rgba(255,255,255,.05)}
.pc-su .irow.sel{background:rgba(37,99,235,.15);border-color:#3b82f6}
.pc-su .irow.dim{opacity:0.4;cursor:default;pointer-events:none}
.pc-su .ico{font-size:18px;width:32px;height:32px;border-radius:7px;flex-shrink:0;
            display:flex;align-items:center;justify-content:center;
            background:var(--pc-off-bg,#eee)22}
.pc-su .iname{flex:1;font-size:13px;font-weight:600}
.pc-su .iprice{font-size:12px;font-weight:700;color:var(--pc-muted,#666);
               font-variant-numeric:tabular-nums}
.pc-su .chip{padding:2px 7px;border-radius:999px;font-size:10px;font-weight:700}
.pc-su .in {background:rgba(20,83,45,.35);color:#4ade80}
.pc-su .low{background:rgba(120,53,15,.35);color:#fbbf24}
.pc-su .out{background:rgba(127,29,29,.35);color:#f87171}
.pc-su .rcard{background:rgba(37,99,235,.1);border:1.5px solid rgba(59,130,246,.4);
              border-radius:10px;padding:14px;margin-bottom:14px}
.pc-su .rcard-top{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.pc-su .ricon{font-size:24px}
.pc-su .rname{flex:1;font-size:14px;font-weight:700}
.pc-su .rprice{font-size:12px;color:var(--pc-muted,#666)}
.pc-su .qty-row{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.pc-su .qty-lbl{font-size:12px;color:#64748b;flex:1}
.pc-su .qty-ctrl{display:flex;align-items:center;gap:6px}
.pc-su .qbtn{width:28px;height:28px;border-radius:6px;border:1.5px solid var(--pc-border,#30363d);
             background:transparent;color:inherit;font-size:16px;cursor:pointer;
             display:flex;align-items:center;justify-content:center;transition:background .1s}
.pc-su .qbtn:hover{background:rgba(255,255,255,.08)}
.pc-su .qnum{width:36px;text-align:center;font-size:15px;font-weight:700;
             font-variant-numeric:tabular-nums}
.pc-su .cost-row{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px}
.pc-su .cost-lbl{font-size:12px;color:#64748b}
.pc-su .cost-val{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums;color:#60a5fa}
.pc-su .btn{width:100%;padding:9px;border-radius:8px;font-size:13px;font-weight:600;
            cursor:pointer;border:none;background:#2563eb;color:#fff;transition:background .12s}
.pc-su .btn:hover{background:#1d4ed8}
.pc-su .back{margin-top:8px;text-align:center;font-size:11px;color:#64748b;cursor:pointer}
.pc-su .back:hover{color:var(--pc-text,#e6edf3)}
.pc-su .urow{padding:7px 0;border-bottom:1px solid var(--pc-border,#30363d)}
.pc-su .urow:last-child{border-bottom:none}
.pc-su .urow-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.pc-su .uname{font-size:12px;font-weight:600}
.pc-su .ubud{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums;color:#4ade80}
.pc-su .bbar{height:4px;border-radius:99px;background:var(--pc-off-bg,#333);overflow:hidden}
.pc-su .bfill{height:100%;border-radius:99px;
              background:linear-gradient(90deg,#2563eb,#7c3aed);transition:width .5s}
.pc-su .litem{display:flex;gap:8px;padding:5px 0;border-bottom:1px solid var(--pc-border,#30363d);
              font:12px ui-monospace,monospace;color:#94a3b8}
.pc-su .litem:last-child{border-bottom:none}
.pc-su .ldot{color:#3b82f6;flex-shrink:0}
.pc-su .hi{color:var(--pc-text,#e6edf3);font-weight:600}
.pc-su .msg{padding:6px 10px;border-radius:7px;font-size:12px;margin-bottom:14px}
.pc-su .ok  {background:rgba(20,83,45,.35);color:#4ade80}
.pc-su .warn{background:rgba(120,53,15,.35);color:#fbbf24}
"""

_USER_SOURCE = """
function Component({ canvas, props }) {
  const rows  = props.rows  || [];
  const users = props.users || [];
  const log   = props.log   || [];
  const ICONS = {Laptop:"💻",Monitor:"🖥️",Keyboard:"⌨️",Mouse:"🖱️",Headset:"🎧"};
  const START = 2000;

  const [sel, setSel] = React.useState(null);
  const [qty, setQty] = React.useState(1);
  const [msg, setMsg] = React.useState(null);

  // Register with Python on mount so budget appears immediately.
  React.useEffect(() => { canvas.send({ action:"ping" }); }, []);

  // Keep selected item in sync with live stock updates.
  React.useEffect(() => {
    if (!sel) return;
    const fresh = rows.find(r => r.item === sel.item);
    if (fresh) setSel(fresh);
  }, [rows]);

  const cost = sel ? sel.price * qty : 0;

  function pick(r) {
    if (r.stock === 0) return;
    setSel(r); setQty(1); setMsg(null);
  }

  function incQty(d) {
    if (!sel) return;
    setQty(q => Math.max(1, Math.min(sel.stock, q + d)));
  }

  function request() {
    if (!sel || qty > sel.stock) return;
    canvas.send({ action:"request", item:sel.item, qty });
    setMsg({t:"✓ Requested " + qty + "× " + sel.item, warn:false});
    setSel(null); setQty(1);
  }

  function chip(n) {
    if (n === 0) return <span className="chip out">Out of stock</span>;
    if (n <= 2)  return <span className="chip low">{n} left</span>;
    return             <span className="chip in">{n} in stock</span>;
  }

  return (
    <div className="pc-su">
      <style>{`__USER_CSS__`}</style>
      <div className="hd">🛒 Request Items</div>

      {msg && <div className={"msg " + (msg.warn ? "warn" : "ok")}>{msg.t}</div>}

      {sel ? (
        <div className="rcard">
          <div className="rcard-top">
            <span className="ricon">{ICONS[sel.item]||"📦"}</span>
            <span className="rname">{sel.item}</span>
            <span className="rprice">${sel.price.toLocaleString()} each</span>
          </div>
          <div className="qty-row">
            <span className="qty-lbl">Quantity</span>
            <div className="qty-ctrl">
              <button className="qbtn" onClick={()=>incQty(-1)}>−</button>
              <span className="qnum">{qty}</span>
              <button className="qbtn" onClick={()=>incQty(+1)}>+</button>
            </div>
          </div>
          <div className="cost-row">
            <span className="cost-lbl">Total cost</span>
            <span className="cost-val">${cost.toLocaleString()}</span>
          </div>
          <button className="btn" onClick={request}>
            Request {qty > 1 ? qty + "× " : ""}{sel.item}
          </button>
          <div className="back" onClick={()=>{setSel(null);setQty(1);}}>
            ← back to catalogue
          </div>
        </div>
      ) : (
        <div className="sec">
          <div className="sec-hd">Catalogue</div>
          {rows.map(r => (
            <div key={r.item}
                 className={"irow" + (r.stock===0?" dim":"")}
                 onClick={()=>pick(r)}>
              <div className="ico">{ICONS[r.item]||"📦"}</div>
              <span className="iname">{r.item}</span>
              <span className="iprice">${r.price.toLocaleString()}</span>
              {chip(r.stock)}
            </div>
          ))}
        </div>
      )}

      <div className="sec">
        <div className="sec-hd">Budgets</div>
        {users.length === 0
          ? <div style={{color:"#64748b",fontSize:"12px",padding:"4px 0"}}>Loading…</div>
          : users.map(u => {
              const pct = Math.max(0, Math.min(100, Math.round(u.budget / START * 100)));
              return (
                <div key={u.name} className="urow">
                  <div className="urow-top">
                    <span className="uname">{u.name}</span>
                    <span className="ubud">${u.budget.toLocaleString()}</span>
                  </div>
                  <div className="bbar"><div className="bfill" style={{width:pct+"%"}} /></div>
                </div>
              );
            })
        }
        <div style={{marginTop:8,fontSize:"11px",color:"#64748b"}}>
          Your name is shown on your cursor in the canvas.
        </div>
      </div>

      <div className="sec">
        <div className="sec-hd">Recent requests</div>
        {log.length === 0
          ? <div style={{color:"#64748b",fontSize:"12px",padding:"4px 0"}}>No requests yet.</div>
          : log.map((r,i) => (
              <div key={i} className="litem">
                <span className="ldot">▸</span>
                <span>
                  <span className="hi">{r.user}</span>
                  {" · "}{r.qty}× {r.item}{" · "}
                  <span className="hi">${r.cost.toLocaleString()}</span>
                </span>
              </div>
            ))
        }
      </div>
    </div>
  );
}
""".replace("__USER_CSS__", _USER_CSS)


# ── Canvas layout ─────────────────────────────────────────────────────────────

stock_board = canvas.react(_BOARD_SOURCE, name="board",
                            x=40,  y=40, w=440, h=480,
                            props={"rows": inv_rows()})

admin_panel = canvas.react(_ADMIN_SOURCE, name="admin",
                            x=500, y=40, w=310, h=480,
                            roles=["admin"],
                            props={"rows": inv_rows(), "users": [], "log": []})

user_panel  = canvas.react(_USER_SOURCE,  name="user",
                            x=500, y=40, w=310, h=480,
                            roles=["user"],
                            props={"rows": inv_rows(), "users": [], "log": []})


# ── Python callbacks ──────────────────────────────────────────────────────────

@admin_panel.on_message
def on_admin(msg, viewer):
    if msg.get("action") != "restock":
        return
    item = msg.get("item", "")
    if item not in inventory:
        return
    new_stock = max(0, int(msg.get("stock", 0)))
    inventory[item]["stock"] = new_stock
    print(f"[admin] {viewer['name']} restocked {item} → {new_stock}")
    push_all()


@user_panel.on_message
def on_user(msg, viewer):
    name   = viewer["name"]
    action = msg.get("action")

    if action == "ping":
        ensure_user(name)
        push_all()
        return

    if action != "request":
        return

    item = msg.get("item", "")
    qty  = max(1, int(msg.get("qty", 1)))

    if item not in inventory:
        return

    inv    = inventory[item]
    cost   = inv["price"] * qty
    budget = ensure_user(name)

    if inv["stock"] < qty or budget < cost:
        return  # guard: UI pre-checks stock; budget is server-side only

    inv["stock"]  -= qty
    budgets[name] -= cost
    req_log.insert(0, {"user": name, "item": item, "qty": qty, "cost": cost})

    print(f"[user] {name} → {qty}× {item} for ${cost:,}  (budget left: ${budgets[name]:,})")
    push_all()


canvas.serve(
    port=8000,
    host="0.0.0.0",
    passwords={"admin": "admin", "user": "user"},
    tunnel=True,
)
