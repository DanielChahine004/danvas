"""Reopen a board: live panels in code, plus your saved formation + drawings.

Companion to ``readback_and_save.py``. Panels are Python objects with real
behaviour (callbacks, live data), so they are *not* saved — instead we recreate
them here in code, then ``load()`` snaps them back into their saved formation
(matched by name) and lays the saved freehand drawings on top.

``load()`` merges the drawings onto the live panels rather than replacing the
document, so the panels keep working. It's recorded on the server and replayed
to every browser that connects (including reloads), so we load first, then serve.
"""

import os

import pycanvas

HERE = os.path.dirname(os.path.abspath(__file__))
CANVAS_FILE = os.path.join(HERE, "saved_canvas.json")

if not os.path.exists(CANVAS_FILE):
    raise SystemExit(
        f"No saved canvas at {CANVAS_FILE}\n"
        "Run examples/readback_and_save.py first and press 's' to save one."
    )

canvas = pycanvas.Canvas()

# Recreate the panels in code — same labels as readback_and_save.py so the saved
# formation can be matched back onto them.
speed = canvas.insert(pycanvas.Slider("speed", min=0, max=100, default=30),
                      x=80, y=80)
gain = canvas.insert(pycanvas.Slider("gain", min=1, max=10, default=2),
                     x=80, y=220)
moved = canvas.insert(pycanvas.Label("last_moved", value="drag a panel…"),
                      x=420, y=80, w=300)

# One call restores both the panel formation and the freehand drawings.
canvas.load(CANVAS_FILE)

print(f"Loaded {CANVAS_FILE} — opening the canvas (Ctrl+C to stop).")
canvas.serve(port=8000)
