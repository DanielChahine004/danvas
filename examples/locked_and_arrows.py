"""Locked panels and arrow connectors: a small signal-flow diagram.

Lays out four panels at fixed positions and wires them together with arrows so
the canvas reads like a pipeline:

    [input] --> [gain] --> [output]
                  |
                  v
               [status]

The stage panels are inserted ``movable=False, resizable=False`` so the diagram
keeps its shape — they can't be dragged or resized in the browser, but their
sliders STILL WORK (unlike a full ``locked=True``, which also blocks
interaction). The ``lock`` toggle at the bottom flips that live: switch it to
"unlocked" to rearrange the panels, back to "locked" to pin them again. Arrows
are bound to the panels, so while unlocked, dragging a panel drags its arrows.
"""

import pycanvas

canvas = pycanvas.Canvas()

# Stage panels at fixed positions: pinned (no move/resize) but still interactive.
source = canvas.insert(
    pycanvas.Slider(label="input", min=0, max=100, default=20),
    x=80, y=80, movable=False, resizable=False,
)
gain = canvas.insert(
    pycanvas.Slider(label="gain", min=1, max=10, default=2),
    x=420, y=80, movable=False, resizable=False,
)
output = canvas.insert(
    pycanvas.Label(label="output", value="0"),
    x=760, y=80, movable=False, resizable=False,
)
status = canvas.insert(
    pycanvas.Label(label="status", value="idle"),
    x=420, y=300, movable=False, resizable=False,
)

# Wire the panels together. Arrows bind to the panels and reroute on their own.
# Like components they take a `label` (shown on the arrow and used for
# canvas.<label> lookup) plus tldraw arrow props like color.
canvas.connect(source, gain, label="scale", color="blue")
canvas.connect(gain, output, label="result", color="green", bend=-180)
canvas.connect(gain, status, label="monitor", dash="dashed")

# A control panel that stays draggable, plus a toggle to lock/unlock the stages.
lock = canvas.insert(
    pycanvas.Toggle(label="lock", options=["locked", "unlocked"]),
    x=80, y=300, movable=False, resizable=False,
)


def recompute():
    result = source.value * gain.value if source.value is not None else 0
    output.update(str(round(result, 1)))
    status.update(f"{source.value} x {gain.value} = {round(result, 1)}")
    # Recolor an arrow live, by label, to flag a high result.
    canvas["monitor"].color = "red" if result > 500 else "grey"


@source.on_change
def on_source(_):
    recompute()


@gain.on_change
def on_gain(_):
    recompute()


@lock.on_change
def on_lock(value):
    pinned = value == "locked"
    for panel in (source, gain, output, status):
        # Pin position and size but leave the controls interactive.
        panel.movable = not pinned
        panel.resizable = not pinned
    print("stages", "pinned" if pinned else "free")


print("Opening canvas at http://127.0.0.1:8000  (Ctrl+C to stop)")
print("Move the sliders to push values down the arrows.")
print("Flip the 'lock' toggle to 'unlocked' to rearrange the diagram.")
canvas.serve(port=8000)
