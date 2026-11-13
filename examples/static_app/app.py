"""A danvas app that runs as a link as well as from Python.

    python app.py          serves it at http://localhost:8000
    ?repo=...&app=...      the static page runs this same file in the browser

A browser has no threads, so this paces itself with threading.Timer (which the
static page runs on the browser's event loop) rather than a thread that sleeps.
"""
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import danvas

FACE = """
<svg width="200" height="200" viewBox="-100 -100 200 200">
  <circle r="95" fill="white" stroke="#333" stroke-width="4"/>
  <line id="h" x1="0" y1="0" x2="0" y2="-45" stroke="#333" stroke-width="6" stroke-linecap="round"/>
  <line id="m" x1="0" y1="0" x2="0" y2="-65" stroke="#666" stroke-width="4" stroke-linecap="round"/>
  <line id="s" x1="0" y1="0" x2="0" y2="-80" stroke="#d33" stroke-width="2" stroke-linecap="round"/>
  <circle r="4" fill="#333"/>
</svg>
<script>
  const turn = (id, deg) => document.getElementById(id).setAttribute("transform", `rotate(${deg})`);
  canvas.onPush(({h, m, s}) => { turn("s", s * 6); turn("m", (m + s / 60) * 6); turn("h", (h % 12 + m / 60) * 30); });
</script>
"""

canvas = danvas.Canvas()
canvas.markdown((Path(__file__).parent / "notes.md").read_text(encoding="utf-8"), name="about", x=60, y=60, w=420, h=200)
clock = canvas.custom(html=FACE, name="clock", x=520, y=40, w=240, h=240)
speed = canvas.slider("clock speed", min=1, max=600, default=1, x=60, y=300)
status = canvas.label("status", "", below=speed)

# the clock's own time: it starts at now and runs `speed` times faster than real time
shown, last = datetime.now(), time.monotonic()


def tick():
    global shown, last
    t = time.monotonic()
    shown += timedelta(seconds=(t - last) * speed.value)
    last = t
    clock.push({"h": shown.hour, "m": shown.minute, "s": shown.second + shown.microsecond / 1e6})
    status.update(f"{shown:%H:%M:%S} at {speed.value}x")
    threading.Timer(0.1, tick).start()


tick()
canvas.serve(port=8000)
