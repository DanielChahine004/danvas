"""Embed a live webcam feed into a canvas using OpenCV."""

import threading
import time

import cv2

import pycanvas

canvas = pycanvas.Canvas()

feed = canvas.video("webcam")
status = canvas.label("status", "starting...")


def worker():
    # 0 is the default camera; change the index for a different device.
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        status.update("could not open camera")
        return

    frames = 0
    t0 = time.time()
    while True:
        ok, frame = cap.read()
        if not ok:
            status.update("dropped frame")
            time.sleep(0.1)
            continue

        feed.update(frame)

        # Report a rough FPS once a second.
        frames += 1
        dt = time.time() - t0
        if dt >= 1.0:
            status.update(f"{frames / dt:.1f} fps")
            frames = 0
            t0 = time.time()

        time.sleep(1 / 30)  # cap at ~30 fps


threading.Thread(target=worker, daemon=True).start()

print("Opening canvas at http://127.0.0.1:8000  (Ctrl+C to stop)")
canvas.serve(port=8000)
