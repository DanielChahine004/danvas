"""danvas, explained by danvas.

A canvas that diagrams *itself*: how the browser and Python connect, which
thread does what, and how state stays in sync across viewers. Every panel is
live — it isn't a picture of the system, it's the system reporting on itself:

* **Pipeline** — the path a message travels (Browser ⇄ WebSocket ⇄ Bridge ⇄
  dispatch thread ⇄ your handlers). A spark lights up on real traffic, fed by
  ``canvas.on_frame`` (the supported wire-tap).
* **Wire** — the actual JSON frames crossing the socket right now, in/out,
  also from ``canvas.on_frame``. This is literally how the front and back ends
  talk: ``register`` / ``update`` going out, ``input`` / ``layout`` coming in.
* **Threads** — the three threads, and a demo proving they're separate: a
  background thread ticks the *event-loop heartbeat* while a button runs a
  3-second **blocking** handler on the FIFO dispatch thread. The heartbeat
  keeps ticking → the loop was never blocked, exactly as designed.
* **Sync** — a shared counter. One click broadcasts to *every* viewer, and the
  current value is replayed to anyone who connects later. Open a second tab.

Run it:  ``python examples/architecture.py``   (then open a second tab too).
"""

import threading
import time

import danvas

canvas = danvas.Canvas()


# ── Shared styling (one global sheet, the canvas.style() convention) ──────────
# Page-global, so selectors are prefixed .arc-* exactly like a panel's own css=.
canvas.style("""
.arc{box-sizing:border-box;padding:16px;
     font-family:system-ui,-apple-system,sans-serif;color:var(--pc-text,#e6edf3)}
.arc h2{margin:0 0 4px;font-size:15px;font-weight:700;display:flex;gap:8px;align-items:center}
.arc .sub{color:#8b95a5;font-size:11px;margin-bottom:14px;line-height:1.45}
.arc code{font:11px ui-monospace,SFMono-Regular,monospace;color:#fbbf24;
          background:rgba(120,53,15,.22);padding:1px 5px;border-radius:5px}
.arc .legend{font-size:11px;color:#8b95a5;margin-top:10px;line-height:1.5}
.arc .dot{display:inline-block;width:8px;height:8px;border-radius:50%;
          margin-right:5px;vertical-align:middle}
""")


# ── 1. Pipeline diagram ───────────────────────────────────────────────────────
# Shows the five stages a message passes through and which thread owns each.
# It re-renders on every real frame (fed a {seq, dir, type} via push()), and a
# CSS spark travels in the frame's direction: "in" (browser→Python) left→right,
# "out" (Python→browser) right→left.
_PIPELINE_CSS = """
.pipe{display:flex;align-items:stretch;gap:0;margin:18px 0 6px}
.stage{flex:1;min-width:0;text-align:center;padding:12px 8px;border-radius:10px;
       border:1.5px solid var(--pc-border,#30363d);background:var(--pc-surface,#1b2230);
       position:relative;transition:border-color .2s,box-shadow .2s}
.stage .icn{font-size:22px}
.stage .nm{font-size:12px;font-weight:700;margin-top:4px}
.stage .th{font-size:9.5px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;
           margin-top:5px;padding:2px 6px;border-radius:999px;display:inline-block}
.stage .th.loop{color:#60a5fa;background:rgba(37,99,235,.18)}
.stage .th.disp{color:#f0abfc;background:rgba(168,85,247,.18)}
.stage .th.user{color:#4ade80;background:rgba(22,163,74,.18)}
.stage .th.net{color:#94a3b8;background:rgba(148,163,184,.15)}
.stage .desc{font-size:10px;color:#8b95a5;margin-top:7px;line-height:1.4}
.arrow{flex:0 0 26px;align-self:center;text-align:center;color:#475569;font-size:16px;position:relative}
.track{position:relative;height:4px;margin:10px 4px 0;border-radius:2px;
       background:var(--pc-border,#30363d);overflow:hidden}
.spark{position:absolute;top:-3px;width:18px;height:10px;border-radius:6px;opacity:0}
.spark.in{animation:arc-right .65s ease-out}
.spark.out{animation:arc-left .65s ease-out}
@keyframes arc-right{0%{left:-18px;opacity:0}10%{opacity:1}90%{opacity:1}100%{left:100%;opacity:0}}
@keyframes arc-left {0%{left:100%;opacity:0}10%{opacity:1}90%{opacity:1}100%{left:-18px;opacity:0}}
.flowlbl{display:flex;justify-content:space-between;font-size:10px;color:#8b95a5;margin-top:4px}
"""

