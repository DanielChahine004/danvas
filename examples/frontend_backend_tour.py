"""An interactive tour of how the PyCanvas frontend talks to the backend.

Everything between Python and the browser is JSON frames over one WebSocket
(``ws://localhost:8000/ws``), multiplexed by component id:

    Python -> browser:  register   "a new panel exists; here's how to draw it"
                        update     "this panel's state/geometry changed"
                        remove     "drop this panel"
    browser -> Python:  input      "the user operated a control"
                        layout     "the user dragged/resized a panel"

This script teaches that by *showing you the actual frames*. It registers a
``canvas.on_frame`` observer — the supported hook for watching the wire — and
mirrors each frame into a WIRE TAP panel on the canvas, live, while you
interact with the other panels. (For plain console logging instead, just run
your script with ``canvas.serve(debug=True)``.)

Run:  python examples/frontend_backend_tour.py
"""

import json
import time

import pycanvas

canvas = pycanvas.Canvas()

# ---------------------------------------------------------------------------
# The tour guide — read this panel first in the browser.
# ---------------------------------------------------------------------------
GUIDE = """
# How the frontend talks to the backend

Everything you see is **JSON frames over one WebSocket**, multiplexed by
component id. The **WIRE TAP** panel (far right) shows every real frame as it
crosses the wire: `▼` = Python → browser, `▲` = browser → Python.

### Station 1 — Python → browser (`update`)
The clock label ticks from a background thread in Python. Hands off the mouse
and watch `update` frames stream down anyway: *Python owns the state, the
browser just renders it.*

### Station 2 — the full round trip (`input` → `update`)
Drag the slider. Each movement is an `▲ input` frame; the `@on_change` handler
runs **in Python**, calls `mirror.update(...)`, and that goes back down as a
`▼ update` frame. The browser never talks to itself — every reaction routes
through your Python code.

### Station 3 — geometry flows back too (`layout`)
Drag or resize the **DRAG ME** panel. The browser reports an `▲ layout` frame,
Python's `comp.x/y/w/h` sync to match, and `@on_layout` fires.

### Station 4 — your own protocol (`Custom`)
A Custom panel's HTML calls `canvas.send({...})` → arrives as an `▲ input`
frame → `@panel.on("hello")` routes it; Python replies with `panel.push(...)`
→ a `▼ update` frame the iframe receives via `canvas.onPush`.

### Try this: `update` vs `push`
**Reload the browser tab.** The slider, clock, and mirror come back exactly as
they were — on connect the server *replays* each component's state (those
replays are per-connection sends, not broadcasts, so the tap doesn't show
them). But the wire-tap log comes back **empty**: it's fed with `push()`,
which streams transient data and is never replayed. State lives in `update`;
streams live in `push`.
"""

guide = canvas.markdown(GUIDE, name="guide", x=40, y=40, w=470, h=860)

# ---------------------------------------------------------------------------
# Station 1 — Python -> browser, no user involved.
# ---------------------------------------------------------------------------
s1 = canvas.markdown(
    "**Station 1 — Python → browser.** This clock is driven by a plain "
    "`while True` loop in Python calling `clock.update(...)` every 3 s. "
    "Watch the `▼ update` frames arrive with nobody touching anything.",
    name="s1", x=560, y=40, w=420, h=120,
)
clock = canvas.label("clock", value="starting…", below=s1)


@canvas.background
def tick():
    while True:
        clock.update(time.strftime("backend time: %H:%M:%S"))
        time.sleep(3)


# ---------------------------------------------------------------------------
# Station 2 — browser -> Python -> browser round trip.
# ---------------------------------------------------------------------------
s2 = canvas.markdown(
    "**Station 2 — the round trip.** Drag the slider: `▲ input` goes up, "
    "`@on_change` runs in Python, `mirror.update()` comes back `▼ down`. "
    "The arrow below is the path your data takes.",
    name="s2", below=clock, gap=40, w=420, h=120,
)
speed = canvas.slider("speed", min=0, max=100, default=50, below=s2)
mirror = canvas.label("mirror", value="drag the slider →", below=speed)
canvas.connect(speed, mirror, text="input → on_change → update", color="blue")


@speed.on_change
def relay(value):
    mirror.update(f"Python saw input: {value}")


