"""Minimal PyCanvas demo: a slider and a label that mirrors it."""

import pycanvas

canvas = pycanvas.Canvas()

# Factory shorthand: canvas.<component>(...) builds and inserts in one call.
servo = canvas.slider("servo_1", min=0, max=180, default=90)
status = canvas.label("status", value="idle")


@servo.on_change
def on_servo(value):
    print("servo_1 =", value)
    status.update(f"servo at {value}")


canvas.serve(port=8000)
