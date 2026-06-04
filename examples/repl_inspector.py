"""On-canvas REPL + Inspector: poke at the live canvas from inside the canvas.

`enable_repl(globals())` shares this script's namespace with the Repl panel, so
you can type things like

    canvas.servo.x          # read a panel's position
    canvas.servo.update(45) # drive a control from the cell
    status.update("hi")     # any variable in this file is in scope

and see the output right below the editor (Ctrl/Cmd+Enter runs the cell). The
Inspector panel lists every component with its live value and geometry — hit
Refresh after you move panels or change values.

A Repl executes arbitrary Python in this process, so serving is local-only by
default; pass `allow_remote_exec=True` to `serve` to expose it on a network.
"""

import pycanvas

canvas = pycanvas.Canvas().enable_repl(globals())

servo = canvas.insert(
    pycanvas.Slider(label="servo", min=0, max=180, default=90),
    x=80, y=80, name="servo",
)
status = canvas.insert(
    pycanvas.Label(label="status", value="idle"),
    x=80, y=210, name="status",
)


@servo.on_change
def on_servo(value):
    status.update(f"servo at {value}")


canvas.insert(pycanvas.Inspector(label="vars"), x=420, y=80)
canvas.insert(pycanvas.Repl(label="poke"), x=420, y=440)

print("Opening canvas at http://127.0.0.1:8000  (Ctrl+C to stop)")
print("Try in the REPL panel:  canvas.servo.x   or   servo.update(45)")
canvas.serve(port=8000)
