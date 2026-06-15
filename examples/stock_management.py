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

_BOARD_CSS = """
.lb{width:100%;border-collapse:collapse;font:13px system-ui,sans-serif;
    color:var(--pc-text,#e6edf3)}
.lb th{padding:8px 12px;text-align:left;font-size:11px;font-weight:600;
    color:#64748b;border-bottom:1px solid var(--pc-border,#30363d);
    text-transform:uppercase;letter-spacing:.05em}
.lb td{padding:9px 12px;border-bottom:1px solid #1e293b}
.lb tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.chip{display:inline-block;padding:2px 9px;border-radius:999px;
      font-size:11px;font-weight:600}
.in {background:#14532d55;color:#4ade80}
.low{background:#78350f55;color:#fbbf24}
.out{background:#7f1d1d55;color:#f87171}
"""

_BOARD_SOURCE = """
function Component({ props }) {
  const rows = props.rows || [];
  function chip(n) {
    if (n === 0) return <span className="chip out">Out of stock</span>;
    if (n <= 2)  return <span className="chip low">{n} left</span>;
    return       <span className="chip in">{n} in stock</span>;
  }
  return (
    <div style={{padding:"8px",height:"100%",overflowY:"auto"}}>
      <style>{`__BOARD_CSS__`}</style>
      <div style={{fontWeight:700,fontSize:"15px",padding:"4px 4px 12px",
                   color:"var(--pc-text,#e6edf3)"}}>
        Inventory
      </div>
      <table className="lb">
        <thead><tr>
          <th>Item</th>
          <th style={{textAlign:"right"}}>Unit price</th>
          <th style={{textAlign:"right"}}>Availability</th>
        </tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.item}>
              <td>{r.item}</td>
              <td className="num">${r.price.toLocaleString()}</td>
              <td style={{textAlign:"right"}}>{chip(r.stock)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
""".replace("__BOARD_CSS__", _BOARD_CSS)


# ── Admin panel (admin only) ──────────────────────────────────────────────────

_ADMIN_CSS = """
section{margin-bottom:14px}
h3{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;
   letter-spacing:.05em;margin:0 0 8px}
.log{padding:0;margin:0}
.log li{font:12px ui-monospace,monospace;color:#94a3b8;padding:3px 0;
        list-style:none;border-bottom:1px solid #1e293b}
.log li:last-child{border-bottom:none}
"""

_ADMIN_SOURCE = """
function Component({ canvas, props }) {
  const rows  = props.rows  || [];
  const users = props.users || [];
  const log   = props.log   || [];
  const [item,  setItem]  = React.useState("");
  const [stock, setStock] = React.useState("");
  const [msg,   setMsg]   = React.useState("");

  const inp = {
    padding:"6px 10px", borderRadius:"6px", fontSize:"13px",
    border:"1px solid var(--pc-border,#30363d)",
    background:"var(--pc-input-bg,#1b2230)",
    color:"inherit", width:"100%", boxSizing:"border-box",
  };

  function submit() {
    const n = parseInt(stock, 10);
    if (!item)           { setMsg("Select an item."); return; }
    if (isNaN(n) || n < 0) { setMsg("Enter a valid quantity."); return; }
    canvas.send({ action: "restock", item, stock: n });
    setMsg("✓ " + item + " set to " + n);
    setItem(""); setStock("");
  }

  return (
    <div style={{padding:"14px",height:"100%",overflowY:"auto",
                 fontFamily:"system-ui,sans-serif",color:"var(--pc-text,#e6edf3)"}}>
      <style>{`__ADMIN_CSS__`}</style>
      <div style={{fontWeight:700,fontSize:"15px",marginBottom:"14px"}}>Admin Controls</div>

      <section>
        <h3>Restock item</h3>
        <select value={item} onChange={e=>setItem(e.target.value)} style={inp}>
          <option value="">— select item —</option>
          {rows.map(r=>(
            <option key={r.item} value={r.item}>{r.item} (stock: {r.stock})</option>
          ))}
        </select>
        <input type="number" min="0" placeholder="New quantity" value={stock}
          onChange={e=>setStock(e.target.value)}
          onKeyDown={e=>{if(e.key==="Enter") submit();}}
          style={{...inp, marginTop:8}} />
        <button onClick={submit}
          style={{marginTop:8,width:"100%",padding:"7px",borderRadius:"6px",
                  fontWeight:600,cursor:"pointer",background:"#2563eb",
                  color:"#fff",border:"none",fontSize:"13px"}}>
          Set stock
        </button>
        {msg && <div style={{marginTop:6,fontSize:"12px",color:"#94a3b8"}}>{msg}</div>}
      </section>

      <section>
        <h3>User budgets</h3>
        {users.length === 0
          ? <div style={{color:"#64748b",fontSize:"12px"}}>No users connected yet.</div>
          : <ul className="log">
              {users.map(u=>(
                <li key={u.name}>{u.name} — <b>${u.budget.toLocaleString()}</b> remaining</li>
              ))}
            </ul>}
      </section>

      <section>
        <h3>Request log</h3>
        {log.length === 0
          ? <div style={{color:"#64748b",fontSize:"12px"}}>No requests yet.</div>
          : <ul className="log">
              {log.map((r,i)=>(
                <li key={i}>{r.user} · {r.qty}× {r.item} · <b>${r.cost.toLocaleString()}</b></li>
              ))}
            </ul>}
      </section>
    </div>
  );
}
""".replace("__ADMIN_CSS__", _ADMIN_CSS)


