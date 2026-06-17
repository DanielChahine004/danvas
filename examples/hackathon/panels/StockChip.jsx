function StockChip({ n }) {
  if (n === 0) return <span className="chip out">Out of stock</span>;
  if (n <= 2)  return <span className="chip low">{n} left</span>;
  return             <span className="chip in">{n} in stock</span>;
}
