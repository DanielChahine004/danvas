"""Hackathon control room: a JSON-backed shop + a live points leaderboard.

Teams spend a budget on resources from a catalogue and earn points from judges;
all state — the catalogue, the teams (password + budget + points), and the order
log — lives in ``hackathon_data.json`` next to this script. It's loaded at
startup and rewritten on every change, so a restart resumes where it left off.

Roles
-----
* **admin / judge** (password ``admin``): manages the catalogue (*Stock
  Control*), creates teams and awards points (*Teams*), works the *Orders* table
  (sortable/filterable, with fulfil / reject / edit-quantity actions), and sees
  the leaderboard.
* **a team** (each team has its own password, set by the admin): browses the
  catalogue, files orders against its shared budget, and watches the leaderboard.
* **a spectator** (password ``view``): a read-only big-screen view — just the
  catalogue and the leaderboard, nothing to click.

A viewer's *role* is the team they logged in as — assigned server-side from the
password, so it can't be spoofed — so every order is attributed to the right team
and the budget/points are shared by everyone on it. Teams are created at runtime:
the admin's new password is added to the live auth map (no restart) and the team
panel starts accepting that role.

Order workflow
--------------
A team adds items to a *cart* and files it, seeing how much budget it would have
left first. Filing **reserves the budget** (it goes down immediately) and creates
one pending order per line. The team can **retract** any still-pending order to
get the money back. In the Orders table the admin can fulfil (✓), mark
not-fulfilled (×), or edit a pending order's quantity (✎). **Stock** is taken
only on fulfilment and returned if that's undone; **budget** is held while an
order is pending or fulfilled and refunded the moment it's retracted or rejected.

Leaderboard
-----------
Every team has a points total. The admin awards (or docks) points from the Teams
panel; the leaderboard ranks teams live, medals for the top three, and is visible
to everyone — admins, teams, and spectators.
"""

import json
import os

import pycanvas

DATA_PATH = os.path.join(os.path.dirname(__file__), "hackathon_data.json")
ADMIN_PASSWORD = "admin"
VIEWER_PASSWORD = "view"     # spectator: read-only catalogue + leaderboard
DEFAULT_BUDGET = 2000

# A role string no password ever maps to. It keeps the team panel's role list
# non-empty: an empty `roles=[]` means "visible to everyone" (incl. admin), so
# even after the admin deletes every team the panel stays hidden from non-teams.
TEAM_SENTINEL = "__team__"

canvas = pycanvas.Canvas()


# ── Persistence ───────────────────────────────────────────────────────────────

DATA_VERSION = 2   # bumped when the saved-state model changes (see migration)


def load():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return (data["inventory"], data["teams"], data.get("log", []),
            data.get("version", 1))


def save():
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump({"version": DATA_VERSION, "inventory": inventory,
                   "teams": teams, "log": log}, f, indent=2)


inventory, teams, log, _data_version = load()   # item->{stock,price}, team->{password,budget,points}, [orders]

# Every team carries a points total for the leaderboard; backfill it for teams
# saved before points existed (idempotent, so it runs safely on every load).
for _t in teams.values():
    _t.setdefault("points", 0)


# ── Orders ────────────────────────────────────────────────────────────────────
# An order is a log row with a status. A team's *budget* is reserved the moment
# it requests (held while the order is "pending" or "fulfilled") and refunded if
# the order is "retracted" (by the team) or "rejected" (by the admin). *Stock*
# moves later: it's taken only when the admin marks an order "fulfilled" and put
# back if that's undone. `set_order_status` reconciles both holds on any change.

_BUDGET_STATES = ("pending", "fulfilled")   # order is holding the team's money
_STOCK_STATES = ("fulfilled",)              # order is holding the stock


def normalize_log():
    """Backfill id/price/status/cost on rows from older saves."""
    seq = max((o["id"] for o in log if isinstance(o.get("id"), int)), default=0)
    for o in log:
        if not isinstance(o.get("id"), int):
            seq += 1
            o["id"] = seq
        qty = max(1, int(o.get("qty", 1)))
        o.setdefault("price", int(o.get("cost", 0) / qty) if o.get("cost") else 0)
        o.setdefault("status", "fulfilled")
        o["qty"] = qty
        o["cost"] = o["price"] * qty


normalize_log()

# Migrate v1 saves (budget was deducted only at fulfilment) to v2 (budget is
# reserved at request): pending orders didn't count against budget before, so
# reserve them once now. Fulfilled orders were already deducted, so they're left
# alone. Stamped DATA_VERSION on the next save, making this run exactly once.
if _data_version < 2:
    for o in log:
        if o["status"] == "pending":
            t = teams.get(o["team"])
            if t is not None:
                t["budget"] -= o["cost"]

_order_seq = max((o["id"] for o in log), default=0)


def next_order_id():
    global _order_seq
    _order_seq += 1
    return _order_seq


def find_order(oid):
    return next((o for o in log if o["id"] == oid), None)


def reserve_budget(o):
    t = teams.get(o["team"])
    if t is not None:
        t["budget"] -= o["cost"]


def refund_budget(o):
    t = teams.get(o["team"])
    if t is not None:
        t["budget"] += o["cost"]


