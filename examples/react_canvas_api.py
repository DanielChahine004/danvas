"""React panel canvas API: viewport / setView / chat from user JSX.

A single React panel that's *canvas-aware* and *collaborative* using only the
`canvas` bridge handle — no Python logic beyond serving:

  * canvas.viewport(cb)  — live x/y/zoom of the canvas centre (updates as you pan/zoom)
  * canvas.setView(v)    — "jump to" buttons that pan/zoom the canvas
  * canvas.chat          — posts a line to the shared room on each jump, and shows
                           the last few lines (open a second tab to see it relayed)

Run:  python examples/react_canvas_api.py
Then pan/zoom the canvas and watch the readout track it; click the buttons to fly
the camera; open a second browser tab to see the jump announcements relayed.
"""

import pycanvas

HUD = r"""
function Component({ canvas }) {
  const [view, setView] = React.useState(null);
  const [lines, setLines] = React.useState([]);

  // Live viewport: cb fires now and on every camera move.
  React.useEffect(() => canvas.viewport(setView), []);

  // Shared chat room: backfill history, then append each new line (deduped).
  React.useEffect(() => {
    setLines(canvas.chat.history().slice(-5));
    return canvas.chat.subscribe((e) =>
      setLines((m) => (m.some((x) => x.msgId === e.msgId) ? m : [...m, e].slice(-5)))
    );
  }, []);

  // Fly the camera and announce it in the shared room.
  const jump = (v, label) => {
    canvas.setView(v);
    canvas.chat.send("flew to " + label);
  };

  const z = view ? view.zoom : 1;
  const btn = {
    padding: "5px 9px", fontSize: 12, fontWeight: 600, cursor: "pointer",
    borderRadius: 6, border: "1px solid var(--pc-border, #30363d)",
    background: "var(--pc-surface, #1b2230)", color: "var(--pc-text, #e6edf3)",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 10,
      height: "100%", boxSizing: "border-box", color: "var(--pc-text, #e6edf3)",
      font: "13px system-ui, sans-serif" }}>
      <div style={{ fontVariantNumeric: "tabular-nums" }}>
        {view
          ? <>centre <b>x</b>={view.x} <b>y</b>={view.y} · <b>zoom</b> {z.toFixed(2)}</>
          : "reading viewport…"}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button style={btn} onClick={() => jump({ x: 0, y: 0, zoom: 1 }, "origin")}>origin</button>
        <button style={btn} onClick={() => jump({ zoom: z * 1.4 }, "zoom in")}>zoom in</button>
        <button style={btn} onClick={() => jump({ zoom: z / 1.4 }, "zoom out")}>zoom out</button>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: "auto", borderTop: "1px solid var(--pc-border, #30363d)",
        paddingTop: 6, fontSize: 12, color: "var(--pc-muted, #9aa4b2)" }}>
        {lines.length === 0
          ? <i>no announcements yet — click a button (or open a second tab)</i>
          : lines.map((m) => (
              <div key={m.msgId}><b style={{ color: m.color }}>{m.name}</b>: {m.text}</div>
            ))}
      </div>
    </div>
  );
}
"""

canvas = pycanvas.Canvas()

# A few panels scattered across the canvas to give the camera somewhere to fly.
canvas.label("alpha", value="panel @ (0, 0)", x=0, y=0)
canvas.label("beta", value="panel @ (900, 600)", x=900, y=600)

canvas.react(HUD, name="hud", label="canvas API (viewport / setView / chat)",
             x=300, y=120, w=320, h=240)

print("Pan/zoom the canvas and watch the HUD track it; click the buttons to fly "
      "the camera; open a second tab to see the jump announcements relayed.")

canvas.serve(port=8000)