# ---------------------------------------------------------------------------
# Station 3 — the user's drags flow back as layout frames.
# ---------------------------------------------------------------------------
s3 = canvas.markdown(
    "**Station 3 — geometry read-back.** Drag or resize the panel below and "
    "watch the `▲ layout` frames. Python's `comp.x / y / w / h` stay in sync, "
    "and `@on_layout` fires with the new geometry.",
    name="s3", below=mirror, gap=40, w=420, h=120,
)
drag_me = canvas.label("drag_me", value="DRAG ME", below=s3)


@drag_me.on_layout
def moved(comp):
    # This update is itself another ▼ frame — moving a panel makes Python
    # react, and Python's reaction goes back over the same wire.
    drag_me.update(f"I'm at ({comp.x:.0f}, {comp.y:.0f}), {comp.w:.0f}px wide")


# ---------------------------------------------------------------------------
# Station 4 — a Custom panel: you define the message vocabulary.
# ---------------------------------------------------------------------------
s4 = canvas.markdown(
    "**Station 4 — your own protocol.** The button below is plain HTML in a "
    "sandboxed iframe. Its JS calls `canvas.send({event: 'hello'})`; Python "
    "routes it with `@panel.on('hello')` and answers with `panel.push(...)`.",
    name="s4", below=drag_me, gap=40, w=420, h=130,
)
hello = canvas.custom(name="hello", below=s4, w=420, h=120, html="""
<style>
  body { margin: 0; font-family: system-ui, sans-serif; padding: 10px; }
  button { font-size: 14px; padding: 6px 14px; cursor: pointer; }
  #reply { margin-top: 8px; color: #0a7; min-height: 1.2em; }
</style>
<button onclick="canvas.send({event: 'hello', sent_at: Date.now()})">
  say hello to Python
</button>
<div id="reply"></div>
<script>
  // Python -> iframe: panel.push(text) lands here.
  canvas.onPush((text) => document.getElementById('reply').textContent = text)
</script>
""")

hello_count = 0


@hello.on("hello")
def greet(msg):
    global hello_count
    hello_count += 1
    hello.push(f"Python says hi back (click #{hello_count}) — "
               f"your click rode an ▲ input frame, this reply a ▼ update")


# ---------------------------------------------------------------------------
# The WIRE TAP — a Custom panel fed every real WebSocket frame.
# ---------------------------------------------------------------------------
wiretap = canvas.custom(name="wiretap", x=1040, y=40, w=640, h=860, html="""
<style>
  body { margin: 0; background: #0f172a; color: #cbd5e1;
         font: 11px/1.5 ui-monospace, Consolas, monospace; }
  h3 { margin: 8px 10px 4px; color: #f1f5f9; font: 600 13px system-ui; }
  #log { padding: 0 10px 10px; }
  #log div { white-space: pre; overflow: hidden; text-overflow: ellipsis; }
  .out { color: #38bdf8; }   /* Python -> browser */
  .in  { color: #fb923c; }   /* browser -> Python */
</style>
<h3>WIRE TAP — live WebSocket frames (newest first)</h3>
<div id="log"></div>
<script>
  const log = document.getElementById('log')
  canvas.onPush((entry) => {
    const line = document.createElement('div')
    line.className = entry.dir
    line.textContent = entry.text
    log.prepend(line)
    while (log.children.length > 40) log.lastChild.remove()
  })
</script>
""")


def install_wiretap(canvas, panel):
    """Mirror every WebSocket frame into ``panel`` via the public observer hook.

    ``canvas.on_frame`` is the supported way to watch the wire: it sees every
    frame Python broadcasts (``"out"``) and every frame a browser sends
    (``"in"``), heartbeats excluded. The log line itself rides a ``panel.push``
    — pycanvas guards taps against their own traffic, so frames the tap causes
    are not re-tapped and logging can't loop.
    """
    boring = {"presence", "chat", "draw", "get_snapshot", "load_snapshot"}

    @canvas.on_frame
    def tap(direction, msg):
        if msg.get("type") in boring:
            return
        arrow = "▼" if direction == "out" else "▲"
        text = f"{arrow} {time.strftime('%H:%M:%S')} {msg.get('type', '?'):<8} "
        text += json.dumps(msg, default=str)[:170]
        panel.push({"dir": direction, "text": text})


install_wiretap(canvas, wiretap)

print("Tour ready — read the guide panel left to right, and keep one eye on "
      "the WIRE TAP while you interact.")
canvas.serve(port=8000)
