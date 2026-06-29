"""Locked panels and arrow connectors: a small signal-flow diagram.

Lays out four panels at fixed positions and wires them together with arrows so
the canvas reads like a pipeline:

    [input] --> [gain] --> [output]
                  |
                  v
               [status]

The stage panels are inserted ``draggable=False, resizable=False`` so the diagram
keeps its shape — they can't be dragged or resized in the browser, but their
sliders STILL WORK (unlike a full ``locked=True``, which also blocks
interaction). The ``lock`` toggle at the bottom flips that live: switch it to
"unlocked" to rearrange the panels, back to "locked" to pin them again. Arrows
are bound to the panels, so while unlocked, dragging a panel drags its arrows.
"""

import danvas

canvas = danvas.Canvas()

# Stage panels at fixed positions: pinned (no move/resize) but still interactive.
source = canvas.insert(
    danvas.Slider("input", min=0, max=100, default=20),
    x=80, y=80, draggable=False, resizable=False,
)
gain = canvas.insert(
    danvas.Slider("gain", min=1, max=10, default=2),
    x=420, y=80, draggable=False, resizable=False,
)
output = canvas.insert(
    danvas.Label("output", value="0"),
    x=760, y=80, draggable=False, resizable=False,
)
status = canvas.insert(
    danvas.Label("status", value="idle"),
    x=420, y=300, draggable=False, resizable=False,
)

# Wire the panels together. Arrows bind to the panels and reroute on their own.
# `name` is each arrow's identity (canvas.<name> lookup, unique); pass `text=`
# to caption the arrow (none shown otherwise), plus shape props like color.
canvas.connect(source, gain, name="scale", text="scale", color="blue")
canvas.connect(gain, output, name="result", text="result", color="green", bend=-180)
canvas.connect(gain, status, name="monitor", text="monitor", dash="dashed")

# A control panel that stays draggable, plus a toggle to lock/unlock the stages.
lock = canvas.insert(
    danvas.Toggle("lock", options=["locked", "unlocked"]),
    x=80, y=300, draggable=False, resizable=False,
)


def recompute():
    result = source.value * gain.value if source.value is not None else 0
    output.update(str(round(result, 1)))
    status.update(f"{source.value} x {gain.value} = {round(result, 1)}")
    # Recolor an arrow live, by name, to flag a high result.
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
        panel.draggable = not pinned
        panel.resizable = not pinned
    print("stages", "pinned" if pinned else "free")


print("Move the sliders to push values down the arrows.")
print("Flip the 'lock' toggle to 'unlocked' to rearrange the diagram.")
canvas.serve(port=8000, host="0.0.0.0")
