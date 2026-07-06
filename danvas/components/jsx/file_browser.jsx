
function Component({ canvas, value }) {
  React.useEffect(() => { canvas.send({ event: "ready" }); }, []);
  const state = value || { cwd: "/", atRoot: true, selected: null, entries: [] };
  function fmtSize(n) {
    if (!n) return "";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : n) + " " + u[i];
  }
  return (
    <div className="pc-fb">
      <style>{`__CSS__`}</style>
      <div className="pc-fb-bar">
        <button className="pc-fb-up" disabled={state.atRoot} title="up one level"
                onClick={() => canvas.send({ event: "up" })}>..</button>
        <span className="pc-fb-cwd">{state.cwd}</span>
      </div>
      <div className="pc-fb-list">
        {state.entries.length === 0
          ? <div className="pc-fb-empty">(empty)</div>
          : state.entries.map((ent, i) => (
              <div key={i} role="button"
                   className={"pc-fb-row" + (!ent.dir && ent.name === state.selected ? " sel" : "")}
                   onClick={() => canvas.send({ event: "open", name: ent.name })}>
                <span className="pc-fb-ico">{ent.dir ? "📁" : "📄"}</span>
                <span className="pc-fb-nm">{ent.name}</span>
                <span className="pc-fb-sz">{ent.dir ? "" : fmtSize(ent.size)}</span>
              </div>
            ))}
      </div>
    </div>
  );
}
