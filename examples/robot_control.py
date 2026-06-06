"""Robot control dashboard: sliders, a mode toggle, a live plot and video.

Demonstrates every component together. Two servo sliders feed a rolling
history plot; a Toggle switches between manual/vision modes; a synthetic
camera feed and a status label round it out.
"""

import math
import threading
import time

import numpy as np

import pycanvas

canvas = pycanvas.Canvas()

# This example uses the explicit two-step form: pycanvas.X(...) builds the panel
# object, canvas.insert(...) places it. Reach for this when you want to construct
# a panel up front and insert it later (or into a different canvas). For the
# common build-and-place-now case, the canvas.<component>(...) factories are
# shorter -- see hello_world.py and sensor_dashboard.py.
servo_1 = canvas.insert(pycanvas.Slider("servo_1", min=0, max=180, default=90))
servo_2 = canvas.insert(pycanvas.Slider("servo_2", min=0, max=180, default=45))
mode = canvas.insert(pycanvas.Toggle("mode", options=["manual", "vision"]))
status = canvas.insert(pycanvas.Label("status", value="idle"))
plot = canvas.insert(
    pycanvas.LivePlot(
        name="servo history",
        traces=["servo_1", "servo_2"],
        max_points=200,
        layout={"yaxis": {"range": [0, 180]}},
    )
)
feed = canvas.insert(pycanvas.VideoFeed("camera"))


@servo_1.on_change
def on_s1(value):
    status.update(f"servo_1 -> {value}")


@servo_2.on_change
def on_s2(value):
    status.update(f"servo_2 -> {value}")


@mode.on_change
def on_mode(value):
    print("mode switched to:", value)
    status.update(f"mode: {value}")
    # In vision mode the worker drives the servos, so make the sliders inert to
    # the user while their thumbs keep tracking the pushed values. `interactive`
    # blocks UI input without locking the shape -- unlike lock(), which would
    # also freeze the programmatic updates and stop the thumb moving.
    drive = value == "vision"
    servo_1.interactive = not drive
    servo_2.interactive = not drive


def worker():
    t = 0.0
    while True:
        # In manual mode, record the slider positions; in vision mode, drive
        # the servos automatically with a sweep.
        if mode.value == "vision":
            v1 = 90 + 60 * math.sin(t)
            v2 = 90 + 60 * math.cos(t)
            servo_1.update(round(v1))
            servo_2.update(round(v2))

        # LivePlot streams smoothly — safe to push every loop (10 Hz here).
        plot.push({"servo_1": servo_1.value, "servo_2": servo_2.value})

        # Synthetic camera frame with a sweeping bar.
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        x = int((math.sin(t) * 0.5 + 0.5) * 319)
        frame[:, max(0, x - 4):x + 4, 1] = 255
        feed.update(frame)

        t += 0.1
        time.sleep(0.1)


threading.Thread(target=worker, daemon=True).start()

print("Toggle 'vision' to let the robot drive its own servos.")
canvas.serve(port=8000)
