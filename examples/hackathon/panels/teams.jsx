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
