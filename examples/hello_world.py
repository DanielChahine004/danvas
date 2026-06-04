"""Minimal PyCanvas demo: a slider and a label that mirrors it."""

import pycanvas

canvas = pycanvas.Canvas()

servo = canvas.insert(pycanvas.Slider(label="servo_1", min=0, max=180, default=90))
status = canvas.insert(pycanvas.Label(label="status", value="idle"))


@servo.on_change
def on_servo(value):
    print("servo_1 =", value)
    status.update(f"servo at {value}")


print("Opening canvas at http://127.0.0.1:8000  (Ctrl+C to stop)")
canvas.serve(port=8000)
