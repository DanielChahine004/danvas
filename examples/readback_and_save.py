"""Read-back + saving: watch the backend receive UI edits, then save the canvas.

Run it, then in the browser **drag and resize the panels** (and scribble with the
draw tool if you like). Two things happen:

  1. Read-back: every move/resize is reported to Python. The `@on_layout`
     callbacks print the new geometry to this console, and a Label on the canvas
     mirrors the latest change live.

  2. Saving: back in this terminal, type a command —
       s -> save the canvas (panel formation + your freehand drawings)
       q -> quit

     One file captures both the panel placement and the shapes you drew. Panels
     are code, so only their *placement* is saved (not their behaviour). To bring
     a board back, run the companion load_canvas.py: it recreates the panels in
     code, then `load()` snaps them into place and lays your drawings on top.

Notes
-----
* `save()` round-trips through the browser to read the drawings, so it BLOCKS
  until the page replies — that's why we drive it from the main thread here (not
  from inside a callback, which runs on the server's event loop and would
  deadlock).
* `serve(block=False)` returns immediately so this console loop can run while the
  server keeps serving.
"""

import os

import pycanvas

HERE = os.path.dirname(os.path.abspath(__file__))
CANVAS_FILE = os.path.join(HERE, "saved_canvas.json")

canvas = pycanvas.Canvas()

speed = canvas.insert(pycanvas.Slider(label="speed", min=0, max=100, default=30),
                      x=80, y=80)
gain = canvas.insert(pycanvas.Slider(label="gain", min=1, max=10, default=2),
                     x=80, y=220)
# This label echoes the most recent UI edit so read-back is visible on-canvas too.
moved = canvas.insert(pycanvas.Label(label="last_moved", value="drag a panel…"),
                      x=420, y=80, w=300)


def report(comp):
    """Fired by read-back whenever the user moves/resizes a panel in the UI."""
    msg = (f"{comp._props.get('label')}: "
           f"x={round(comp.x)} y={round(comp.y)} "
           f"w={round(comp.w)} h={round(comp.h)} rot={round(comp.rotation)}°")
    print("  [read-back]", msg)
    moved.update(msg)


# Register read-back on the panels we want to track.
for panel in (speed, gain):
    panel.on_layout(report)


canvas.serve(port=8000, block=False)

print("\nServing at http://127.0.0.1:8000")
print("Drag / resize the panels in the browser — edits print here.")
print("Commands:  s = save canvas   q = quit")
print("(to load a saved canvas, run load_canvas.py)\n")

while True:
    try:
        cmd = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        break
    if cmd == "q":
        break
    elif cmd == "s":
        try:
            canvas.save(CANVAS_FILE)
            print(f"  saved canvas (formation + drawings) -> {CANVAS_FILE}")
        except (RuntimeError, TimeoutError) as e:
            print(f"  could not save: {e} (is the browser tab open?)")
    elif cmd:
        print("  unknown command — use s or q")

canvas.stop()
print("stopped.")
