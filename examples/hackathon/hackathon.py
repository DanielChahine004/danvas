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

# Each panel's frontend lives in panels/: a .jsx component and a sibling .css,
# loaded with canvas.react(path=…, css_path=…). This keeps the Python file
# logic-only — no JSX/CSS embedded as string literals.
PANELS_DIR = os.path.join(_HERE, "panels")


def _panel(name):
    """Absolute path to a panels/ file (for path= / css_path= / define)."""
    return os.path.join(PANELS_DIR, name)


def _read(name):
    """Contents of a panels/ file (for canvas.style(…), which takes a string)."""
    with open(_panel(name), encoding="utf-8") as f:
        return f.read()


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


def commit():
    """Persist the CSVs and re-broadcast every panel — the shared tail of every
    catalogue/team/order mutation. (The announcement is separate state and
    broadcasts on its own; see admin_announce_set.)"""
    save()
    push_all()


# Each browser action is one named handler, wired to its panel in the wiring
# section with @panel.on("action", fields=…) — no central if/elif. The admin
# panels are roles=["admin"], so only admins reach them; the per-handler role
# check is defence-in-depth (authorize server-side too). Numeric inputs declare
# ``fields=`` at the wiring so they arrive coerced, and a malformed value drops
# that message instead of crashing the handler.

# -- admin: stock control (the Stock panel) -----------------------------------
def admin_reload(msg, viewer):
    if viewer.get("role") != "admin":
        return
    reload_data()   # pull manual edits to the CSVs back into memory
    print(f"[admin] reloaded from CSV: {len(inventory)} items, "
          f"{len(teams)} teams, {len(log)} orders")
    commit()


def admin_item_set(msg, viewer):
    if viewer.get("role") != "admin":
        return
    name = (msg.get("item") or "").strip()
    if not name:
        return
    inventory[name] = {"stock": max(0, msg.get("stock", 0)),
                       "price": max(0, msg.get("price", 0))}
    print(f"[admin] set {name}: ${inventory[name]['price']} / {inventory[name]['stock']} in stock")
    commit()


def admin_item_remove(msg, viewer):
    if viewer.get("role") != "admin":
        return
    inventory.pop(msg.get("item", ""), None)
    commit()


# -- admin: teams + points (the Teams panel) ----------------------------------
def admin_team_add(msg, viewer):
    if viewer.get("role") != "admin":
        return
    tid = (msg.get("id") or "").strip()
    name = (msg.get("name") or "").strip()
    pw = (msg.get("password") or "").strip()
    if not name or not pw or name == "admin":
        return
    budget = max(0, msg.get("budget", DEFAULT_BUDGET))
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
    commit()


def admin_team_remove(msg, viewer):
    if viewer.get("role") != "admin":
        return
    tid = msg.get("id", "")
    if tid in teams:
        name = teams[tid]["name"]
        teams.pop(tid)
        PASSWORDS.pop(tid, None)
        team_panel.remove_role(tid)   # drop the panels from that role, live
        board.remove_role(tid)
        print(f"[admin] removed team {name!r} ({tid})")
    commit()


def admin_award(msg, viewer):
    if viewer.get("role") != "admin":
        return
    tid = msg.get("id", "")
    if tid in teams:
        teams[tid]["points"] += msg.get("points", 0)
        print(f"[admin] {teams[tid]['name']} -> {teams[tid]['points']} pts")
    commit()


# -- admin: order workflow (the Orders table) ---------------------------------
def admin_order_fulfil(msg, viewer):
    if viewer.get("role") != "admin":
        return
    o = find_order(msg.get("id"))
    if o is None or o["status"] == "fulfilled":
        return
    if not set_order_status(o, "fulfilled"):   # takes stock (budget already held)
        print(f"[admin] can't fulfil order #{o['id']} "
              f"({o['qty']}x {o['item']} for {team_name(o['team'])}): not enough stock/budget")
        return
    print(f"[admin] fulfilled #{o['id']}: {o['qty']}x {o['item']} -> {team_name(o['team'])}")
    commit()


