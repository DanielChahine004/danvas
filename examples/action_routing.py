"""Action routing with field validation (@panel.on(action, fields=...)).

A React panel sends {action: …, …} messages; Python routes each action to its own
named handler instead of one big if/elif, and `fields=` coerces values off the
wire (strings) into real types — dropping a malformed message with a log line
rather than crashing the handler. Run it:

    python examples/action_routing.py

Then: set a price/stock and watch the catalogue update; the quantity stepper
sends ints. Type a non-number into a field and submit — the console logs a
"dropped …" line and the catalogue is unchanged (the handler never ran).
"""

import danvas

canvas = danvas.Canvas()

catalogue = {"Widget": {"price": 10, "stock": 5}}


def render():
    board.update(items=[{"item": k, **v} for k, v in catalogue.items()])


# event_key="action" makes @panel.on route on the payload's "action" field,
# matching the JSX canvas.send({action: '…'}).
board = canvas.react("""
function Component({ canvas, props }) {
  const items = props.items || [];
  const [item, setItem]   = React.useState("Widget");
  const [price, setPrice] = React.useState("10");
  const [stock, setStock] = React.useState("5");
  return (
    <div className="wrap">
      <div className="hd">Catalogue</div>
      {items.map(r => (
        <div key={r.item} className="row">
          <span className="nm">{r.item}</span>
          <span className="pr">${r.price}</span>
          <span className="st">{r.stock} in stock</span>
        </div>
      ))}
      <div className="form">
        <input value={item}  onChange={e=>setItem(e.target.value)}  placeholder="item" />
        <input value={price} onChange={e=>setPrice(e.target.value)} placeholder="price" />
        <input value={stock} onChange={e=>setStock(e.target.value)} placeholder="stock" />
        <button onClick={()=>canvas.send({action:"item_set", item, price, stock})}>Set</button>
      </div>
      <div className="hint">Try a non-number for price → the message is dropped (check the console).</div>
    </div>
  );
}
""", name="board", label="Catalogue", x=60, y=60, w=360, h=360,
   event_key="action",
   props={"items": [{"item": k, **v} for k, v in catalogue.items()]})

canvas.style("""
.wrap{padding:14px;font-family:system-ui,sans-serif;color:#e6edf3}
.hd{font-weight:700;margin-bottom:10px}
.row{display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid #30363d}
.nm{flex:1;font-weight:600}.pr{color:#60a5fa;font-weight:700}.st{color:#94a3b8;font-size:12px}
.form{display:flex;gap:6px;margin-top:12px}
.form input{flex:1;min-width:0;padding:6px 8px;border-radius:6px;border:1px solid #30363d;
            background:#1b2230;color:inherit}
.form button{padding:6px 12px;border:0;border-radius:6px;background:#2563eb;color:#fff;font-weight:600}
.hint{margin-top:10px;font-size:12px;color:#64748b}
""")


# One named handler per action — no if/elif. fields= coerces price/stock to int
# before the handler runs; a non-numeric value drops the message (logged) instead
# of crashing here on int("abc").
@board.on("item_set", fields={"price": int, "stock": int})
def _(msg):
    name = (msg.get("item") or "").strip()
    if not name:
        return
    catalogue[name] = {"price": max(0, msg["price"]), "stock": max(0, msg["stock"])}
    print(f"[set] {name}: ${catalogue[name]['price']} / {catalogue[name]['stock']} in stock")
    render()


canvas.serve()