_PIPELINE_SRC = """
function Component({ value }) {
  const v = value || {};
  const dir = v.dir === "in" ? "in" : "out";
  const color = dir === "in" ? "#60a5fa" : "#4ade80";
  const stages = [
    {icn:"🌐", nm:"Browser", th:"net",  cls:"net",
     desc:"React panel calls canvas.send(...)"},
    {icn:"🔌", nm:"WebSocket", th:"1 socket", cls:"net",
     desc:"all panels multiplexed by id"},
    {icn:"⚙️", nm:"Bridge", th:"event loop", cls:"loop",
     desc:"asyncio I/O — never runs your code"},
    {icn:"🧵", nm:"Kernel", th:"dispatch", cls:"disp",
     desc:"one FIFO thread runs handlers"},
    {icn:"🐍", nm:"@on handler", th:"your code", cls:"user",
     desc:"mutate state, then broadcast()"},
  ];
  return (
    <div className="arc">
      <h2>⚙️ The pipeline</h2>
      <div className="sub">
        A single message, end to end. <code>canvas.send</code> rides the socket as
        an <code>input</code> frame; the Bridge hands it to the dispatch thread,
        your handler runs and <code>broadcast</code>s an <code>update</code> back out.
      </div>
      <div className="pipe">
        {stages.map((s, i) => (
          <React.Fragment key={s.nm}>
            <div className="stage">
              <div className="icn">{s.icn}</div>
              <div className="nm">{s.nm}</div>
              <div className={"th " + s.cls}>{s.th}</div>
              <div className="desc">{s.desc}</div>
            </div>
            {i < stages.length - 1 && <div className="arrow">⇄</div>}
          </React.Fragment>
        ))}
      </div>
      <div className="track">
        <div key={v.seq} className={"spark " + dir} style={{background: color}} />
      </div>
      <div className="flowlbl">
        <span>browser → Python (<code>input</code>, <code>layout</code>)</span>
        <span>Python → browser (<code>register</code>, <code>update</code>)</span>
      </div>
      <div className="legend">
        Last frame: <span className="dot" style={{background: color}} />
        <b style={{color}}>{dir === "in" ? "inbound" : "outbound"}</b>
        &nbsp;<code>{v.type || "—"}</code>
      </div>
    </div>
  );
}
"""


# ── 2. Live wire tap ──────────────────────────────────────────────────────────
# Accumulates the real frames seen by canvas.on_frame. Uses canvas.onFrame (not
# the `value` prop) so a burst of frames appends without fighting React renders.
_WIRE_CSS = """
.wire .rows{font:11.5px ui-monospace,SFMono-Regular,monospace;line-height:1.7}
.wire .f{display:flex;gap:8px;align-items:center;padding:3px 6px;border-radius:6px}
.wire .f.in{background:rgba(37,99,235,.10)}
.wire .f.out{background:rgba(22,163,74,.08)}
.wire .ar{font-weight:800;width:14px;flex:0 0 14px;text-align:center}
.wire .f.in .ar{color:#60a5fa}
.wire .f.out .ar{color:#4ade80}
.wire .ty{font-weight:700;min-width:74px}
.wire .id{color:#8b95a5;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wire .empty{color:#64748b;font-size:12px;padding:10px 0}
"""

