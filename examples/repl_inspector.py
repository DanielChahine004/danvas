"""On-canvas REPL + Inspector: poke at the live canvas from inside the canvas.

`enable_repl(globals())` shares this script's namespace with the Repl panel, so
you can type things like

    canvas.servo.x          # read a panel's position
    canvas.servo.update(45) # drive a control from the cell
    status.update("hi")     # any variable in this file is in scope

and see the output right below the editor (Ctrl/Cmd+Enter runs the cell). The
Inspector panel has a dropdown to switch between the canvas panels (every
component, Repls and Inspectors included, with live value + geometry) and the
shared REPL namespace (this file's globals, `canvas` included). Hit Refresh
after you move panels or change values, and click any row to drill into that
object's fields and attributes.

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


# Some plain variables for the globals inspector to show.
gain = 1.5
mode = "auto"
waypoints = [(0, 0), (10, 5), (20, 0)]

# One inspector with a source dropdown in its header: switch between the canvas
# panels and the shared REPL namespace (this file's globals) live. It also has a
# name-search box, a type filter, and click-to-drill-into-fields.
canvas.insert(pycanvas.Inspector(label="inspector", refresh=1.0), x=420, y=80)
canvas.insert(pycanvas.Repl(label="poke"), x=80, y=320)

print("Opening canvas at http://127.0.0.1:8000  (Ctrl+C to stop)")
print("Try in the REPL panel:  canvas.servo.x   or   servo.update(45)")
canvas.serve(port=8000)
