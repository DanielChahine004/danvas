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