def admin_order_reject(msg, viewer):
    if viewer.get("role") != "admin":
        return
    o = find_order(msg.get("id"))
    if o is None or o["status"] in ("rejected", "retracted"):
        return
    set_order_status(o, "rejected")   # refunds budget (and returns stock if fulfilled)
    print(f"[admin] marked #{o['id']} not fulfilled")
    commit()


def admin_order_edit(msg, viewer):
    if viewer.get("role") != "admin":
        return
    o = find_order(msg.get("id"))
    if o is None or o["status"] != "pending":
        return  # only a pending order's quantity can be edited
    new_qty = max(1, msg.get("qty", o["qty"]))
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
    commit()


# -- admin: announcement board (its own Markdown state, no shared commit) ------
def admin_announce_set(msg, viewer):
    if viewer.get("role") != "admin":
        return
    global announcement
    announcement = msg.get("text", "")
    save_announcement(announcement)
    announce_display.update(announcement)       # everyone sees the rendered board
    announce_editor.update(text=announcement)   # persist for admins who connect later
    print(f"[admin] announcement updated ({len(announcement)} chars)")
    # The announcement is unrelated to the CSV catalogue/teams/orders, so it
    # broadcasts on its own above and skips the shared commit().


# -- a team's own actions (the team catalogue/cart panel) ---------------------
# ``viewer["role"]`` is the team's stable id (orders reference it), server-trusted.
def team_ping(msg, viewer):
    # Sent on mount so the team's budget/orders appear immediately.
    if viewer.get("role") in teams:
        push_all()


def team_file(msg, viewer):
    team = viewer.get("role")
    if team not in teams:
        return
    name = teams[team]["name"]
    # File a cart of [{item, qty}, …] as one pending order per line. Budget is
    # reserved now; prices are snapshotted. Atomic: if the team can't afford the
    # whole cart, nothing is filed (the UI also pre-checks this).
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
    # Atomic: file nothing unless the team can afford the whole cart *and* no item
    # is requested beyond its current stock (the UI pre-checks both).
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
    commit()


def team_retract(msg, viewer):
    team = viewer.get("role")
    if team not in teams:
        return
    name = teams[team]["name"]
    o = find_order(msg.get("id"))
    # A team can only retract its *own* still-pending order; this refunds the
    # reserved budget.
    if o is None or o["team"] != team or o["status"] != "pending":
        return
    set_order_status(o, "retracted")
    print(f"[{name}] retracted order #{o['id']} (budget back: ${teams[team]['budget']:,})")
    commit()


# ── Panels & layout ───────────────────────────────────────────────────────────
# Every panel's frontend (JSX + CSS) lives in panels/, one file per panel, loaded
# below via canvas.react(path=…, css_path=…).

# Layout, by role:
#   admin      board + Stock + Leaderboard (top), Teams + Orders (bottom)
#   a team     board + their ordering panel + Leaderboard (top row)
#   spectator  the Leaderboard only (the board is admin+team only, below)
# Panels keep one position for all viewers, so the board and the team/stock
# panels share an anchor (right_of=board) — only one reaches any given viewer.

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
# the admin Orders table). Define the chip once (StockChip.jsx) and keep the
# shared colours in one stylesheet (shared.css), instead of re-declaring the JSX
# into every panel — panels just render <StockChip n={…}/> and use the .st-* /
# .chip colours. Registered before the panels (replayed to each browser on
# connect, ahead of the panels that use them).
canvas.define("StockChip", path=_panel("StockChip.jsx"))

canvas.style(_read("shared.css"))


# Top row, left -> right:  board | stock·team | leaderboard | announcements
# The inventory board is for admins and teams, not spectators, so it carries
# explicit roles (admin + each team) and grows with new teams, like team_panel.
# "admin" is always present, so the list is never empty.
board = canvas.react(path=_panel("board.jsx"), name="board", css_path=_panel("board.css"),
                     x=40, y=40, w=440, h=560,
                     roles=["admin", *teams.keys()],
                     props={"rows": inv_rows()})

