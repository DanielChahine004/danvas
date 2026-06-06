"""Live demo: a synthetic video feed plus a label updated from a worker thread."""

import math
import threading
import time

import numpy as np

import pycanvas

canvas = pycanvas.Canvas()

gain = canvas.slider("gain", min=1, max=10, default=3)
reading = canvas.label("reading", "0.0")
feed = canvas.video("synthetic camera")


def worker():
    t = 0.0
    while True:
        # Synthetic sensor reading scaled by the slider's current value.
        val = gain.value * math.sin(t)
        reading.update(f"{val:.2f}")

        # Synthetic moving gradient frame (BGR).
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        x = int((math.sin(t) * 0.5 + 0.5) * 319)
        frame[:, :, 1] = np.linspace(0, 255, 320, dtype=np.uint8)
        frame[:, max(0, x - 5):x + 5, 2] = 255
        feed.update(frame)

        t += 0.1
        time.sleep(0.05)


threading.Thread(target=worker, daemon=True).start()

print("Opening canvas at http://127.0.0.1:8000  (Ctrl+C to stop)")
canvas.serve(port=8000)
