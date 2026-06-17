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
