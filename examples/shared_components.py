"""Shared React components + global CSS (canvas.define / canvas.style).

Two panels render the *same* StatusPill and Stat components without either panel
re-declaring them — they're defined once with canvas.define() and styled once
with canvas.style(). Run it and confirm both panels show identical, styled pills:

    python examples/shared_components.py

Then edit the StatusPill source or the stylesheet below and restart — both panels
pick up the change. This is the pattern that kills the per-panel duplication of a
shared table/badge/button in a bigger app.
"""

import pycanvas

canvas = pycanvas.Canvas()

# --- shared components: defined once, usable by name in every react() panel ---
canvas.define("StatusPill", """
  function StatusPill({ kind, children }) {
    return <span className={"pill " + kind}>{children}</span>
  }
""")

canvas.define("Stat", """
  function Stat({ label, value }) {
    return (
      <div className="stat">
        <div className="stat-label">{label}</div>
        <div className="stat-value">{value}</div>
      </div>
    )
  }
""")

# --- one global stylesheet shared by both panels (injected once into <head>) ---
canvas.style("""
  .pill { display:inline-block; padding:2px 10px; border-radius:999px;
          font-size:12px; font-weight:600; }
  .pill.ok  { background:rgba(20,83,45,.35);  color:#4ade80; }
  .pill.low { background:rgba(120,53,15,.35); color:#fbbf24; }
  .pill.out { background:rgba(127,29,29,.35); color:#f87171; }
  .stat { padding:10px 14px; }
  .stat-label { font-size:12px; color:#94a3b8; }
  .stat-value { font-size:24px; font-weight:700; }
  .panel-body { display:flex; flex-direction:column; gap:12px; padding:14px;
                font-family:system-ui, sans-serif; }
  .row { display:flex; gap:8px; align-items:center; }
""")

# --- two panels that both use the shared components, neither re-declaring them --
canvas.react("""
  function Component() {
    return (
      <div className="panel-body">
        <Stat label="Widgets in stock" value="42" />
        <div className="row">
          <StatusPill kind="ok">In stock</StatusPill>
          <StatusPill kind="low">Low</StatusPill>
          <StatusPill kind="out">Out</StatusPill>
        </div>
      </div>
    )
  }
""", name="inventory", label="Inventory", x=60, y=60, w=320, h=200)

canvas.react("""
  function Component() {
    return (
      <div className="panel-body">
        <Stat label="Open orders" value="7" />
        <div className="row">
          <StatusPill kind="ok">Fulfilled</StatusPill>
          <StatusPill kind="low">Pending</StatusPill>
        </div>
      </div>
    )
  }
""", name="orders", label="Orders", right_of="inventory", gap=24, w=320, h=200)

canvas.serve()
