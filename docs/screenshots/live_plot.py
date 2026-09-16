"""A streaming LivePlot for the README clip: two traces, pushed 20x a second."""
import math
import random
import time

import danvas

canvas = danvas.Canvas()

lp = canvas.live_plot("telemetry", label="Live plot", x=80, y=80, w=520, h=300,
                      max_points=200)
status = canvas.label("status", "streaming", x=620, y=80, w=220)


@canvas.background
def feed():
    t = 0.0
    while True:
        lp.push({"signal": math.sin(t) + random.uniform(-0.08, 0.08),
                 "envelope": 0.6 * math.sin(t * 0.25)})
        status.update(f"t = {t:5.1f} s")
        t += 0.05
        time.sleep(0.05)


canvas.serve(port=8000)
