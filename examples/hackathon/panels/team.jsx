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
