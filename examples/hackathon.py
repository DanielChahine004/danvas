"""Hackathon control room: a JSON-backed shop + a live points leaderboard.

Teams spend a budget on resources from a catalogue and earn points from judges;
all state lives in three CSV files next to this script — ``hackathon_inventory.csv``
(the catalogue), ``hackathon_teams.csv`` (password + budget + points), and
``hackathon_orders.csv`` (the order log) — so it's easy to eyeball or edit in a
spreadsheet. They're loaded at startup and rewritten on every change, so a restart
resumes where it left off. On first run the old ``hackathon_data.json`` (if present)
is read once to seed the CSVs, after which the JSON is ignored.

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

import csv
import json
import os

import pycanvas

_HERE = os.path.dirname(__file__)
INVENTORY_CSV = os.path.join(_HERE, "hackathon_inventory.csv")
TEAMS_CSV = os.path.join(_HERE, "hackathon_teams.csv")
ORDERS_CSV = os.path.join(_HERE, "hackathon_orders.csv")
ANNOUNCEMENT_MD = os.path.join(_HERE, "hackathon_announcement.md")  # admin notice board
LEGACY_JSON = os.path.join(_HERE, "hackathon_data.json")  # one-time bootstrap only
ADMIN_PASSWORD = "admin"
VIEWER_PASSWORD = "view"     # spectator: read-only catalogue + leaderboard
DEFAULT_BUDGET = 2000

# A role string no password ever maps to. It keeps the team panel's role list
# non-empty: an empty `roles=[]` means "visible to everyone" (incl. admin), so
# even after the admin deletes every team the panel stays hidden from non-teams.
TEAM_SENTINEL = "__team__"

canvas = pycanvas.Canvas()


# ── Persistence ───────────────────────────────────────────────────────────────
# State lives in three CSV tables (inventory / teams / orders) so it can be read
# or edited in a spreadsheet. A team's identity is its *id* (the ``id`` column);
# the name is just a display field, so renaming a team never disturbs logins or
# order history (which reference the id). In memory: item->{stock,price},
# id->{name,password,budget,points}, and [orders] whose ``team`` is a team id.
# Columns are the canonical schema — extra columns are ignored, and a missing/
# empty file just loads as empty.

INVENTORY_COLS = ("item", "stock", "price")
TEAMS_COLS = ("id", "name", "password", "budget", "points")
ORDERS_COLS = ("id", "team", "item", "qty", "price", "cost", "status")


def _read_csv(path):
    """Rows of a CSV as dicts, or None if the file doesn't exist."""
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _bootstrap_from_json():
    """Seed CSV-shaped rows from the old single-file JSON on first run.

    Returns ``(inventory_rows, team_rows, order_rows)`` — the same dict-per-row
    shape a CSV would, so load() normalises both sources the same way. Empty lists
    if there's nothing to migrate. Carries the v1→v2 budget fix: v1 reserved a
    team's budget only at fulfilment, v2 reserves it at request, so back pending
    orders out once here. Runs at most once: the next save writes CSVs and the
    JSON is never read again.
    """
    if not os.path.exists(LEGACY_JSON):
        return [], [], []
    with open(LEGACY_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    inv, tms, lg = data.get("inventory", {}), data.get("teams", {}), data.get("log", [])
    if data.get("version", 1) < 2:
        for o in lg:
            if o.get("status") == "pending":
                t = tms.get(o.get("team"))
                if t is not None:
                    t["budget"] = t.get("budget", 0) - o.get("cost", 0)
    inv_rows = [{"item": k, "stock": v["stock"], "price": v["price"]}
                for k, v in inv.items()]
    # No id in the old JSON (teams were keyed by name); load() assigns one, and
    # orders still referencing a name are remapped to that id there.
    team_rows = [{"name": k, "password": v.get("password", ""),
                  "budget": v.get("budget", 0), "points": v.get("points", 0)}
                 for k, v in tms.items()]
    order_rows = [dict(o) for o in lg]
    return inv_rows, team_rows, order_rows


def _norm_teams(rows):
    """Build the id-keyed teams dict from raw rows, assigning a stable id to any
    row that lacks one (the pre-id schema, or a hand-added row). Returns
    ``(teams, name2id)``; the name→id map migrates orders that still reference a
    team by name (see load()) and is meaningless once orders hold ids."""
    teams, used, pending = {}, set(), []
    for r in rows or []:
        name = (str(r.get("name") or "")).strip()
        if not name:
            continue
        rec = {"name": name, "password": str(r.get("password") or ""),
               "budget": int(r.get("budget") or 0),
               "points": int(r.get("points") or 0)}
        tid = (str(r.get("id") or "")).strip()
        if tid and tid not in teams:
            teams[tid] = rec
            used.add(tid)
        else:
            pending.append(rec)   # missing or duplicate id -> assign one below
    seq = 0
    for rec in pending:
        seq += 1
        while f"t{seq}" in used:
            seq += 1
        used.add(f"t{seq}")
        teams[f"t{seq}"] = rec
    name2id = {rec["name"]: tid for tid, rec in teams.items()}
    return teams, name2id


def load():
    inv_rows = _read_csv(INVENTORY_CSV)
    team_rows_ = _read_csv(TEAMS_CSV)
    order_rows = _read_csv(ORDERS_CSV)
    if inv_rows is None and team_rows_ is None and order_rows is None:
        inv_rows, team_rows_, order_rows = _bootstrap_from_json()
    inventory = {r["item"]: {"stock": int(r["stock"]), "price": int(r["price"])}
                 for r in (inv_rows or []) if r.get("item")}
    teams, name2id = _norm_teams(team_rows_)
    log = [{"id": int(r["id"]), "team": str(r["team"]), "item": r["item"],
            "qty": int(r["qty"]), "price": int(r["price"]), "cost": int(r["cost"]),
            "status": r["status"]}
           for r in (order_rows or []) if r.get("id")]
    # One-time migration: orders that reference a team by name (the pre-id schema)
    # get rewritten to its id. A no-op once orders already hold ids.
    for o in log:
        if o["team"] not in teams and o["team"] in name2id:
            o["team"] = name2id[o["team"]]
    return inventory, teams, log


def save():
    _write_csv(INVENTORY_CSV, INVENTORY_COLS,
               ([k, v["stock"], v["price"]] for k, v in inventory.items()))
    _write_csv(TEAMS_CSV, TEAMS_COLS,
               ([tid, t["name"], t["password"], t["budget"], t["points"]]
                for tid, t in teams.items()))
    _write_csv(ORDERS_CSV, ORDERS_COLS,
               ([o["id"], o["team"], o["item"], o["qty"], o["price"], o["cost"], o["status"]]
                for o in log))


DEFAULT_ANNOUNCEMENT = (
    "# 📣 Welcome to the hackathon!\n\n"
    "Admins can edit this board live. Markdown works: **bold**, *italics*,\n"
    "`code`, [links](https://example.com), lists and headings.\n\n"
    "- Pinned info and rules go here\n"
    "- Updates appear instantly for every team\n"
)


def load_announcement():
    """The announcement board's Markdown, from its own .md file (human-editable
    in any text editor). Falls back to a friendly default on first run."""
    if os.path.exists(ANNOUNCEMENT_MD):
        with open(ANNOUNCEMENT_MD, "r", encoding="utf-8") as f:
            return f.read()
    return DEFAULT_ANNOUNCEMENT


def save_announcement(text):
    with open(ANNOUNCEMENT_MD, "w", encoding="utf-8") as f:
        f.write(text)


inventory, teams, log = load()
announcement = load_announcement()


def _max_team_seq():
    """Highest n among ids shaped ``t{n}``, so runtime ids continue the sequence
    without colliding with ones already assigned on load."""
    return max((int(tid[1:]) for tid in teams
                if tid[:1] == "t" and tid[1:].isdigit()), default=0)


_team_seq = _max_team_seq()


def next_team_id():
    global _team_seq
    _team_seq += 1
    return f"t{_team_seq}"


def team_name(tid):
    """Display name for a team id (falls back to the id, e.g. for an order that
    references a since-deleted team)."""
    t = teams.get(tid)
    return t["name"] if t else tid


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
    # ``id`` rides along so the admin panel can edit/remove/award by stable id.
    out = []
    for tid, t in teams.items():
        row = {"id": tid, "name": t["name"], "budget": t["budget"], "points": t["points"]}
        if with_passwords:
            row["password"] = t["password"]
        out.append(row)
    return out


def leaderboard_rows():
    """Teams ranked by points, highest first, with *standard competition ranking*
    — tied teams share a rank and the next rank skips accordingly (1, 2, 2, 4).
    The ``rank`` rides along so the leaderboard can give ties the same medal."""
    ranked = sorted(teams.items(), key=lambda kv: kv[1]["points"], reverse=True)
    rows = []
    rank = 0
    last_points = None
    for i, (tid, t) in enumerate(ranked):
        if t["points"] != last_points:
            rank = i + 1            # first team at this score sets the shared rank
            last_points = t["points"]
        rows.append({"id": tid, "name": t["name"], "points": t["points"], "rank": rank})
    return rows


def admin_order_rows():
    """The order log with each row's team id resolved to its display name, for the
    admin Orders table (orders store the id; admins read names)."""
    return [{**o, "team": team_name(o["team"])} for o in log[:200]]


def push_team(tid):
    """Send one team only *its* slice — catalogue, budget, and its own orders —
    addressed to its id-role with update(roles=...), so no other team's budget or
    orders are ever sent to it (privacy by construction, not client-side
    filtering). The role-scoped props persist, so they replay on reconnect."""
    team_panel.update(roles=tid,
                      team=teams[tid]["name"],
                      rows=inv_rows(),
                      budget=teams[tid]["budget"],
                      orders=[o for o in log if o["team"] == tid][:40])


def push_all():
    # Broadcast the shared panels...
    board.update(rows=inv_rows())
    admin_stock.update(rows=inv_rows())
    admin_teams.update(teams=team_rows(True))
    admin_orders.update(log=admin_order_rows())
    leaderboard.update(rows=leaderboard_rows())
    # ...then each team's private view, role by role.
    for tid in teams:
        push_team(tid)


# ── Request handlers ──────────────────────────────────────────────────────────
# All the app's behaviour lives here, next to the state it touches. They're
# registered on the panels down in the wiring section (the panels don't exist
# yet); every reference below — panels, PASSWORDS, grant_team_view — resolves at
# call time, i.e. once the canvas is serving.

def reload_data():
    """Re-read the three CSVs from disk so manual edits apply without a restart.

    State is replaced *in place* (clear + refill) so every existing reference —
    handlers and panels alike — keeps pointing at the live objects. Auth and the
    team/board panel roles are reconciled for teams that appeared or vanished in
    the CSV, mirroring what team_add/team_remove do for in-app edits.
    """
    global _order_seq, _team_seq, announcement
    new_inv, new_teams, new_log = load()
    old_ids = set(teams)
    inventory.clear(); inventory.update(new_inv)
    teams.clear(); teams.update(new_teams)
    log.clear(); log.extend(new_log)
    # Pick up an externally-edited announcement file too, and re-broadcast it.
    announcement = load_announcement()
    announce_display.update(announcement)
    announce_editor.update(text=announcement)
    normalize_log()                                   # backfill ids/cost on hand-edited rows
    _order_seq = max((o["id"] for o in log), default=0)
    _team_seq = _max_team_seq()
    # Reconcile panel roles for teams added/removed via the CSV. A team renamed in
    # the CSV keeps its id, so it isn't in either diff — no role churn, and its
    # connected members keep their session and panels.
    new_ids = set(teams)
    for tid in new_ids - old_ids:
        team_panel.add_role(tid)
        board.add_role(tid)
        grant_team_view(tid)
    for tid in old_ids - new_ids:
        team_panel.remove_role(tid)
        board.remove_role(tid)
    # Rebuild the auth map in place so edited/removed passwords take effect too.
    PASSWORDS.clear()
    PASSWORDS.update({"admin": ADMIN_PASSWORD, "viewer": VIEWER_PASSWORD,
                      **{tid: t["password"] for tid, t in teams.items()}})


def on_admin(msg, viewer):
    # One handler for both admin panels — stock actions come from the stock
    # panel, team actions from the teams panel; we dispatch on `action`.
    if viewer.get("role") != "admin":
        return  # the panels only reach admins, but authorize server-side too
    action = msg.get("action")

    if action == "announce_set":
        # The announcement is its own bit of state (a Markdown file), unrelated to
        # the CSV catalogue/teams/orders, so it saves + broadcasts on its own and
        # skips the shared save()/push_all() at the end.
        global announcement
        announcement = msg.get("text", "")
        save_announcement(announcement)
        announce_display.update(announcement)       # everyone sees the rendered board
        announce_editor.update(text=announcement)   # persist for admins who connect later
        print(f"[admin] announcement updated ({len(announcement)} chars)")
        return

    if action == "reload":
        # Pull manual edits to the CSVs back into memory. Falls through to the
        # save() + push_all() below, which re-broadcasts the freshly loaded state
        # (and normalises the files on disk).
        reload_data()
        print(f"[admin] reloaded from CSV: {len(inventory)} items, "
              f"{len(teams)} teams, {len(log)} orders")

    elif action == "item_set":
        name = (msg.get("item") or "").strip()
        if not name:
            return
        inventory[name] = {"stock": max(0, int(msg.get("stock", 0))),
                           "price": max(0, int(msg.get("price", 0)))}
        print(f"[admin] set {name}: ${inventory[name]['price']} / {inventory[name]['stock']} in stock")

    elif action == "item_remove":
        inventory.pop(msg.get("item", ""), None)

    elif action == "team_add":
        tid = (msg.get("id") or "").strip()
        name = (msg.get("name") or "").strip()
        pw = (msg.get("password") or "").strip()
        if not name or not pw or name == "admin":
            return
        budget = max(0, int(msg.get("budget", DEFAULT_BUDGET)))
        if tid and tid in teams:
            # Edit an existing team — including a *rename*. The id (and so the role
            # and order history) is unchanged, so connected members keep their
            # session and panels; the new name just propagates via push_all.
            t = teams[tid]
            t["name"], t["password"], t["budget"] = name, pw, budget
            PASSWORDS[tid] = pw
            print(f"[admin] updated team {tid} -> {name!r} (budget ${budget})")
        else:
            tid = next_team_id()
            teams[tid] = {"name": name, "password": pw, "budget": budget, "points": 0}
            PASSWORDS[tid] = pw
            team_panel.add_role(tid)   # team + board panels now admit this role, live
            board.add_role(tid)
            grant_team_view(tid)
            print(f"[admin] created team {name!r} as {tid} "
                  f"(password {pw!r}, budget ${budget})")

    elif action == "team_remove":
        tid = msg.get("id", "")
        if tid in teams:
            name = teams[tid]["name"]
            teams.pop(tid)
            PASSWORDS.pop(tid, None)
            team_panel.remove_role(tid)   # drop the panels from that role, live
            board.remove_role(tid)
            print(f"[admin] removed team {name!r} ({tid})")

    elif action == "award":
        tid = msg.get("id", "")
        if tid in teams:
            teams[tid]["points"] += int(msg.get("points", 0))
            print(f"[admin] {teams[tid]['name']} -> {teams[tid]['points']} pts")

    elif action == "order_fulfil":
        o = find_order(msg.get("id"))
        if o is None or o["status"] == "fulfilled":
            return
        if not set_order_status(o, "fulfilled"):   # takes stock (budget already held)
            print(f"[admin] can't fulfil order #{o['id']} "
                  f"({o['qty']}x {o['item']} for {team_name(o['team'])}): not enough stock/budget")
            return
        print(f"[admin] fulfilled #{o['id']}: {o['qty']}x {o['item']} -> {team_name(o['team'])}")

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
    team = viewer.get("role")   # the team's stable id (orders reference it)
    if team not in teams:
        return
    name = teams[team]["name"]
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
        want = {}   # item -> total qty requested across the cart
        for entry in msg.get("cart", []):
            item = entry.get("item", "")
            qty = max(1, int(entry.get("qty", 1)))
            if item not in inventory:
                continue
            price = inventory[item]["price"]
            lines.append((item, qty, price))
            total += price * qty
            want[item] = want.get(item, 0) + qty
        # Atomic: file nothing unless the team can afford the whole cart *and*
        # no item is requested beyond its current stock (the UI pre-checks both).
        if not lines or teams[team]["budget"] < total:
            return
        if any(qty > inventory[item]["stock"] for item, qty in want.items()):
            print(f"[{name}] request rejected: over stock")
            return
        for item, qty, price in lines:
            order = {"id": next_order_id(), "team": team, "item": item,
                     "qty": qty, "price": price, "cost": price * qty,
                     "status": "pending"}
            log.insert(0, order)
            reserve_budget(order)   # budget goes down on request
        print(f"[{name}] filed {len(lines)} line(s) for ${total:,} "
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
        print(f"[{name}] retracted order #{o['id']} (budget back: ${teams[team]['budget']:,})")
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
.pc-sb .empty{color:#64748b;font-size:13px;padding:12px 4px}
"""

_BOARD_SOURCE = """
function Component({ props }) {
  const rows  = props.rows || [];
  const ICONS = {Laptop:"💻",Monitor:"🖥️",Keyboard:"⌨️",Mouse:"🖱️",Headset:"🎧"};
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
              <StockChip n={r.stock} />
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
.pc-sa .hd.hd-row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.pc-sa .reload{padding:5px 10px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;
               border:1.5px solid var(--pc-border,#30363d);background:transparent;color:#94a3b8;
               transition:border-color .12s,color .12s}
.pc-sa .reload:hover{border-color:#3b82f6;color:var(--pc-text,#e6edf3)}
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
.pc-sa .row.editing{background:rgba(59,130,246,.12);box-shadow:inset 2px 0 0 #3b82f6}
.pc-sa .back{flex:1 1 100%;margin-top:4px;text-align:center;font-size:11px;color:#64748b;cursor:pointer}
.pc-sa .back:hover{color:var(--pc-text,#e6edf3)}
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
      <div className="hd hd-row">
        <span>⚙️ Stock Control</span>
        <button className="reload" title="Reload inventory, teams & orders from the CSV files on disk"
          onClick={()=>canvas.send({ action:"reload" })}>↻ Reload CSV</button>
      </div>

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

  // tm.id distinguishes editing an existing team (id set — incl. renaming it)
  // from creating a new one (id ""). The id is the team's stable identity.
  const [tm, setTm] = React.useState({ id:"", name:"", password:"", budget:"2000" });
  const [aw, setAw] = React.useState({ team:"", points:"" });  // aw.team is a team id

  function submitTeam() {
    const name = tm.name.trim();
    const password = tm.password.trim();
    const budget = parseInt(tm.budget, 10);
    if (!name || !password || isNaN(budget)) return;
    canvas.send({ action:"team_add", id:tm.id, name, password, budget });
    setTm({ id:"", name:"", password:"", budget:"2000" });
  }
  function award(delta) {
    const team = aw.team;
    const pts = delta != null ? delta : parseInt(aw.points, 10);
    if (!team || isNaN(pts) || pts === 0) return;
    canvas.send({ action:"award", id:team, points:pts });
    if (delta == null) setAw({ ...aw, points:"" });
  }

  return (
    <div className="pc-sa">
      <div className="hd">👥 Teams</div>

      <div className="sec">
        <div className="sec-hd">{tm.id ? "Edit team — rename / set password & budget" : "Add team"}</div>
        <div className="frm">
          <input className="inp grow" placeholder="Team name" value={tm.name}
            onChange={e=>setTm({...tm, name:e.target.value})} />
          <input className="inp num" placeholder="Password" value={tm.password}
            onChange={e=>setTm({...tm, password:e.target.value})} />
          <input className="inp num" type="number" min="0" placeholder="Budget" value={tm.budget}
            onChange={e=>setTm({...tm, budget:e.target.value})}
            onKeyDown={e=>{ if(e.key==="Enter") submitTeam(); }} />
          <button className="btn" onClick={submitTeam}>{tm.id ? "Save changes" : "Add team"}</button>
          {tm.id &&
            <div className="back" onClick={()=>setTm({ id:"", name:"", password:"", budget:"2000" })}>
              + add a new team instead
            </div>}
        </div>
      </div>

      <div className="sec">
        <div className="sec-hd">Award points</div>
        <div className="frm">
          <select className="inp grow" value={aw.team}
            onChange={e=>setAw({...aw, team:e.target.value})}>
            <option value="">— select team —</option>
            {teams.map(t => <option key={t.id} value={t.id}>{t.name} ({t.points} pts)</option>)}
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
              <div key={t.id} className={"row" + (t.id === tm.id ? " editing" : "")}
                   title="Click to edit"
                   onClick={()=>setTm({ id:t.id, name:t.name, password:t.password, budget:String(t.budget) })}>
                <span className="nm">{t.name}</span>
                <span className="pw">{t.password}</span>
                <span className="bud">${t.budget.toLocaleString()}</span>
                <span className="pts">{t.points} pts</span>
                <button className="x" title="Remove team"
                  onClick={e=>{ e.stopPropagation(); canvas.send({ action:"team_remove", id:t.id }); }}>×</button>
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
  // update(roles=...) — the panel never receives another team's figures, so
  // there's nothing to filter and no need to read our own identity.
  const rows   = props.rows   || [];
  const myTeam = props.team   || "";
  const budget = props.budget || 0;     // already net of reserved orders
  const myLog  = props.orders || [];
  const ICONS = {Laptop:"💻",Monitor:"🖥️",Keyboard:"⌨️",Mouse:"🖱️",Headset:"🎧"};
  const priceOf = (item) => { const r = rows.find(x => x.item === item); return r ? r.price : 0; };
  const stockOf = (item) => { const r = rows.find(x => x.item === item); return r ? r.stock : 0; };

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
                                     cost: priceOf(c.item) * c.qty,
                                     stock: stockOf(c.item) }));
  const cartTotal = cartLines.reduce((s, l) => s + l.cost, 0);
  const afterFiling = budget - cartTotal;          // what we'd have left once filed
  const overStock = cartLines.some(l => l.qty > l.stock);   // any line beyond stock

  function pick(r) { if (r.stock > 0) { setSel(r); setQty(1); setMsg(null); } }
  function incQty(d) { if (sel) setQty(q => Math.max(1, Math.min(sel.stock, q + d))); }
  function addToCart() {
    if (!sel) return;
    setCart(c => {
      const i = c.findIndex(x => x.item === sel.item);
      if (i >= 0) { const n = [...c]; n[i] = { ...n[i], qty: Math.min(sel.stock, n[i].qty + qty) }; return n; }
      return [...c, { item: sel.item, qty }];
    });
    setMsg("Added " + qty + "× " + sel.item + " to cart");
    setSel(null); setQty(1);
  }
  function setLineQty(item, q) {
    setCart(c => c.map(x => x.item === item ? { ...x, qty: Math.max(1, Math.min(stockOf(item), q)) } : x));
  }
  function removeLine(item) { setCart(c => c.filter(x => x.item !== item)); }
  function fileRequest() {
    if (!cart.length || afterFiling < 0 || overStock) return;
    canvas.send({ action:"file", cart: cart.map(c => ({ item: c.item, qty: c.qty })) });
    setMsg("✓ Filed " + cart.length + " line(s) for $" + cartTotal.toLocaleString());
    setCart([]);
  }
  function retract(id) { canvas.send({ action:"retract", id }); }

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
                  <StockChip n={r.stock} />
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
          <button className="btn" onClick={fileRequest} disabled={afterFiling < 0 || overStock}>
            {overStock ? "Not enough stock"
              : afterFiling < 0 ? "Over budget"
              : "File request — $" + cartTotal.toLocaleString()}
          </button>
        </div>
      )}

      <div className="sec">
        <div className="sec-hd">Your orders</div>
        {myLog.length === 0
          ? <div className="empty">No orders yet.</div>
          : myLog.map(r => {
              const st = ({pending:["awaiting","st-pend"], fulfilled:["fulfilled","st-ok"],
                           rejected:["declined","st-no"], retracted:["retracted","st-ret"]})[r.status]
                         || ["awaiting","st-pend"];
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
"""


# ── Leaderboard (everyone) ────────────────────────────────────────────────────
# Teams ranked by points: a three-tier podium for the top three, then a runners-up
# table for the rest. Visible to admins, teams and spectators alike.

_LEADERBOARD_CSS = """
.pc-lb{padding:14px;height:100%;overflow:auto;box-sizing:border-box;
       font-family:system-ui,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-lb .hd{font-size:16px;font-weight:700;margin-bottom:10px}

/* ── Podium (top 3) ── */
.pc-lb .podium{display:flex;align-items:flex-end;justify-content:center;gap:8px;
               margin:8px 0 18px;min-height:150px}
.pc-lb .col{flex:1 1 0;max-width:120px;display:flex;flex-direction:column;align-items:center;
            animation:lb-rise 1.1s cubic-bezier(.2,.8,.2,1) both}  /* delay set inline, per place */
@keyframes lb-rise{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
.pc-lb .crown{font-size:22px;line-height:1;margin-bottom:1px;
              filter:drop-shadow(0 0 7px rgba(251,191,36,.85))}
.pc-lb .ava{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;
            justify-content:center;font-size:26px;margin-bottom:6px;border:2px solid #475569;
            background:radial-gradient(circle at 50% 35%,rgba(255,255,255,.14),rgba(255,255,255,.02))}
.pc-lb .ava.g1{border-color:#fbbf24;box-shadow:0 0 16px rgba(251,191,36,.6)}
.pc-lb .ava.g2{border-color:#cbd5e1;box-shadow:0 0 11px rgba(203,213,225,.45)}
.pc-lb .ava.g3{border-color:#f59e0b;box-shadow:0 0 11px rgba(245,158,11,.4)}
.pc-lb .pname{font-size:12px;font-weight:700;max-width:100%;white-space:nowrap;overflow:hidden;
              text-overflow:ellipsis;text-align:center;line-height:1.2}
.pc-lb .ppts{font-size:12px;font-weight:800;color:#fbbf24;
             font-variant-numeric:tabular-nums;margin:1px 0 8px}
.pc-lb .ped{width:100%;border-radius:9px 9px 0 0;display:flex;align-items:flex-start;
            justify-content:center;padding-top:7px;font-size:22px;font-weight:900;
            color:rgba(255,255,255,.92);text-shadow:0 1px 2px rgba(0,0,0,.4);
            box-shadow:inset 0 2px 0 rgba(255,255,255,.18),0 -1px 0 rgba(0,0,0,.25)}
.pc-lb .ped.r1{height:96px;background:linear-gradient(180deg,#fbbf24,#b45309)}
.pc-lb .ped.r2{height:68px;background:linear-gradient(180deg,#e2e8f0,#64748b)}
.pc-lb .ped.r3{height:48px;background:linear-gradient(180deg,#f59e0b,#7c2d12)}

/* ── Runners-up (rank 4+) ── */
.pc-lb .rest-hd{font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
                color:#64748b;margin:0 2px 4px}
.pc-lb table{width:100%;border-collapse:collapse;font-size:14px}
.pc-lb td{padding:9px 12px;border-bottom:1px solid #1e293b}
.pc-lb tr:last-child td{border-bottom:none}
.pc-lb .pts{font-variant-numeric:tabular-nums;font-weight:800;text-align:right}
.pc-lb .rank{width:40px;text-align:center;font-size:15px;font-weight:700;color:#94a3b8}
.pc-lb .name{font-weight:600}
.pc-lb .empty{color:#64748b;padding:10px 4px;font-size:13px}
"""

_LEADERBOARD_SOURCE = """
function Component({ props }) {
  const rows = props.rows || [];
  const MEDAL = ["🥇","🥈","🥉"];
  // Python sends a `rank` per row (standard competition ranking), so ties share
  // a place — and thus a medal, colour and plinth height. The podium is every
  // team placing 1st–3rd (a tie can mean two golds, or no silver, etc.).
  const podium = rows.filter(r => r.rank <= 3);
  const rest   = rows.filter(r => r.rank >= 4);
  // Lay out the places silver | gold | bronze so the winners sit in the middle;
  // a place left empty by a tie simply contributes no column.
  const atPlace = p => podium.filter(r => r.rank === p);
  const order = [...atPlace(2), ...atPlace(1), ...atPlace(3)];
  // Reveal builds up to the winner: 3rd rises first, then 2nd, then 1st last.
  // Keyed by place, so tied teams (same rank) rise together.
  const DELAY = { 1: 2, 2: 1, 3: 0.5 };

  return (
    <div className="pc-lb">
      <div className="hd">🏆 Leaderboard</div>

      {rows.length === 0 && <div className="empty">No teams yet.</div>}

      {podium.length > 0 &&
        <div className="podium">
          {order.map(r => (
            <div key={r.id} className="col" style={{ animationDelay: DELAY[r.rank] + "s" }}>
              {r.rank === 1 && <div className="crown">👑</div>}
              <div className={"ava g" + r.rank}>{MEDAL[r.rank - 1]}</div>
              <div className="pname" title={r.name}>{r.name}</div>
              <div className="ppts">{r.points.toLocaleString()} pts</div>
              <div className={"ped r" + r.rank}>{r.rank}</div>
            </div>
          ))}
        </div>}

      {rest.length > 0 &&
        <div className="rest">
          <div className="rest-hd">Runners-up</div>
          <table><tbody>
            {rest.map(r => (
              <tr key={r.id}>
                <td className="rank">{r.rank}</td>
                <td className="name">{r.name}</td>
                <td className="pts">{r.points.toLocaleString()}</td>
              </tr>
            ))}
          </tbody></table>
        </div>}
    </div>
  );
}
"""


# ── Announcements editor (admin only) ─────────────────────────────────────────
# The rendered board everyone sees is a built-in Markdown panel (canvas.markdown,
# Python-rendered). This admin-only panel edits its source: a textarea with a live
# client-side preview and a Publish button that broadcasts + persists. The preview
# is a tiny Markdown subset just for instant feedback; the published board uses
# Python's renderer (richer). Raw string so the regex backslashes survive.

_ANNOUNCE_CSS = """
.pc-ann{display:flex;flex-direction:column;height:100%;box-sizing:border-box;padding:14px;
        font-family:system-ui,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-ann .hd{font-size:16px;font-weight:700;margin-bottom:10px;
            display:flex;align-items:center;justify-content:space-between;gap:8px}
.pc-ann .st{font-size:11px;font-weight:700;color:#4ade80}
.pc-ann .st.dirty{color:#fbbf24}
.pc-ann .ta{width:100%;min-height:90px;flex:0 0 auto;resize:vertical;box-sizing:border-box;
            padding:9px 11px;border-radius:8px;border:1.5px solid var(--pc-border,#30363d);
            background:var(--pc-input-bg,#1b2230);color:inherit;outline:none;
            font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
.pc-ann .ta:focus{border-color:#3b82f6}
.pc-ann .row{display:flex;align-items:center;gap:8px;margin:8px 0}
.pc-ann .btn{padding:7px 14px;border-radius:7px;font-size:13px;font-weight:600;cursor:pointer;
             border:none;background:#2563eb;color:#fff;transition:background .12s}
.pc-ann .btn:not(:disabled):hover{background:#1d4ed8}
.pc-ann .btn:disabled{opacity:.45;cursor:not-allowed}
.pc-ann .btn.ghost{background:transparent;border:1.5px solid var(--pc-border,#30363d);color:#94a3b8}
.pc-ann .btn.ghost:not(:disabled):hover{border-color:#3b82f6;color:var(--pc-text,#e6edf3)}
.pc-ann .hint{font-size:11px;color:#64748b;margin-left:auto;text-align:right}
.pc-ann .prev-hd{font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
                 color:#64748b;margin:4px 2px 6px}
.pc-ann .prev{flex:1;overflow:auto;border:1px solid var(--pc-border,#30363d);border-radius:8px;
              padding:10px 12px;font-size:13px;line-height:1.5;background:rgba(255,255,255,.02)}
.pc-ann .prev h1,.pc-ann .prev h2,.pc-ann .prev h3{margin:.4em 0 .3em;line-height:1.25}
.pc-ann .prev h1{font-size:1.5em}.pc-ann .prev h2{font-size:1.3em}.pc-ann .prev h3{font-size:1.1em}
.pc-ann .prev p{margin:.4em 0}.pc-ann .prev ul,.pc-ann .prev ol{margin:.4em 0;padding-left:1.4em}
.pc-ann .prev a{color:#60a5fa}
.pc-ann .prev code{background:rgba(255,255,255,.08);border-radius:4px;padding:1px 4px;
                   font:12px ui-monospace,monospace}
.pc-ann .prev pre{background:rgba(255,255,255,.06);border-radius:6px;padding:8px;overflow:auto}
.pc-ann .prev pre code{background:none;padding:0}
.pc-ann .prev blockquote{margin:.4em 0;padding:2px 10px;border-left:3px solid #3b82f6;color:#94a3b8}
.pc-ann .prev hr{border:none;border-top:1px solid var(--pc-border,#30363d);margin:.6em 0}
"""

_ANNOUNCE_SOURCE = r"""
// Compact Markdown -> HTML for the *preview* only (instant, client-side). The
// published board is rendered by Python's richer converter. (Regexes here avoid
// char classes with brackets/backticks so the startup lint's brace-scan stays
// happy; lazy groups do the same job.)
function annEsc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function annInline(s){
  return annEsc(s)
    .replace(/`(.+?)`/g,"<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>")
    .replace(/\*(.+?)\*/g,"<em>$1</em>")
    .replace(/\[(.+?)\]\((.+?)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function annFence(l){ return l.trim().slice(0,3) === "" + "``" + "`"; }
function annIsBlock(l){
  return /^(#{1,3})\s+/.test(l) || /^\s*([-*]|\d+\.)\s+/.test(l)
      || /^\s*>/.test(l) || annFence(l) || /^\s*(---|\*\*\*)\s*$/.test(l);
}
function annMd(src){
  const lines=(src||"").split("\n"); const out=[]; let i=0;
  while(i<lines.length){
    const ln=lines[i];
    if(annFence(ln)){ i++; const code=[];
      while(i<lines.length && !annFence(lines[i])){ code.push(annEsc(lines[i])); i++; }
      i++; out.push("<pre><code>"+code.join("\n")+"</code></pre>"); continue; }
    const h=ln.match(/^(#{1,3})\s+(.*)/);
    if(h){ const l=h[1].length; out.push("<h"+l+">"+annInline(h[2])+"</h"+l+">"); i++; continue; }
    if(/^\s*(---|\*\*\*)\s*$/.test(ln)){ out.push("<hr>"); i++; continue; }
    if(/^\s*>\s?/.test(ln)){ const q=[];
      while(i<lines.length && /^\s*>\s?/.test(lines[i])){ q.push(annInline(lines[i].replace(/^\s*>\s?/,""))); i++; }
      out.push("<blockquote>"+q.join("<br>")+"</blockquote>"); continue; }
    if(/^\s*([-*]|\d+\.)\s+/.test(ln)){ const ordered=/^\s*\d+\.\s+/.test(ln); const items=[];
      while(i<lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])){
        items.push("<li>"+annInline(lines[i].replace(/^\s*([-*]|\d+\.)\s+/,""))+"</li>"); i++; }
      out.push((ordered?"<ol>":"<ul>")+items.join("")+(ordered?"</ol>":"</ul>")); continue; }
    if(!ln.trim()){ i++; continue; }
    const para=[];
    while(i<lines.length && lines[i].trim() && !annIsBlock(lines[i])){ para.push(annInline(lines[i])); i++; }
    out.push("<p>"+para.join("<br>")+"</p>");
  }
  return out.join("");
}

function Component({ canvas, props }) {
  const published = props.text || "";
  const [draft, setDraft] = React.useState(published);
  // Re-sync when the published text changes server-side (reload, another admin).
  React.useEffect(() => { setDraft(published); }, [published]);
  const dirty = draft !== published;

  function publish() { canvas.send({ action: "announce_set", text: draft }); }

  return (
    <div className="pc-ann">
      <div className="hd">
        <span>📣 Edit announcement</span>
        <span className={"st" + (dirty ? " dirty" : "")}>{dirty ? "● unpublished" : "✓ published"}</span>
      </div>
      <textarea className="ta" value={draft} spellCheck={false}
        placeholder="Write in Markdown…  # heading, **bold**, - list, [link](url)"
        onChange={e=>setDraft(e.target.value)}
        onKeyDown={e=>{ if((e.ctrlKey||e.metaKey) && e.key==="Enter") publish(); }} />
      <div className="row">
        <button className="btn" onClick={publish} disabled={!dirty}>Publish</button>
        <button className="btn ghost" onClick={()=>setDraft(published)} disabled={!dirty}>Revert</button>
        <span className="hint">Ctrl+Enter to publish</span>
      </div>
      <div className="prev-hd">Live preview</div>
      <div className="prev" dangerouslySetInnerHTML={{ __html: annMd(draft) }} />
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

# Placement is anchor-based: only the board carries absolute x/y, and every other
# panel hangs off a neighbour with a uniform GAP — so there are no scattered magic
# coordinates and the row/column reflows if a panel's size changes. (Panels still
# carry w/h: these dashboard cards have intended sizes, and right_of needs the
# anchor's width.) Note admin_stock and team_panel anchor to the *same* spot
# (right_of=board): only one ever reaches a given viewer — admins vs teams — so
# they overlap by design rather than fighting for the slot.
GAP = 20

# ── Shared UI kit (canvas.define / canvas.style) ──────────────────────────────
# A couple of elements recur across panels: the stock-level chip (inventory board
# + each team's catalogue) and the order-status colours (a team's order list +
# the admin Orders table). Define the chip once and put the shared colours in one
# stylesheet here, instead of re-declaring the JSX helper and re-pasting the CSS
# into every panel — panels just render <StockChip n={…}/> and use the .st-* /
# .chip colours. Registered before the panels (replayed to each browser on
# connect, ahead of the panels that use them).
canvas.define("StockChip", """
function StockChip({ n }) {
  if (n === 0) return <span className="chip out">Out of stock</span>;
  if (n <= 2)  return <span className="chip low">{n} left</span>;
  return             <span className="chip in">{n} in stock</span>;
}
""")

canvas.style("""
/* stock-level chip — shared by the inventory board and each team's catalogue */
.chip{padding:3px 9px;border-radius:999px;font-size:11px;font-weight:700}
.chip.in {background:rgba(20,83,45,.35);color:#4ade80;border:1px solid rgba(74,222,128,.2)}
.chip.low{background:rgba(120,53,15,.35);color:#fbbf24;border:1px solid rgba(251,191,36,.2)}
.chip.out{background:rgba(127,29,29,.35);color:#f87171;border:1px solid rgba(248,113,113,.2)}
/* order-status colours — shared by a team's order list and the admin Orders
   table; each panel keeps its own pill base (.ostat / .badge). */
.st-pend{background:rgba(59,130,246,.2);color:#60a5fa}
.st-ok{background:rgba(20,83,45,.35);color:#4ade80}
.st-no{background:rgba(127,29,29,.35);color:#f87171}
.st-ret{background:rgba(100,116,139,.25);color:#94a3b8}
""")


# Top row, left -> right:  board | stock·team | leaderboard | announcements
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
                           right_of=board, gap=GAP, w=380, h=560,
                           roles=["admin"],
                           props={"rows": inv_rows()})

# Team roles are dynamic: seed with the teams already in the JSON, plus the
# sentinel so the list is never empty. New teams are appended in `team_add`.
# The team panel's per-team data (its name, budget, and orders) is delivered with
# update(roles=...) so no team ever receives another team's figures (see
# push_all); the seed props just cover the first render before that arrives.
# Same anchor as admin_stock — admins see stock there, teams see this.
team_panel = canvas.react(_TEAM_SOURCE, name="team", css=_TEAM_CSS,
                          right_of=board, gap=GAP, w=380, h=560,
                          roles=[TEAM_SENTINEL, *teams.keys()],
                          props={"rows": inv_rows(), "team": "", "budget": 0, "orders": []})

# The leaderboard is for everyone (roles=[] = all roles, incl. spectators).
leaderboard = canvas.react(_LEADERBOARD_SOURCE, name="leaderboard", css=_LEADERBOARD_CSS,
                           right_of=admin_stock, gap=GAP, w=360, h=560,
                           props={"rows": leaderboard_rows()}, frame=False, grabbable=False)

# Announcements. The rendered board is a built-in Markdown panel visible to
# everyone (no roles); the admin-only editor below it publishes new Markdown
# (broadcast + saved to hackathon_announcement.md). Rightmost column.
announce_display = canvas.markdown(announcement, name="announce",
                                   label="📣 Announcements",
                                   right_of=leaderboard, gap=GAP, w=560, h=560, frame=False, grabbable=False)

# Bottom row (admin only): Teams under the board, Orders under the stock panel.
admin_teams = canvas.react(_TEAMS_SOURCE, name="teams", css=_ADMIN_CSS,
                           below=board, gap=GAP, w=440, h=700,
                           roles=["admin"],
                           props={"teams": team_rows(True)})

admin_orders = canvas.react(_ORDERS_SOURCE, name="orders", css=_ORDERS_CSS,
                            below=admin_stock, gap=GAP, w=760, h=560,
                            roles=["admin"],
                            props={"log": admin_order_rows()})

announce_editor = canvas.react(_ANNOUNCE_SOURCE, name="announce_edit", css=_ANNOUNCE_CSS,
                               below=announce_display, gap=GAP, w=560, h=560,
                               roles=["admin"],
                               props={"text": announcement})


# ── Auth map (mutated live as the admin creates teams) ────────────────────────
# Passed by reference to serve(); the login check iterates it on every attempt,
# so adding a key here makes that password valid immediately — no restart.
PASSWORDS = {"admin": ADMIN_PASSWORD, "viewer": VIEWER_PASSWORD,
             **{tid: t["password"] for tid, t in teams.items()}}


def grant_team_view(role):
    """Give a role the read-only, chrome-free kiosk view (admins keep the full
    surface). Applies on that role's next connect."""
    canvas.set_view(read_only=True, ui=False, roles=[role])


grant_team_view("viewer")          # spectators: read-only, leaderboard only
for _tid in teams:
    grant_team_view(_tid)


# ── Wire the handlers (defined up top) to the panels ──────────────────────────
admin_stock.on_message(on_admin)
admin_teams.on_message(on_admin)
admin_orders.on_message(on_admin)
announce_editor.on_message(on_admin)
team_panel.on_message(on_team)


# Startup lint: catch JSX typos (a missing Component, unbalanced braces) here
# instead of as a cryptic error in the browser after someone connects.
for _name, _panel in [("board", board), ("stock", admin_stock),
                      ("teams", admin_teams), ("orders", admin_orders),
                      ("team", team_panel), ("leaderboard", leaderboard),
                      ("announce_edit", announce_editor)]:
    for _issue in _panel.validate():
        print(f"[warn] {_name} panel source: {_issue}")


canvas.serve(
    port=8000,
    host="0.0.0.0",
    passwords=PASSWORDS,
    tunnel=True,
    ui_inspector=False,
    hot_reload=True
)