def set_order_status(o, new):
    """Move an order to ``new`` status, reconciling the budget + stock it holds.

    Acquiring a hold (budget or stock) is pre-checked, so the transition is
    atomic: it returns False and changes nothing if the team can't afford the
    re-reservation or there isn't enough stock to fulfil.
    """
    old = o["status"]
    if old == new:
        return True
    gain_budget = old not in _BUDGET_STATES and new in _BUDGET_STATES
    gain_stock = old not in _STOCK_STATES and new in _STOCK_STATES
    t = teams.get(o["team"])
    inv = inventory.get(o["item"])
    if gain_budget and (t is None or t["budget"] < o["cost"]):
        return False
    if gain_stock and (inv is None or inv["stock"] < o["qty"]):
        return False
    # Release first, then acquire (both pre-checked above).
    if old in _BUDGET_STATES and new not in _BUDGET_STATES:
        refund_budget(o)
    if old in _STOCK_STATES and new not in _STOCK_STATES and inv is not None:
        inv["stock"] += o["qty"]
    if gain_budget:
        reserve_budget(o)
    if gain_stock:
        inv["stock"] = max(0, inv["stock"] - o["qty"])
    o["status"] = new
    return True


# ── Wire shapes for the panels ────────────────────────────────────────────────

def inv_rows():
    return [{"item": k, "stock": v["stock"], "price": v["price"]}
            for k, v in inventory.items()]


def team_rows(with_passwords):
    # Admins get the passwords (they set them); teams never see another team's.
    out = []
    for name, t in teams.items():
        row = {"name": name, "budget": t["budget"], "points": t["points"]}
        if with_passwords:
            row["password"] = t["password"]
        out.append(row)
    return out


def leaderboard_rows():
    """Teams ranked by points, highest first — what the leaderboard renders."""
    ranked = sorted(teams.items(), key=lambda kv: kv[1]["points"], reverse=True)
    return [{"name": n, "points": t["points"]} for n, t in ranked]


def push_team(name):
    """Send one team only *its* slice — catalogue, budget, and its own orders —
    addressed to that role with update_for, so no other team's budget or orders
    are ever sent to it (privacy by construction, not client-side filtering)."""
    team_panel.update_for(role=name,
                          team=name,
                          rows=inv_rows(),
                          budget=teams[name]["budget"],
                          orders=[o for o in log if o["team"] == name][:40])


def push_all():
    # Broadcast the shared panels...
    board.update(rows=inv_rows())
    admin_stock.update(rows=inv_rows())
    admin_teams.update(teams=team_rows(True))
    admin_orders.update(log=log[:200])
    leaderboard.update(rows=leaderboard_rows())
    # ...then each team's private view, role by role.
    for name in teams:
        push_team(name)


# ── Request handlers ──────────────────────────────────────────────────────────
# All the app's behaviour lives here, next to the state it touches. They're
# registered on the panels down in the wiring section (the panels don't exist
# yet); every reference below — panels, PASSWORDS, grant_team_view — resolves at
# call time, i.e. once the canvas is serving.

def on_admin(msg, viewer):
    # One handler for both admin panels — stock actions come from the stock
    # panel, team actions from the teams panel; we dispatch on `action`.
    if viewer.get("role") != "admin":
        return  # the panels only reach admins, but authorize server-side too
    action = msg.get("action")

    if action == "item_set":
        name = (msg.get("item") or "").strip()
        if not name:
            return
        inventory[name] = {"stock": max(0, int(msg.get("stock", 0))),
                           "price": max(0, int(msg.get("price", 0)))}
        print(f"[admin] set {name}: ${inventory[name]['price']} / {inventory[name]['stock']} in stock")

    elif action == "item_remove":
        inventory.pop(msg.get("item", ""), None)

    elif action == "team_add":
        name = (msg.get("name") or "").strip()
        pw = (msg.get("password") or "").strip()
        if not name or not pw or name == "admin":
            return
        budget = max(0, int(msg.get("budget", DEFAULT_BUDGET)))
        new_team = name not in teams
        # Upsert: keep the spent-down budget on edit (the form prefills it) and
        # preserve the team's points across edits.
        teams[name] = {"password": pw, "budget": budget,
                       "points": teams.get(name, {}).get("points", 0)}
        PASSWORDS[name] = pw
        team_panel.add_role(name)   # team + board panels now admit this role, live
        board.add_role(name)
        grant_team_view(name)
        print(f"[admin] {'created' if new_team else 'updated'} team {name!r} "
              f"(password {pw!r}, budget ${budget})")

    elif action == "team_remove":
        name = msg.get("name", "")
        if name in teams:
            teams.pop(name)
            PASSWORDS.pop(name, None)
            team_panel.remove_role(name)   # drop the panels from that role, live
            board.remove_role(name)
            print(f"[admin] removed team {name!r}")

    elif action == "award":
        name = msg.get("name", "")
        if name in teams:
            teams[name]["points"] += int(msg.get("points", 0))
            print(f"[admin] {name} -> {teams[name]['points']} pts")

    elif action == "order_fulfil":
        o = find_order(msg.get("id"))
        if o is None or o["status"] == "fulfilled":
            return
        if not set_order_status(o, "fulfilled"):   # takes stock (budget already held)
            print(f"[admin] can't fulfil order #{o['id']} "
                  f"({o['qty']}x {o['item']} for {o['team']}): not enough stock/budget")
            return
        print(f"[admin] fulfilled #{o['id']}: {o['qty']}x {o['item']} -> {o['team']}")

    elif action == "order_reject":
        o = find_order(msg.get("id"))
        if o is None or o["status"] in ("rejected", "retracted"):
            return
        set_order_status(o, "rejected")   # refunds budget (and returns stock if fulfilled)
        print(f"[admin] marked #{o['id']} not fulfilled")

    elif action == "order_edit":
        o = find_order(msg.get("id"))
        if o is None or o["status"] != "pending":
            return  # only a pending order's quantity can be edited
        new_qty = max(1, int(msg.get("qty", o["qty"])))
        new_cost = o["price"] * new_qty
        t = teams.get(o["team"])
        if t is not None:
            # The order already holds o["cost"]; adjust the reservation to the new
            # cost, refusing an increase the team can't afford.
            if t["budget"] + o["cost"] < new_cost:
                return
            t["budget"] += o["cost"] - new_cost
        o["qty"] = new_qty
        o["cost"] = new_cost

    else:
        return

    save()
    push_all()


