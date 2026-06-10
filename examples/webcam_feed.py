"""Embed a live webcam feed into a canvas using OpenCV, with optional audio.

Video is captured with OpenCV. Audio is captured separately from the default
microphone with ``sounddevice`` (``pip install pycanvas[audio]``) and streamed
to the browser, where you click "Enable audio" on the panel to start playback
(the browser blocks autoplay until a user gesture). OpenCV can't capture audio,
so the two are independent streams — handy for monitoring, not lip-synced.

If ``sounddevice`` isn't installed the example still runs video-only.
"""

import threading
import time

import cv2

import pycanvas

canvas = pycanvas.Canvas()

# queue="latest" drops stale frames for a slow viewer so latency stays bounded
# rather than the feed backing up (it's VideoFeed's default; explicit here).
feed = canvas.video("webcam", queue="latest")
sound = canvas.audio("mic", sample_rate=16000)
status = canvas.label("status", "starting...")


def video_worker():
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


def audio_worker():
    try:
        import sounddevice as sd
    except ImportError:
        print("[webcam] audio disabled — run `pip install pycanvas[audio]` "
              "(needs sounddevice) to stream the microphone")
        return

    # 16 kHz mono keeps the stream light (~256 kbps before base64). The callback
    # runs on PortAudio's thread; feed.update is thread-safe. float32 in [-1, 1]
    # is converted to int16 by AudioFeed.
    def callback(indata, frames, time_info, sd_status):
        sound.update(indata[:, 0])

    try:
        with sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                            blocksize=1024, callback=callback):
            while True:
                time.sleep(0.5)
    except Exception as exc:  # no input device, etc. -- don't kill the app
        print(f"[webcam] audio capture failed: {exc}")


threading.Thread(target=video_worker, daemon=True).start()
threading.Thread(target=audio_worker, daemon=True).start()

canvas.serve(port=8000, tunnel=True)
