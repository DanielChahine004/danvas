"""The README hello world, verbatim (the screenshot drags the slider itself)."""
import danvas

canvas = danvas.Canvas()
servo  = canvas.slider("servo_1", min=0, max=180, default=90)
status = canvas.label("status", "idle")

@servo.on_change
def handle(value):
    status.update(f"servo at {value}")

canvas.serve(port=8000)
