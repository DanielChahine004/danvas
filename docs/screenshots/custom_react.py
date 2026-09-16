"""Custom + React panels for the README screenshot.

Re-uses the panel sources from examples/analog_clock.py (a Custom HTML/SVG
clock driven by canvas.onPush) and examples/react_component.py (a React
component talking back with canvas.send), pulled from those files so the
README picture and the examples cannot drift apart.
"""
import os
import threading
import time
from datetime import datetime

import danvas

_EX = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "examples")


def _src(fname, var):
    """The triple-quoted string assigned to `var` in examples/<fname>."""
    text = open(os.path.join(_EX, fname), encoding="utf-8").read()
    head = var + ' = """'
    i = text.index(head) + len(head)
    return text[i:text.index('"""', i)]


CLOCK_HTML = _src("analog_clock.py", "CLOCK_HTML")
PING_JSX = _src("react_component.py", "PING_JSX")

canvas = danvas.Canvas()

clock = canvas.custom(html=CLOCK_HTML, name="analog_clock", label="Custom (HTML + SVG)",
                      x=80, y=80, w=250, h=250)
clock_note = canvas.markdown(
    "`canvas.custom(html=...)` — your own HTML/SVG/JS in a sandboxed iframe. "
    "Python calls `clock.push({...})` once a second; the page redraws the hands.",
    name="clock_note", label="Python side", x=80, y=360, w=250)

ping = canvas.react(PING_JSX, name="ping", label="React (JSX, compiled in-browser)",
                    props={"title": "Hello, React"}, x=380, y=80, w=320, h=200)
status = canvas.label("status", value="click ping", x=380, y=310, w=320)
react_note = canvas.markdown(
    "`canvas.react(JSX)` — a component with hooks and `canvas.send(...)`; "
    "`@panel.on('ping')` receives it in Python.",
    name="react_note", label="Python side", x=380, y=400, w=320)


@ping.on("ping")
def on_ping(msg):
    status.update(f"ping #{msg['count']}")


def tick():
    while True:
        now = datetime.now()
        clock.push({"h": now.hour, "m": now.minute, "s": now.second})
        ping.push(time.strftime("%H:%M:%S"))
        time.sleep(1)


threading.Thread(target=tick, daemon=True).start()
canvas.serve(port=8000)