_WIRE_SRC = """
function Component({ canvas }) {
  const [frames, setFrames] = React.useState([]);
  React.useEffect(() => {
    return canvas.onFrame((f) => {
      setFrames((prev) => [f, ...prev].slice(0, 14));
    });
  }, []);
  return (
    <div className="arc wire">
      <h2>📡 The wire (live)</h2>
      <div className="sub">
        Real JSON frames crossing the socket, via <code>canvas.on_frame</code>.
        Click things on the canvas and watch them appear. This is the whole
        protocol — there is no hidden channel.
      </div>
      <div className="rows">
        {frames.length === 0
          ? <div className="empty">Waiting for traffic… try the buttons below.</div>
          : frames.map((f, i) => (
              <div key={i} className={"f " + f.dir}>
                <span className="ar">{f.dir === "in" ? "↑" : "↓"}</span>
                <span className="ty">{f.type}</span>
                <span className="id">{f.id || ""}</span>
              </div>
            ))}
      </div>
      <div className="legend">
        <span className="dot" style={{background:"#60a5fa"}} />↑ browser → Python
        &nbsp;&nbsp;
        <span className="dot" style={{background:"#4ade80"}} />↓ Python → browser
      </div>
    </div>
  );
}
"""


# ── 3. Threads demo ───────────────────────────────────────────────────────────
# Props: beat (loop heartbeat, ticked by a background thread), busy (dispatch
# thread running a blocking handler), label, queued (clicks waiting their turn).
_THREADS_CSS = """
.thr .cols{display:flex;flex-direction:column;gap:10px;margin:14px 0}
.thr .col{border:1.5px solid var(--pc-border,#30363d);border-radius:10px;padding:11px 13px;
          background:var(--pc-surface,#1b2230)}
.thr .ct{font-size:12px;font-weight:700;display:flex;align-items:center;gap:7px}
.thr .badge{font-size:9px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;
            padding:2px 6px;border-radius:999px;margin-left:auto}
.thr .badge.idle{color:#94a3b8;background:rgba(148,163,184,.15)}
.thr .badge.busy{color:#fbbf24;background:rgba(217,119,6,.2)}
.thr .badge.go{color:#4ade80;background:rgba(22,163,74,.18)}
.thr .cd{font-size:10.5px;color:#8b95a5;margin-top:5px;line-height:1.45}
.thr .beat{font:13px ui-monospace,monospace;color:#60a5fa;font-weight:700}
.thr .btn{width:100%;margin-top:6px;padding:9px;border-radius:8px;font-size:13px;font-weight:600;
          cursor:pointer;border:none;background:#7c3aed;color:#fff;transition:background .12s}
.thr .btn:hover{background:#6d28d9}
.thr .q{font-size:11px;color:#fbbf24;margin-top:6px;min-height:15px}
"""

_THREADS_SRC = """
function Component({ canvas, props }) {
  const beat   = props.beat || 0;
  const busy   = !!props.busy;
  const label  = props.label || "idle";
  const queued = props.queued || 0;
  return (
    <div className="arc thr">
      <h2>🧵 Three threads, one job each</h2>
      <div className="sub">
        A slow handler cannot freeze the canvas — your code never runs on the
        socket event loop.
      </div>
      <div className="cols">
        <div className="col">
          <div className="ct">⚙️ asyncio event loop
            <span className="badge go">always free</span></div>
          <div className="cd">All socket reads/writes. Heartbeat from a Python
            background thread, broadcast through the loop:
            &nbsp;<span className="beat">tick #{beat}</span></div>
        </div>
        <div className="col">
          <div className="ct">🧵 Kernel dispatch (FIFO)
            <span className={"badge " + (busy ? "busy" : "idle")}>{busy ? "busy" : "idle"}</span></div>
          <div className="cd">Runs your <code>@on</code> handlers, one at a time,
            in order. Current: <b>{label}</b></div>
          <button className="btn" onClick={() => canvas.send({ event: "block" })}>
            Run a 3s blocking handler
          </button>
          <div className="q">{queued > 0 ? (queued + " click(s) queued behind it (FIFO)") : ""}</div>
        </div>
        <div className="col">
          <div className="ct">🐍 your main thread
            <span className="badge idle">parked</span></div>
          <div className="cd">Built the canvas, then parked in
            <code>serve()</code>. The work happens on the two threads above.</div>
        </div>
      </div>
      <div className="legend">
        Click the purple button, then watch <span className="beat">tick #</span> keep
        rising while the dispatch thread sleeps. The loop was never blocked.
      </div>
    </div>
  );
}
"""


