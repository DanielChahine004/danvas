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
