"""Demo: high-rate client → host binary stream via canvas.requestCamera().

This demonstrates the custom binary input channel:
  canvas.requestCamera(opts)  →  @panel.on_binary receives each frame as bytes

The parent page captures the webcam (getUserMedia runs in the main page, not
the sandboxed iframe — see comments in bridge.js for why), encodes each frame
as JPEG, and sends it to Python as a BIN_INPUT binary frame. The frame also
arrives in canvas.onPush so the panel can display it locally without a Python
round-trip. Python measures throughput and echoes each frame back (flipped)
as a VideoFeed so the full round-trip path is exercised.

canvas.requestCamera opts:
  width, height  – capture resolution (default 320×240)
  quality        – JPEG quality 0–1 (default 0.7)
  fps            – cap the rate; omit / 0 for max (rAF-limited, ≤60 fps)

Requires: pip install opencv-python numpy
"""

import time
import numpy as np
import cv2
import pycanvas

canvas = pycanvas.Canvas()

status = canvas.label("status", value="waiting for frames…")
feed   = canvas.video("echo", label="echo (Python processed)", x=420, y=40)

panel = canvas.custom(label="webcam source", x=40, y=40, w=360, h=300, html="""
  <canvas id="v" style="width:100%;border-radius:4px"></canvas>
  <div id="s" style="font-size:12px;color:#888;margin-top:6px">starting…</div>
  <script>
    var display = document.getElementById('v');
    var ctx = display.getContext('2d');
    display.width = 320; display.height = 240;
    var n = 0, t0 = performance.now();

    // Ask the parent page to open the camera at max rate (rAF-limited, ≤60 fps).
    // Each captured JPEG frame is sent to Python as a binary WebSocket frame
    // (BIN_INPUT → @panel.on_binary) AND forwarded here via canvas.onPush so
    // the panel can display it without a Python round-trip.
    canvas.requestCamera({width: 320, height: 240, quality: 0.6});

    canvas.onPush(function(data) {
      if (!(data instanceof ArrayBuffer)) return;
      n++;
      var elapsed = (performance.now() - t0) / 1000;
      if (elapsed >= 1) {
        document.getElementById('s').textContent =
          (n / elapsed).toFixed(1) + ' fps  ·  ' + (data.byteLength / 1024).toFixed(1) + ' KB/frame';
        n = 0; t0 = performance.now();
      }
      // createImageBitmap decodes the JPEG off the main thread — faster than
      // new Image() + onload for high-rate streams.
      createImageBitmap(new Blob([data], {type:'image/jpeg'})).then(function(bmp) {
        ctx.drawImage(bmp, 0, 0, 320, 240);
        bmp.close();
      });
    });
  </script>
""")

_frame_count = 0
_t0 = time.monotonic()

@panel.on_binary
def got_frame(data: bytes, viewer):
    global _frame_count, _t0
    _frame_count += 1
    now = time.monotonic()
    elapsed = now - _t0
    if elapsed >= 1.0:
        fps = _frame_count / elapsed
        kb = len(data) / 1024
        status.update(
            f"{fps:.1f} fps  ·  {kb:.1f} KB/frame  ·  {fps * kb:.0f} KB/s"
            f"  ·  viewer: {viewer.get('name', '?')}"
        )
        _frame_count = 0
        _t0 = now

    arr = np.frombuffer(data, np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return
    feed.update(cv2.flip(frame, 1))

canvas.serve(port=8000, tunnel=True, ui_inspector=True)