# ── User panel (user only) ────────────────────────────────────────────────────

_USER_CSS = """
section{margin-bottom:14px}
h3{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;
   letter-spacing:.05em;margin:0 0 8px}
.log{padding:0;margin:0}
.log li{font:12px ui-monospace,monospace;color:#94a3b8;padding:3px 0;
        list-style:none;border-bottom:1px solid #1e293b}
.log li:last-child{border-bottom:none}
"""

_USER_SOURCE = """
function Component({ canvas, props }) {
  const rows  = props.rows  || [];
  const users = props.users || [];
  const log   = props.log   || [];
  const [item, setItem] = React.useState("");
  const [qty,  setQty]  = React.useState("1");
  const [msg,  setMsg]  = React.useState("");

  // Register with Python on mount so our budget appears immediately.
  React.useEffect(() => { canvas.send({ action: "ping" }); }, []);

  const row  = rows.find(r => r.item === item);
  const q    = Math.max(1, parseInt(qty) || 1);
  const cost = row ? row.price * q : 0;

  const inp = {
    padding:"6px 10px", borderRadius:"6px", fontSize:"13px",
    border:"1px solid var(--pc-border,#30363d)",
    background:"var(--pc-input-bg,#1b2230)",
    color:"inherit", width:"100%", boxSizing:"border-box",
  };

  function request() {
    if (!item)             { setMsg("Select an item."); return; }
    if (!row || row.stock < q) { setMsg("Not enough stock."); return; }
    canvas.send({ action: "request", item, qty: q });
    setMsg("✓ Requested " + q + "× " + item);
    setItem(""); setQty("1");
  }

  return (
    <div style={{padding:"14px",height:"100%",overflowY:"auto",
                 fontFamily:"system-ui,sans-serif",color:"var(--pc-text,#e6edf3)"}}>
      <style>{`__USER_CSS__`}</style>
      <div style={{fontWeight:700,fontSize:"15px",marginBottom:"14px"}}>Request Items</div>

      <section>
        <h3>Place a request</h3>
        <select value={item} onChange={e=>setItem(e.target.value)} style={inp}>
          <option value="">— select item —</option>
          {rows.filter(r=>r.stock>0).map(r=>(
            <option key={r.item} value={r.item}>{r.item} — ${r.price.toLocaleString()}</option>
          ))}
        </select>
        <input type="number" min="1" value={qty}
          onChange={e=>setQty(e.target.value)} placeholder="Quantity"
          style={{...inp, marginTop:8}} />
        {item && (
          <div style={{marginTop:6,fontSize:"12px",color:"#94a3b8"}}>
            Total: <b style={{color:"var(--pc-text,#e6edf3)"}}>${cost.toLocaleString()}</b>
            {row && q > row.stock &&
              <span style={{color:"#f87171",marginLeft:8}}>only {row.stock} available</span>}
          </div>
        )}
        <button onClick={request}
          style={{marginTop:8,width:"100%",padding:"7px",borderRadius:"6px",
                  fontWeight:600,cursor:"pointer",background:"#2563eb",
                  color:"#fff",border:"none",fontSize:"13px"}}>
          Request
        </button>
        {msg && <div style={{marginTop:6,fontSize:"12px",color:"#94a3b8"}}>{msg}</div>}
      </section>

      <section>
        <h3>Budgets <span style={{fontWeight:400,color:"#94a3b8"}}>(your name is shown in the canvas cursor)</span></h3>
        {users.length === 0
          ? <div style={{color:"#64748b",fontSize:"12px"}}>Loading…</div>
          : <ul className="log">
              {users.map(u=>(
                <li key={u.name}>{u.name} — <b>${u.budget.toLocaleString()}</b> remaining</li>
              ))}
            </ul>}
      </section>

      <section>
        <h3>Recent requests</h3>
        {log.length === 0
          ? <div style={{color:"#64748b",fontSize:"12px"}}>No requests yet.</div>
          : <ul className="log">
              {log.map((r,i)=>(
                <li key={i}>{r.user} · {r.qty}× {r.item} · <b>${r.cost.toLocaleString()}</b></li>
              ))}
            </ul>}
      </section>
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
        return  # guard: UI pre-checks stock; budget check is server-side only

    inv["stock"]  -= qty
    budgets[name] -= cost
    req_log.insert(0, {"user": name, "item": item, "qty": qty, "cost": cost})

    print(f"[user] {name} → {qty}× {item} for ${cost:,}  (budget left: ${budgets[name]:,})")
    push_all()


canvas.serve(
    port=8000,
    host="0.0.0.0",
    passwords={"admin": "admin", "user": "user"},
)