def on_team(msg, viewer):
    team = viewer.get("role")
    if team not in teams:
        return
    action = msg.get("action")

    if action == "ping":
        push_all()
        return

    if action == "file":
        # File a cart of [{item, qty}, …] as one pending order per line. Budget is
        # reserved now; prices are snapshotted. Atomic: if the team can't afford
        # the whole cart, nothing is filed (the UI also pre-checks this).
        lines = []
        total = 0
        for entry in msg.get("cart", []):
            item = entry.get("item", "")
            qty = max(1, int(entry.get("qty", 1)))
            if item not in inventory:
                continue
            price = inventory[item]["price"]
            lines.append((item, qty, price))
            total += price * qty
        if not lines or teams[team]["budget"] < total:
            return
        for item, qty, price in lines:
            order = {"id": next_order_id(), "team": team, "item": item,
                     "qty": qty, "price": price, "cost": price * qty,
                     "status": "pending"}
            log.insert(0, order)
            reserve_budget(order)   # budget goes down on request
        print(f"[{team}] filed {len(lines)} line(s) for ${total:,} "
              f"(budget left: ${teams[team]['budget']:,})")
        save()
        push_all()
        return

    if action == "retract":
        o = find_order(msg.get("id"))
        # A team can only retract its *own* still-pending order; this refunds the
        # reserved budget.
        if o is None or o["team"] != team or o["status"] != "pending":
            return
        set_order_status(o, "retracted")
        print(f"[{team}] retracted order #{o['id']} (budget back: ${teams[team]['budget']:,})")
        save()
        push_all()
        return


# ══════════════════════════════════════════════════════════════════════════════
# PANEL VIEWS — the frontend. Each panel is a React component authored as JSX,
# with its stylesheet in a sibling CSS string (passed as css=). This is the bulk
# of the file but none of the app logic; skip to "Canvas layout" for the wiring.
# ══════════════════════════════════════════════════════════════════════════════

