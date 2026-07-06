
function ViewReadout({ canvas }) {
  // The current viewport (canvas centre + zoom) — the x/y/zoom serve(view=...)
  // and set_view() take. canvas.viewport calls back live as the camera moves.
  const [v, setV] = React.useState(null);
  React.useEffect(() => (canvas.viewport ? canvas.viewport(setV) : undefined), []);
  if (!v) return null;
  return (
    <div
      style={{
        marginTop: 6, fontSize: 11, fontFamily: "ui-monospace, monospace",
        color: "var(--pc-muted)", userSelect: "text", WebkitUserSelect: "text",
        cursor: "text",
      }}
      title="current viewport — pass these to serve(view=...) or canvas.set_view() to fix this view"
    >
      view: x={v.x} y={v.y} zoom={v.zoom.toFixed(2)}
    </div>
  );
}

// Drill-down: an object's type/repr header plus a field/type/value table.
function DetailView({ selected, detail, onBack, onRefresh, controlStyle }) {
  const [query, setQuery] = React.useState("");
  const [typeFilter, setTypeFilter] = React.useState("all");
  // Reset filters when drilling into a different object.
  React.useEffect(() => { setQuery(""); setTypeFilter("all"); }, [selected]);

  const allFields = detail && Array.isArray(detail.fields) ? detail.fields : [];
  const types = ["all", ...Array.from(new Set(allFields.map((f) => f.type))).sort()];
  const selectable = { userSelect: "text", WebkitUserSelect: "text", cursor: "text" };
  const q = query.toLowerCase();
  const fields = allFields.filter(
    (f) =>
      (typeFilter === "all" || f.type === typeFilter) &&
      (!q || String(f.field ?? "").toLowerCase().includes(q))
  );
  const filtered = q !== "" || typeFilter !== "all";
  return (
    <>
      <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
        <button style={{ ...controlStyle, cursor: "pointer" }} onClick={onBack}>← back</button>
        <span style={{ flex: 1, minWidth: 0, fontSize: 13, fontWeight: 600,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {selected}
          {detail && <span style={{ fontWeight: 400, color: "var(--pc-faint)" }}> : {detail.type}</span>}
        </span>
        <button style={{ ...controlStyle, cursor: "pointer" }} onClick={onRefresh}>Refresh</button>
      </div>
      {detail && !detail.missing && allFields.length > 0 && (
        <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
          <input placeholder="search field…" value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ ...controlStyle, flex: 1, minWidth: 0 }} />
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} style={controlStyle}>
            {types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {!detail ? (
          <div style={{ fontSize: 12, color: "var(--pc-faint2)", padding: 6 }}>loading…</div>
        ) : detail.missing ? (
          <div style={{ fontSize: 12, color: "var(--pc-faint2)", padding: 6 }}>no longer available</div>
        ) : (
          <>
            <div style={{ fontSize: 12, fontFamily: "ui-monospace, monospace",
              color: "var(--pc-detail-text)", background: "var(--pc-detail-bg)",
              border: "1px solid var(--pc-detail-border)", borderRadius: 4,
              padding: "4px 6px", marginBottom: 6, wordBreak: "break-all", ...selectable }}>
              {detail.repr}
            </div>
            <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {["field", "type", "value"].map((c) => (
                    <th key={c} style={{ textAlign: "left", padding: "2px 6px",
                      borderBottom: "1px solid var(--pc-border-mid)", color: "var(--pc-muted)",
                      position: "sticky", top: 0, background: "var(--pc-bg)" }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {fields.length === 0 ? (
                  <tr>
                    <td colSpan={3} style={{ padding: 6, color: "var(--pc-faint2)", fontStyle: "italic" }}>
                      {filtered ? "no matching fields" : "no fields — see repr above"}
                    </td>
                  </tr>
                ) : (
                  fields.map((f, i) => (
                    <tr key={i}>
                      <td style={{ padding: "2px 6px", borderBottom: "1px solid var(--pc-border-soft)", ...selectable }}>{f.field}</td>
                      <td style={{ padding: "2px 6px", borderBottom: "1px solid var(--pc-border-soft)", color: "var(--pc-faint)" }}>{f.type}</td>
                      <td style={{ padding: "2px 6px", borderBottom: "1px solid var(--pc-border-soft)",
                        fontFamily: "ui-monospace, monospace", ...selectable }}>{f.value}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </>
        )}
      </div>
    </>
  );
}

function Component({ canvas, props }) {
  const [query, setQuery] = React.useState("");
  const [typeFilter, setTypeFilter] = React.useState("all");
  // Which row is drilled into (its key), or null for the table view.
  const [selected, setSelected] = React.useState(null);

  // Tolerant reads: a JSON string (Python) or plain JSON (other SDKs).
  const asJson = (v, fallback) => {
    if (v == null || v === "") return fallback;
    if (typeof v !== "string") return v;
    try { return JSON.parse(v) ?? fallback; } catch { return fallback; }
  };
  const rows = asJson(props.rows, []);
  let cols = ["name", "label", "type", "value", "x", "y", "w", "h"];
  const parsedCols = asJson(props.cols, null);
  if (Array.isArray(parsedCols) && parsedCols.length) cols = parsedCols;

  const controlStyle = {
    fontSize: 12, padding: "3px 6px", border: "1px solid var(--pc-border-mid)",
    borderRadius: 6, background: "var(--pc-input-bg)", color: "var(--pc-text)",
  };

  // --- detail (drill-down) view -------------------------------------------
  if (selected != null) {
    const detail = asJson(props.detail, null);
    // Only show detail once it's arrived for the row we clicked (avoid stale).
    const ready = detail && detail.key === selected;
    return (
      <DetailView
        selected={selected}
        detail={ready ? detail : null}
        onBack={() => { setSelected(null); canvas.send({ action: "detail", key: null }); }}
        onRefresh={() => canvas.send({ action: "detail", key: selected })}
        controlStyle={controlStyle} />
    );
  }

  const types = ["all", ...Array.from(new Set(rows.map((r) => r.type))).sort()];
  const q = query.toLowerCase();
  const shown = rows.filter(
    (r) =>
      (typeFilter === "all" || r.type === typeFilter) &&
      (!q || String(r.name ?? "").toLowerCase().includes(q))
  );

  const openDetail = (r) => {
    const key = r.key ?? r.name;
    if (!key) return;
    setSelected(key);
    canvas.send({ action: "detail", key });
  };

  const source = props.source || "components";
  const switchSource = (next) => {
    if (next === source) return;
    setTypeFilter("all"); // the type set differs between the two views
    setQuery("");
    canvas.send({ action: "source", source: next });
  };

  return (
    <>
      <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
        <select value={source} onChange={(e) => switchSource(e.target.value)} style={controlStyle} title="what to inspect">
          <option value="components">panels</option>
          <option value="canvas">canvas</option>
          <option value="globals">globals</option>
          <option value="system">system</option>
        </select>
        <input placeholder="search name…" value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ ...controlStyle, flex: 1, minWidth: 0 }} />
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} style={controlStyle}>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <button style={{ ...controlStyle, cursor: "pointer" }} onClick={() => canvas.send({ action: "refresh" })}>Refresh</button>
        <button style={{ ...controlStyle, cursor: "pointer" }}
          title="open the live dispatch-trace panel"
          onClick={() => canvas.send({ action: "trace" })}>Trace</button>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c} style={{ textAlign: "left", padding: "2px 6px",
                  borderBottom: "1px solid var(--pc-border-mid)", color: "var(--pc-muted)",
                  position: "sticky", top: 0, background: "var(--pc-bg)" }}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} onClick={() => openDetail(r)} style={{ cursor: "pointer" }} title="click to inspect fields">
                {cols.map((c) => (
                  <td key={c} style={{ padding: "2px 6px", borderBottom: "1px solid var(--pc-border-soft)",
                    fontFamily: c === "value" ? "ui-monospace, monospace" : "inherit" }}>
                    {String(r[c] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ViewReadout canvas={canvas} />
    </>
  );
}