# ── 4. Multi-viewer sync demo ─────────────────────────────────────────────────
_SYNC_CSS = """
.syn .big{font-size:54px;font-weight:800;text-align:center;color:#4ade80;
          font-variant-numeric:tabular-nums;margin:8px 0 2px}
.syn .who{text-align:center;font-size:11px;color:#8b95a5;min-height:15px;margin-bottom:12px}
.syn .btn{width:100%;padding:11px;border-radius:8px;font-size:14px;font-weight:700;
          cursor:pointer;border:none;background:#2563eb;color:#fff;transition:background .12s}
.syn .btn:hover{background:#1d4ed8}
.syn .vw{display:flex;align-items:center;justify-content:center;gap:7px;margin-top:13px;
         font-size:12px;color:#cbd5e1}
.syn .vw b{color:#60a5fa}
"""

_SYNC_SRC = """
function Component({ canvas, props }) {
  const count = props.count || 0;
  const last  = props.last || "";
  const views = props.viewers || 1;
  return (
    <div className="arc syn">
      <h2>👥 Shared state, every viewer</h2>
      <div className="sub">
        One click → <code>input</code> → Python mutates state → <code>broadcast</code>
        to <i>all</i> sockets. New tabs get the current value replayed on connect.
      </div>
      <div className="big">{count}</div>
      <div className="who">{last ? ("last +1 by " + last) : "nobody has clicked yet"}</div>
      <button className="btn" onClick={() => canvas.send({ event: "inc" })}>
        +1 for everyone
      </button>
      <div className="vw">
        <span className="dot" style={{background:"#22c55e"}} />
        <b>{views}</b>&nbsp;viewer{views === 1 ? "" : "s"} connected —
        open a second tab to see the sync.
      </div>
    </div>
  );
}
"""


# ── Build the panels ──────────────────────────────────────────────────────────
# Initial sizes only — placement happens via set_layout below. The h= values are
# just the height shown before the browser measures the content (see auto-height).
pipeline = canvas.react(source=_PIPELINE_SRC, css=_PIPELINE_CSS, name="pipeline",
                        w=1184, h=280)
wire = canvas.react(source=_WIRE_SRC, css=_WIRE_CSS, name="wire",
                    w=376, h=300)
threads = canvas.react(source=_THREADS_SRC, css=_THREADS_CSS, name="threads",
                      props={"beat": 0, "busy": False, "label": "idle", "queued": 0},
                      w=400, h=300)
sync = canvas.react(source=_SYNC_SRC, css=_SYNC_CSS, name="sync",
                   props={"count": 0, "last": "", "viewers": 1},
                   w=360, h=260)

# Fit each panel's height to its rendered content (ReactHost measures and reports
# it back). Width stays fixed; the wire panel grows as frames arrive, settling
# once it fills to its 14-row cap.
for _p in (pipeline, wire, threads, sync):
    _p.h = "auto"

# Lay them out: one full-width row (the pipeline) above three side-by-side
# columns. set_layout sets x/y/w only — leaving h auto, so the measured height
# stands. The columns share one y, which tracks the pipeline's *measured* bottom
# (see the reactive restack below). These initial values place everything before
# the first height report arrives from the browser.
MARGIN, GAP, STACK_GAP = 40, 24, 20
row2_y = MARGIN + pipeline.h + STACK_GAP
col2_x = MARGIN + wire.w + GAP
col3_x = col2_x + threads.w + GAP
pipeline.set_layout(x=MARGIN, y=MARGIN, w=1184)
wire.set_layout(x=MARGIN, y=row2_y, w=376)
threads.set_layout(x=col2_x, y=row2_y, w=400)
sync.set_layout(x=col3_x, y=row2_y, w=360)