# ── Inventory board (everyone) ────────────────────────────────────────────────

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
.pc-sb .empty{color:#64748b;font-size:13px;padding:12px 4px}
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
      <div className="hd">📦 Inventory</div>
      {rows.length === 0
        ? <div className="empty">No items in the catalogue yet.</div>
        : rows.map(r => (
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
"""


# ── Admin panels (admin only) ─────────────────────────────────────────────────
# Shared CSS for both admin panels (stock control + teams). Scoped under .pc-sa,
# so duplicating the <style> in each panel is harmless.

_ADMIN_CSS = """
.pc-sa{padding:16px;height:100%;overflow-y:auto;box-sizing:border-box;
       font-family:system-ui,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-sa .hd{font-size:16px;font-weight:700;margin-bottom:16px}
.pc-sa .sec{margin-bottom:20px}
.pc-sa .sec-hd{font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
               color:#64748b;display:flex;align-items:center;gap:8px;margin-bottom:10px}
.pc-sa .sec-hd::after{content:"";flex:1;height:1px;background:var(--pc-border,#30363d)}
.pc-sa .frm{display:flex;flex-wrap:wrap;gap:6px}
.pc-sa .inp{padding:7px 9px;border-radius:7px;font-size:13px;box-sizing:border-box;
            border:1.5px solid var(--pc-border,#30363d);background:var(--pc-input-bg,#1b2230);
            color:inherit;outline:none;transition:border-color .15s;min-width:0}
.pc-sa .inp:focus{border-color:#3b82f6}
.pc-sa .grow{flex:1 1 100%}
.pc-sa .num{flex:1 1 0;min-width:70px}
.pc-sa .btn{flex:1 1 100%;margin-top:2px;padding:8px;border-radius:7px;font-size:13px;
            font-weight:600;cursor:pointer;border:none;background:#2563eb;color:#fff;
            transition:background .12s}
.pc-sa .btn:hover{background:#1d4ed8}
.pc-sa .row{display:flex;align-items:center;gap:10px;padding:7px 0;
            border-bottom:1px solid var(--pc-border,#30363d);cursor:pointer}
.pc-sa .row:last-child{border-bottom:none}
.pc-sa .row:hover{background:rgba(255,255,255,.04)}
.pc-sa .nm{flex:1;font-size:13px;font-weight:600}
.pc-sa .meta{font-size:12px;color:#94a3b8;font-variant-numeric:tabular-nums}
.pc-sa .pw{font:12px ui-monospace,monospace;color:#fbbf24;
           background:rgba(120,53,15,.25);padding:1px 6px;border-radius:5px}
.pc-sa .bud{font-size:12px;font-weight:700;color:#4ade80;font-variant-numeric:tabular-nums}
.pc-sa .pts{font-size:12px;font-weight:800;color:#fbbf24;font-variant-numeric:tabular-nums;
            min-width:50px;text-align:right}
.pc-sa .x{width:22px;height:22px;border-radius:6px;border:none;cursor:pointer;flex-shrink:0;
          background:rgba(127,29,29,.3);color:#f87171;font-size:14px;line-height:1}
.pc-sa .x:hover{background:rgba(127,29,29,.6)}
.pc-sa .quick{display:flex;gap:6px;margin-top:8px}
.pc-sa .qpt{flex:1;padding:6px;border-radius:6px;border:1.5px solid var(--pc-border,#30363d);
            background:transparent;color:inherit;font-size:12px;font-weight:700;cursor:pointer}
.pc-sa .qpt:not(:disabled):hover{border-color:#3b82f6;background:rgba(59,130,246,.15)}
.pc-sa .qpt:disabled{opacity:.4;cursor:not-allowed}
.pc-sa .hi{color:var(--pc-text,#e6edf3);font-weight:600}
.pc-sa .empty{color:#64748b;font-size:12px;padding:6px 0}
"""

# Panel 1: stock control — add / change / remove items, prices and stock.
_STOCK_SOURCE = """
function Component({ canvas, props }) {
  const rows = props.rows || [];

  const [it, setIt] = React.useState({ item:"", price:"", stock:"" });

  function submitItem() {
    const name = it.item.trim();
    const price = parseInt(it.price, 10);
    const stock = parseInt(it.stock, 10);
    if (!name || isNaN(price) || isNaN(stock)) return;
    canvas.send({ action:"item_set", item:name, price, stock });
    setIt({ item:"", price:"", stock:"" });
  }

  return (
    <div className="pc-sa">
      <div className="hd">⚙️ Stock Control</div>

      <div className="sec">
        <div className="sec-hd">Add / update item</div>
        <div className="frm">
          <input className="inp grow" placeholder="Item name" value={it.item}
            onChange={e=>setIt({...it, item:e.target.value})} />
          <input className="inp num" type="number" min="0" placeholder="Price" value={it.price}
            onChange={e=>setIt({...it, price:e.target.value})} />
          <input className="inp num" type="number" min="0" placeholder="Stock" value={it.stock}
            onChange={e=>setIt({...it, stock:e.target.value})}
            onKeyDown={e=>{ if(e.key==="Enter") submitItem(); }} />
          <button className="btn" onClick={submitItem}>Save item</button>
        </div>
      </div>

      <div className="sec">
        <div className="sec-hd">Catalogue ({rows.length})</div>
        {rows.length === 0
          ? <div className="empty">No items yet.</div>
          : rows.map(r => (
              <div key={r.item} className="row"
                   title="Click to edit"
                   onClick={()=>setIt({ item:r.item, price:String(r.price), stock:String(r.stock) })}>
                <span className="nm">{r.item}</span>
                <span className="meta">${r.price.toLocaleString()} · {r.stock} in stock</span>
                <button className="x" title="Remove item"
                  onClick={e=>{ e.stopPropagation(); canvas.send({ action:"item_remove", item:r.item }); }}>×</button>
              </div>
            ))}
      </div>
    </div>
  );
}
"""


# Panel 2: team management — create/edit/remove teams (budget) and award points.
_TEAMS_SOURCE = """
function Component({ canvas, props }) {
  const teams = props.teams || [];

  const [tm, setTm] = React.useState({ name:"", password:"", budget:"2000" });
  const [aw, setAw] = React.useState({ team:"", points:"" });

  function submitTeam() {
    const name = tm.name.trim();
    const password = tm.password.trim();
    const budget = parseInt(tm.budget, 10);
    if (!name || !password || isNaN(budget)) return;
    canvas.send({ action:"team_add", name, password, budget });
    setTm({ name:"", password:"", budget:"2000" });
  }
  function award(delta) {
    const team = aw.team;
    const pts = delta != null ? delta : parseInt(aw.points, 10);
    if (!team || isNaN(pts) || pts === 0) return;
    canvas.send({ action:"award", name:team, points:pts });
    if (delta == null) setAw({ ...aw, points:"" });
  }

  return (
    <div className="pc-sa">
      <div className="hd">👥 Teams</div>

      <div className="sec">
        <div className="sec-hd">Add / update team</div>
        <div className="frm">
          <input className="inp grow" placeholder="Team name" value={tm.name}
            onChange={e=>setTm({...tm, name:e.target.value})} />
          <input className="inp num" placeholder="Password" value={tm.password}
            onChange={e=>setTm({...tm, password:e.target.value})} />
          <input className="inp num" type="number" min="0" placeholder="Budget" value={tm.budget}
            onChange={e=>setTm({...tm, budget:e.target.value})}
            onKeyDown={e=>{ if(e.key==="Enter") submitTeam(); }} />
          <button className="btn" onClick={submitTeam}>Save team</button>
        </div>
      </div>

      <div className="sec">
        <div className="sec-hd">Award points</div>
        <div className="frm">
          <select className="inp grow" value={aw.team}
            onChange={e=>setAw({...aw, team:e.target.value})}>
            <option value="">— select team —</option>
            {teams.map(t => <option key={t.name} value={t.name}>{t.name} ({t.points} pts)</option>)}
          </select>
          <input className="inp grow" type="number" placeholder="± points" value={aw.points}
            onChange={e=>setAw({...aw, points:e.target.value})}
            onKeyDown={e=>{ if(e.key==="Enter") award(); }} />
          <button className="btn" onClick={()=>award()}>Award points</button>
        </div>
        <div className="quick">
          {[1, 5, 10, -5].map(d => (
            <button key={d} className="qpt" disabled={!aw.team} onClick={()=>award(d)}>
              {d > 0 ? "+" + d : d}
            </button>
          ))}
        </div>
      </div>

      <div className="sec">
        <div className="sec-hd">Teams ({teams.length})</div>
        {teams.length === 0
          ? <div className="empty">No teams yet — add one above.</div>
          : teams.map(t => (
              <div key={t.name} className="row"
                   title="Click to edit"
                   onClick={()=>setTm({ name:t.name, password:t.password, budget:String(t.budget) })}>
                <span className="nm">{t.name}</span>
                <span className="pw">{t.password}</span>
                <span className="bud">${t.budget.toLocaleString()}</span>
                <span className="pts">{t.points} pts</span>
                <button className="x" title="Remove team"
                  onClick={e=>{ e.stopPropagation(); canvas.send({ action:"team_remove", name:t.name }); }}>×</button>
              </div>
            ))}
      </div>
    </div>
  );
}
"""


# ── Team panel (teams only) ───────────────────────────────────────────────────

_TEAM_CSS = """
.pc-su{padding:16px;height:100%;overflow-y:auto;box-sizing:border-box;
       font-family:system-ui,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-su .top{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:16px}
.pc-su .tname{font-size:16px;font-weight:700}
.pc-su .tbud{font-size:13px;font-weight:800;color:#4ade80;font-variant-numeric:tabular-nums}
.pc-su .sec{margin-bottom:18px}
.pc-su .sec-hd{font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
               color:#64748b;display:flex;align-items:center;gap:8px;margin-bottom:10px}
.pc-su .sec-hd::after{content:"";flex:1;height:1px;background:var(--pc-border,#30363d)}
.pc-su .irow{display:flex;align-items:center;gap:10px;padding:8px;border-radius:8px;
             cursor:pointer;border:1.5px solid transparent;transition:background .1s,border-color .1s}
.pc-su .irow:hover{background:rgba(255,255,255,.05)}
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
             display:flex;align-items:center;justify-content:center}
.pc-su .qbtn:hover{background:rgba(255,255,255,.08)}
.pc-su .qnum{width:36px;text-align:center;font-size:15px;font-weight:700;
             font-variant-numeric:tabular-nums}
.pc-su .cost-row{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px}
.pc-su .cost-lbl{font-size:12px;color:#64748b}
.pc-su .cost-val{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums;color:#60a5fa}
.pc-su .cost-val.over{color:#f87171}
.pc-su .btn{width:100%;padding:9px;border-radius:8px;font-size:13px;font-weight:600;
            cursor:pointer;border:none;background:#2563eb;color:#fff;transition:background .12s}
.pc-su .btn:disabled{opacity:.45;cursor:not-allowed}
.pc-su .btn:not(:disabled):hover{background:#1d4ed8}
.pc-su .back{margin-top:8px;text-align:center;font-size:11px;color:#64748b;cursor:pointer}
.pc-su .back:hover{color:var(--pc-text,#e6edf3)}
.pc-su .litem{display:flex;align-items:center;gap:8px;padding:5px 0;
              border-bottom:1px solid var(--pc-border,#30363d);
              font:12px ui-monospace,monospace;color:#94a3b8}
.pc-su .litem:last-child{border-bottom:none}
.pc-su .ldot{color:#3b82f6;flex-shrink:0}
.pc-su .ltext{flex:1;min-width:0}
.pc-su .hi{color:var(--pc-text,#e6edf3);font-weight:600}
.pc-su .ostat{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;
              padding:2px 6px;border-radius:999px;flex-shrink:0}
.pc-su .s-pend{background:rgba(59,130,246,.2);color:#60a5fa}
.pc-su .s-ok{background:rgba(20,83,45,.35);color:#4ade80}
.pc-su .s-no{background:rgba(127,29,29,.35);color:#f87171}
.pc-su .s-ret{background:rgba(100,116,139,.25);color:#94a3b8}
.pc-su .msg{padding:6px 10px;border-radius:7px;font-size:12px;margin-bottom:14px;
            background:rgba(20,83,45,.35);color:#4ade80}
.pc-su .empty{color:#64748b;font-size:12px;padding:4px 0}
.pc-su .retract{width:22px;height:22px;border-radius:5px;border:none;cursor:pointer;flex-shrink:0;
                background:rgba(255,255,255,.06);color:#94a3b8;font-size:12px;line-height:1}
.pc-su .retract:hover{background:rgba(251,191,36,.25);color:#fbbf24}
.pc-su .cart{margin-bottom:18px;border:1.5px solid rgba(59,130,246,.4);border-radius:10px;
             background:rgba(37,99,235,.08);padding:12px}
.pc-su .cline{display:flex;align-items:center;gap:8px;padding:6px 0;
              border-bottom:1px solid var(--pc-border,#30363d)}
.pc-su .cline:last-of-type{border-bottom:none}
.pc-su .cname{flex:1;font-size:13px;font-weight:600;min-width:0;white-space:nowrap;
              overflow:hidden;text-overflow:ellipsis}
.pc-su .qty-ctrl.sm .qbtn{width:22px;height:22px;font-size:14px}
.pc-su .qty-ctrl.sm .qnum{width:24px;font-size:13px}
.pc-su .ccost{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums;
              min-width:56px;text-align:right}
.pc-su .cx{width:20px;height:20px;border-radius:5px;border:none;cursor:pointer;flex-shrink:0;
           background:rgba(127,29,29,.3);color:#f87171;font-size:13px;line-height:1}
.pc-su .cx:hover{background:rgba(127,29,29,.6)}
.pc-su .ctot{margin:10px 0;font-size:12px}
.pc-su .ctot-row{display:flex;justify-content:space-between;padding:3px 0;color:#94a3b8}
.pc-su .ctot-row.big{font-size:14px;font-weight:800;color:#4ade80;margin-top:4px;
                     padding-top:8px;border-top:1px solid var(--pc-border,#30363d)}
.pc-su .ctot-row.big.over{color:#f87171}
"""

_TEAM_SOURCE = """
function Component({ canvas, props }) {
  // Everything here is *this team's own* slice, delivered by Python's
  // update_for(role=...) — the panel never receives another team's figures, so
  // there's nothing to filter and no need to read our own identity.
  const rows   = props.rows   || [];
  const myTeam = props.team   || "";
  const budget = props.budget || 0;     // already net of reserved orders
  const myLog  = props.orders || [];
  const ICONS = {Laptop:"💻",Monitor:"🖥️",Keyboard:"⌨️",Mouse:"🖱️",Headset:"🎧"};
  const priceOf = (item) => { const r = rows.find(x => x.item === item); return r ? r.price : 0; };

  const [sel, setSel]   = React.useState(null);
  const [qty, setQty]   = React.useState(1);
  const [cart, setCart] = React.useState([]);     // [{item, qty}] — staged, not yet filed
  const [msg, setMsg]   = React.useState(null);

  // Register with Python on mount so our budget/log appear immediately.
  React.useEffect(() => { canvas.send({ action:"ping" }); }, []);

  // Keep the selected item in sync with live stock updates; drop cart lines whose
  // item left the catalogue.
  React.useEffect(() => {
    if (sel) { const fresh = rows.find(r => r.item === sel.item); setSel(fresh || null); }
    setCart(c => c.filter(line => rows.some(r => r.item === line.item)));
  }, [rows]);

  const cartLines = cart.map(c => ({ item:c.item, qty:c.qty,
                                     cost: priceOf(c.item) * c.qty }));
  const cartTotal = cartLines.reduce((s, l) => s + l.cost, 0);
  const afterFiling = budget - cartTotal;          // what we'd have left once filed

  function pick(r) { if (r.stock > 0) { setSel(r); setQty(1); setMsg(null); } }
  function incQty(d) { if (sel) setQty(q => Math.max(1, Math.min(sel.stock, q + d))); }
  function addToCart() {
    if (!sel) return;
    setCart(c => {
      const i = c.findIndex(x => x.item === sel.item);
      if (i >= 0) { const n = [...c]; n[i] = { ...n[i], qty: n[i].qty + qty }; return n; }
      return [...c, { item: sel.item, qty }];
    });
    setMsg("Added " + qty + "× " + sel.item + " to cart");
    setSel(null); setQty(1);
  }
  function setLineQty(item, q) {
    setCart(c => c.map(x => x.item === item ? { ...x, qty: Math.max(1, q) } : x));
  }
  function removeLine(item) { setCart(c => c.filter(x => x.item !== item)); }
  function fileRequest() {
    if (!cart.length || afterFiling < 0) return;
    canvas.send({ action:"file", cart: cart.map(c => ({ item: c.item, qty: c.qty })) });
    setMsg("✓ Filed " + cart.length + " line(s) for $" + cartTotal.toLocaleString());
    setCart([]);
  }
  function retract(id) { canvas.send({ action:"retract", id }); }

  function chip(n) {
    if (n === 0) return <span className="chip out">Out of stock</span>;
    if (n <= 2)  return <span className="chip low">{n} left</span>;
    return             <span className="chip in">{n} in stock</span>;
  }

  return (
    <div className="pc-su">
      <div className="top">
        <span className="tname">🛒 {myTeam || "…"}</span>
        <span className="tbud">${budget.toLocaleString()} left</span>
      </div>

      {msg && <div className="msg">{msg}</div>}

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
            <span className="cost-lbl">Line cost</span>
            <span className="cost-val">${(sel.price * qty).toLocaleString()}</span>
          </div>
          <button className="btn" onClick={addToCart}>Add to cart</button>
          <div className="back" onClick={()=>{ setSel(null); setQty(1); }}>← back to catalogue</div>
        </div>
      ) : (
        <div className="sec">
          <div className="sec-hd">Catalogue</div>
          {rows.length === 0
            ? <div className="empty">Nothing in stock right now.</div>
            : rows.map(r => (
                <div key={r.item} className={"irow" + (r.stock===0?" dim":"")} onClick={()=>pick(r)}>
                  <div className="ico">{ICONS[r.item]||"📦"}</div>
                  <span className="iname">{r.item}</span>
                  <span className="iprice">${r.price.toLocaleString()}</span>
                  {chip(r.stock)}
                </div>
              ))}
        </div>
      )}

      {cart.length > 0 && (
        <div className="cart">
          <div className="sec-hd">Cart ({cart.length})</div>
          {cartLines.map(l => (
            <div key={l.item} className="cline">
              <span className="cname">{ICONS[l.item]||"📦"} {l.item}</span>
              <div className="qty-ctrl sm">
                <button className="qbtn" onClick={()=>setLineQty(l.item, l.qty - 1)}>−</button>
                <span className="qnum">{l.qty}</span>
                <button className="qbtn" onClick={()=>setLineQty(l.item, l.qty + 1)}>+</button>
              </div>
              <span className="ccost">${l.cost.toLocaleString()}</span>
              <button className="cx" title="Remove" onClick={()=>removeLine(l.item)}>×</button>
            </div>
          ))}
          <div className="ctot">
            <div className="ctot-row"><span>Cart total</span><span>${cartTotal.toLocaleString()}</span></div>
            <div className="ctot-row"><span>Budget now</span><span>${budget.toLocaleString()}</span></div>
            <div className={"ctot-row big" + (afterFiling < 0 ? " over" : "")}>
              <span>After filing</span><span>${afterFiling.toLocaleString()}</span>
            </div>
          </div>
          <button className="btn" onClick={fileRequest} disabled={afterFiling < 0}>
            {afterFiling < 0 ? "Over budget" : "File request — $" + cartTotal.toLocaleString()}
          </button>
        </div>
      )}

      <div className="sec">
        <div className="sec-hd">Your orders</div>
        {myLog.length === 0
          ? <div className="empty">No orders yet.</div>
          : myLog.map(r => {
              const st = ({pending:["awaiting","s-pend"], fulfilled:["fulfilled","s-ok"],
                           rejected:["declined","s-no"], retracted:["retracted","s-ret"]})[r.status]
                         || ["awaiting","s-pend"];
              return (
                <div key={r.id} className="litem">
                  <span className="ldot">▸</span>
                  <span className="ltext">{r.qty}× {r.item}{" · "}
                    <span className="hi">${(r.price * r.qty).toLocaleString()}</span></span>
                  <span className={"ostat " + st[1]}>{st[0]}</span>
                  {r.status === "pending" &&
                    <button className="retract" title="Retract — refunds your budget"
                      onClick={()=>retract(r.id)}>↩</button>}
                </div>
              );
            })}
      </div>
    </div>
  );
}
"""


# ── Orders panel (admin only) ─────────────────────────────────────────────────
# The order log as a sortable, filterable table: click a header to sort, type to
# filter by team/item, and use the status chips to narrow to pending/fulfilled/
# not-fulfilled. The Actions column keeps the fulfil (✓) / not-fulfilled (×) /
# edit-quantity (✎) controls.

_ORDERS_CSS = """
.pc-ord{display:flex;flex-direction:column;height:100%;box-sizing:border-box;
        font-family:system-ui,sans-serif;color:var(--pc-text,#e6edf3);padding:14px}
.pc-ord .hd{font-size:16px;font-weight:700;margin-bottom:12px}
.pc-ord .bar{display:flex;align-items:center;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.pc-ord .filter{flex:1;min-width:140px;padding:7px 10px;border-radius:7px;font-size:13px;
                box-sizing:border-box;border:1.5px solid var(--pc-border,#30363d);
                background:var(--pc-input-bg,#1b2230);color:inherit;outline:none}
.pc-ord .filter:focus{border-color:#3b82f6}
.pc-ord .chip{padding:5px 10px;border-radius:999px;font-size:12px;font-weight:600;cursor:pointer;
              border:1.5px solid var(--pc-border,#30363d);background:transparent;color:#94a3b8}
.pc-ord .chip:hover{border-color:#3b82f6;color:var(--pc-text,#e6edf3)}
.pc-ord .chip.on{background:#2563eb;border-color:#2563eb;color:#fff}
.pc-ord .scroll{flex:1;overflow:auto;border:1px solid var(--pc-border,#30363d);border-radius:8px}
.pc-ord table{border-collapse:collapse;width:100%;font-size:13px}
.pc-ord th,.pc-ord td{padding:8px 10px;text-align:left;white-space:nowrap;
                      border-bottom:1px solid var(--pc-border,#30363d)}
.pc-ord thead th{position:sticky;top:0;background:var(--pc-input-bg,#1b2230);z-index:1;
                 font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;
                 color:#94a3b8;cursor:pointer;user-select:none}
.pc-ord thead th.acth{cursor:default;text-align:right}
.pc-ord thead th:hover{color:var(--pc-text,#e6edf3)}
.pc-ord .arr{color:#3b82f6}
.pc-ord tbody tr:hover td{background:rgba(255,255,255,.03)}
.pc-ord td.rt{text-align:right;font-variant-numeric:tabular-nums}
.pc-ord .badge{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;
               padding:2px 7px;border-radius:999px}
.pc-ord .b-pend{background:rgba(59,130,246,.2);color:#60a5fa}
.pc-ord .b-ok{background:rgba(20,83,45,.35);color:#4ade80}
.pc-ord .b-no{background:rgba(127,29,29,.35);color:#f87171}
.pc-ord .b-ret{background:rgba(100,116,139,.25);color:#94a3b8}
.pc-ord td.acts{text-align:right;white-space:nowrap}
.pc-ord .act{width:24px;height:24px;border-radius:6px;border:none;cursor:pointer;margin-left:4px;
             font-size:13px;line-height:1;background:rgba(255,255,255,.06);color:inherit}
.pc-ord .act.ok:hover{background:rgba(20,83,45,.5);color:#4ade80}
.pc-ord .act.no:hover{background:rgba(127,29,29,.5);color:#f87171}
.pc-ord .act.ed:hover{background:rgba(59,130,246,.4);color:#fff}
.pc-ord .qedit{width:52px;padding:2px 5px;border-radius:5px;font:13px ui-monospace,monospace;
               border:1.5px solid #3b82f6;background:var(--pc-input-bg,#1b2230);color:inherit;outline:none}
.pc-ord .empty{color:#64748b;text-align:center;padding:24px}
"""

_ORDERS_SOURCE = """
const STATUS = { pending:["pending","b-pend"], fulfilled:["fulfilled","b-ok"],
                 rejected:["not fulfilled","b-no"], retracted:["retracted","b-ret"] };
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
"""


# ── Leaderboard (everyone) ────────────────────────────────────────────────────
# Teams ranked by points, medals for the top three. Visible to admins, teams and
# spectators alike. Adapted from examples/leaderboard.py.

_LEADERBOARD_CSS = """
.pc-lb{padding:14px;height:100%;overflow:auto;box-sizing:border-box;
       font-family:system-ui,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-lb .hd{font-size:16px;font-weight:700;margin-bottom:14px}
.pc-lb table{width:100%;border-collapse:collapse;font-size:14px}
.pc-lb th{padding:8px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;
          text-transform:uppercase;letter-spacing:.05em;
          border-bottom:1px solid var(--pc-border,#30363d)}
.pc-lb td{padding:11px 12px;border-bottom:1px solid #1e293b}
.pc-lb tr:last-child td{border-bottom:none}
.pc-lb .pts{font-variant-numeric:tabular-nums;font-weight:800;text-align:right}
.pc-lb .rank{width:40px;text-align:center;font-size:18px;font-weight:700}
.pc-lb .name{font-weight:600}
.pc-lb .medal td{font-weight:700}
.pc-lb .empty{color:#64748b;padding:10px 4px;font-size:13px}
"""

_LEADERBOARD_SOURCE = """
function Component({ props }) {
  const rows = props.rows || [];
  const MEDAL = ["🥇","🥈","🥉"];
  const MEDAL_BG = ["#78350f","#334155","#431407"];  // gold / silver / bronze tints

  return (
    <div className="pc-lb">
      <div className="hd">🏆 Leaderboard</div>
      {rows.length === 0
        ? <div className="empty">No teams yet.</div>
        : <table>
            <thead>
              <tr><th className="rank">#</th><th>Team</th>
                  <th style={{textAlign:"right"}}>Points</th></tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const medal = i < 3;
                return (
                  <tr key={r.name} className={medal ? "medal" : ""}
                      style={medal ? { background: MEDAL_BG[i] + "55" } : {}}>
                    <td className="rank">{medal ? MEDAL[i] : i + 1}</td>
                    <td className="name">{r.name}</td>
                    <td className="pts">{r.points.toLocaleString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>}
    </div>
  );
}
"""


# ── Canvas layout ─────────────────────────────────────────────────────────────

# Layout, by role:
#   admin      board + Stock + Leaderboard (top), Teams + Orders (bottom)
#   a team     board + their ordering panel + Leaderboard (top row)
#   spectator  the Leaderboard only (the board is admin+team only, below)
# Panels keep one position for all viewers, so the board and the team/stock
# panels share x=500 — only one of them reaches any given viewer.

# The inventory board is for admins and teams, not spectators, so it carries
# explicit roles (admin + each team) and grows with new teams, like team_panel.
# "admin" is always present, so the list is never empty.
# Each panel keeps its stylesheet in a plain Python string passed as ``css=`` —
# the host renders it into a <style>, so there's no inline <style>/`.replace()`.

# The inventory board is for admins and teams, not spectators, so it carries
# explicit roles (admin + each team) and grows with new teams, like team_panel.
# "admin" is always present, so the list is never empty.
board = canvas.react(_BOARD_SOURCE, name="board", css=_BOARD_CSS,
                     x=40, y=40, w=440, h=560,
                     roles=["admin", *teams.keys()],
                     props={"rows": inv_rows()})

# Props are seeded from the loaded JSON so the admin sees the full catalogue,
# every team (budget + points) and the order log on the very first connect.
admin_stock = canvas.react(_STOCK_SOURCE, name="stock", css=_ADMIN_CSS,
                           x=500, y=40, w=380, h=560,
                           roles=["admin"],
                           props={"rows": inv_rows()})

admin_teams = canvas.react(_TEAMS_SOURCE, name="teams", css=_ADMIN_CSS,
                           x=40, y=620, w=440, h=340,
                           roles=["admin"],
                           props={"teams": team_rows(True)})

admin_orders = canvas.react(_ORDERS_SOURCE, name="orders", css=_ORDERS_CSS,
                            x=500, y=620, w=760, h=340,
                            roles=["admin"],
                            props={"log": log[:200]})

# Team roles are dynamic: seed with the teams already in the JSON, plus the
# sentinel so the list is never empty. New teams are appended in `team_add`.
# The team panel's per-team data (its name, budget, and orders) is delivered with
# update_for so no team ever receives another team's figures (see push_all); the
# seed props just cover the first render before the panel's ping arrives.
team_panel = canvas.react(_TEAM_SOURCE, name="team", css=_TEAM_CSS,
                          x=500, y=40, w=380, h=560,
                          roles=[TEAM_SENTINEL, *teams.keys()],
                          props={"rows": inv_rows(), "team": "", "budget": 0, "orders": []})

# The leaderboard is for everyone (roles=[] = all roles, incl. spectators).
leaderboard = canvas.react(_LEADERBOARD_SOURCE, name="leaderboard", css=_LEADERBOARD_CSS,
                           x=900, y=40, w=360, h=560,
                           props={"rows": leaderboard_rows()})


# ── Auth map (mutated live as the admin creates teams) ────────────────────────
# Passed by reference to serve(); the login check iterates it on every attempt,
# so adding a key here makes that password valid immediately — no restart.
PASSWORDS = {"admin": ADMIN_PASSWORD, "viewer": VIEWER_PASSWORD,
             **{name: t["password"] for name, t in teams.items()}}


def grant_team_view(name):
    """Give a role the read-only, chrome-free kiosk view (admins keep the full
    surface). Applies on that role's next connect."""
    canvas.set_view(read_only=True, ui=False, roles=[name])


grant_team_view("viewer")          # spectators: read-only, leaderboard only
for _name in teams:
    grant_team_view(_name)


# ── Wire the handlers (defined up top) to the panels ──────────────────────────
admin_stock.on_message(on_admin)
admin_teams.on_message(on_admin)
admin_orders.on_message(on_admin)
team_panel.on_message(on_team)


# Startup lint: catch JSX typos (a missing Component, unbalanced braces) here
# instead of as a cryptic error in the browser after someone connects.
for _name, _panel in [("board", board), ("stock", admin_stock),
                      ("teams", admin_teams), ("orders", admin_orders),
                      ("team", team_panel), ("leaderboard", leaderboard)]:
    for _issue in _panel.validate():
        print(f"[warn] {_name} panel source: {_issue}")


canvas.serve(
    port=8000,
    host="0.0.0.0",
    passwords=PASSWORDS,
    tunnel=True,
)