# Props are seeded from the loaded JSON so the admin sees the full catalogue,
# every team (budget + points) and the order log on the very first connect.
admin_stock = canvas.react(path=_panel("stock.jsx"), name="stock", css_path=_panel("admin.css"), event_key="action",
                           right_of=board, gap=GAP, w=380, h=560,
                           roles=["admin"],
                           props={"rows": inv_rows()})

# Team roles are dynamic: seed with the teams already in the JSON, plus the
# sentinel so the list is never empty. New teams are appended in `team_add`.
# The team panel's per-team data (its name, budget, and orders) is delivered with
# update(roles=...) so no team ever receives another team's figures (see
# push_all); the seed props just cover the first render before that arrives.
# Same anchor as admin_stock — admins see stock there, teams see this.
team_panel = canvas.react(path=_panel("team.jsx"), name="team", css_path=_panel("team.css"), event_key="action",
                          right_of=board, gap=GAP, w=380, h=560,
                          roles=[TEAM_SENTINEL, *teams.keys()],
                          props={"rows": inv_rows(), "team": "", "budget": 0, "orders": []})

# The leaderboard is for everyone (roles=[] = all roles, incl. spectators).
leaderboard = canvas.react(path=_panel("leaderboard.jsx"), name="leaderboard", css_path=_panel("leaderboard.css"),
                           right_of=admin_stock, gap=GAP, w=360, h=560,
                           props={"rows": leaderboard_rows()}, frame=False, grabbable=False)

# Announcements. The rendered board is a built-in Markdown panel visible to
# everyone (no roles); the admin-only editor below it publishes new Markdown
# (broadcast + saved to hackathon_announcement.md). Rightmost column.
announce_display = canvas.markdown(announcement, name="announce",
                                   label="📣 Announcements",
                                   right_of=leaderboard, gap=GAP, w=560, h=560, frame=False, grabbable=False)

# Bottom row (admin only): Teams under the board, Orders under the stock panel.
admin_teams = canvas.react(path=_panel("teams.jsx"), name="teams", css_path=_panel("admin.css"), event_key="action",
                           below=board, gap=GAP, w=440, h=700,
                           roles=["admin"],
                           props={"teams": team_rows(True)})

admin_orders = canvas.react(path=_panel("orders.jsx"), name="orders", css_path=_panel("orders.css"), event_key="action",
                            below=admin_stock, gap=GAP, w=760, h=560,
                            roles=["admin"],
                            props={"log": admin_order_rows()})

announce_editor = canvas.react(path=_panel("announce.jsx"), name="announce_edit", css_path=_panel("announce.css"), event_key="action",
                               below=announce_display, gap=GAP, w=560,
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


# ── Wire each action to its handler (defined up top) ──────────────────────────
# @panel.on("action") routes on msg["action"] (the panels carry event_key="action"),
# so each browser action lands on its own named handler — no central if/elif.
# `fields=` coerces numeric inputs off the wire (and drops a malformed message).
admin_stock.on("reload")(admin_reload)
admin_stock.on("item_set", fields={"stock": int, "price": int})(admin_item_set)
admin_stock.on("item_remove")(admin_item_remove)

admin_teams.on("team_add", fields={"budget": int})(admin_team_add)
admin_teams.on("team_remove")(admin_team_remove)
admin_teams.on("award", fields={"points": int})(admin_award)

admin_orders.on("order_fulfil")(admin_order_fulfil)
admin_orders.on("order_reject")(admin_order_reject)
admin_orders.on("order_edit", fields={"qty": int})(admin_order_edit)

announce_editor.on("announce_set")(admin_announce_set)

team_panel.on("ping")(team_ping)
team_panel.on("file")(team_file)
team_panel.on("retract")(team_retract)


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
    login_message=(
        "Spectators: enter  'view' for a read-only big-screen view.\n"
        "Teams: enter your password ."
    ),
    tunnel=True,
    ui_inspector=False,
    hot_reload=True
)