# ── Reactive stacking: keep the column row STACK_GAP px below the pipeline ────
# Auto-height is measured in the browser, so Python only learns a panel's real
# height when the frontend reports it back as an inbound `layout` frame (the same
# stream canvas.on_frame taps). Each time the pipeline reports a new height, drop
# the row beneath it to (pipeline top + measured height + gap). This is what makes
# "20px below the bottom of the panel above" hold even as content reflows — a
# one-shot calc couldn't, since the height changes after first paint.
def stack_below_pipeline(direction, msg):
    if direction != "in" or msg.get("type") != "layout":
        return
    if msg.get("id") != pipeline.id or msg.get("h") is None:
        return
    new_y = MARGIN + msg["h"] + STACK_GAP            # pipeline top is MARGIN
    for p in (wire, threads, sync):
        if (p._position or (None, None))[1] != new_y:
            p.set_layout(y=new_y)                    # x/h untouched → auto-height stays

canvas.on_frame(stack_below_pipeline)


# ── Wire-tap: feed the pipeline + wire panels their own real traffic ──────────
# Frames a tap itself causes (these very push()es) are not re-tapped, so this
# can't recurse. Cursor moves and heartbeats are already filtered upstream.
_seq = {"n": 0}

@canvas.on_frame
def show(direction, msg):
    _seq["n"] += 1
    pipeline.push({"seq": _seq["n"], "dir": direction, "type": msg.get("type")})
    wire.push({"dir": direction, "type": msg.get("type"), "id": msg.get("id", "")})


# ── Threads demo: a deliberately blocking handler on the dispatch thread ──────
_block = {"running": False, "queued": 0}

@threads.on("block")
def run_blocking(msg, viewer):
    # This runs on the Kernel's single FIFO dispatch thread. A second click while
    # this sleeps waits its turn (that's what "queued" reports) — meanwhile the
    # event-loop heartbeat below keeps ticking, proving the loop is untouched.
    if _block["running"]:
        _block["queued"] += 1
        threads.update(queued=_block["queued"])
    _block["running"] = True
    threads.update(busy=True, label="sleeping 3s…")
    time.sleep(3)
    if _block["queued"] > 0:
        _block["queued"] -= 1
    _block["running"] = _block["queued"] > 0
    threads.update(busy=_block["running"],
                  label="sleeping 3s…" if _block["running"] else "idle",
                  queued=_block["queued"])


# ── Sync demo: mutate shared state, broadcast to everyone ─────────────────────
_shared = {"count": 0}

@sync.on("inc")
def increment(msg, viewer):
    _shared["count"] += 1
    # update() merges into the panel's props and broadcasts to every socket; the
    # value also persists, so a viewer who connects later gets it replayed.
    sync.update(count=_shared["count"], last=viewer.get("name", "someone"))


# ── Event-loop heartbeat: a background Python thread ticking through the loop ──
# Proves the asyncio loop keeps flowing while the dispatch thread is blocked.
# Each tick is one real outbound `update` frame (visible in the wire panel).
def heartbeat():
    n = 0
    while True:
        n += 1
        threads.update(beat=n)
        sync.update(viewers=len(canvas.viewers) or 1)
        time.sleep(2)

threading.Thread(target=heartbeat, daemon=True).start()


if __name__ == "__main__":
    # Lint the JSX before serving — catches an unbalanced brace as a clean error
    # instead of a cryptic browser failure.
    for p in (pipeline, wire, threads, sync):
        problems = p.validate()
        assert not problems, f"{p.id}: {problems}"
    canvas.serve()
