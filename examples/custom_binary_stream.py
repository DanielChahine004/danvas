"""High-telemetry binary streaming into a Custom panel (`push_binary`).

`Custom.push(data)` streams JSON — fine for tens of Hz of small payloads. For
*frame- or array-grade* telemetry, `Custom.push_binary(bytes)` sends raw bytes on
a binary WebSocket frame instead (no JSON serialize, no base64 — the same fast
path `VideoFeed`/`AudioFeed` use). The iframe receives it on the **same**
`canvas.onPush(fn)`, but as an `ArrayBuffer`, so we branch on the type to tell the
two streams apart.

This demo is a little oscilloscope: a background loop packs a window of float32
samples and pushes the raw buffer ~60 times a second; the panel decodes each
`ArrayBuffer` into a `Float32Array` and draws the sweep, showing a live
throughput readout. A slider drives the signal frequency back into Python — the
panel stays fully bidirectional while the firehose runs. `queue="latest"` keeps a
slow viewer from backing the producer up: stale buffers are dropped, not queued.

    python examples/custom_binary_stream.py
"""

import math
import struct
import time

import pycanvas

N = 512          # samples per frame (one oscilloscope sweep)
FPS = 60         # frames per second -> ~512 * 60 * 4 bytes ≈ 125 KB/s of raw f32

canvas = pycanvas.Canvas()

# The panel: an HTML <canvas> that decodes each binary push and draws it. Control
# messages (none here, but the branch shows the pattern) would arrive as plain
# objects on the same onPush; binary frames arrive as an ArrayBuffer.
SCOPE_HTML = """
<!doctype html>
<html>
  <head>
    <style>
      html, body { margin: 0; height: 100%; background: #0b1020; overflow: hidden; }
      #scope { display: block; width: 100%; height: 100%; }
      #stats {
        position: absolute; top: 8px; left: 10px; color: #5eead4;
        font: 12px ui-monospace, monospace; text-shadow: 0 0 6px #042f2e;
        pointer-events: none; white-space: pre;
      }
    </style>
  </head>
  <body>
    <canvas id="scope"></canvas>
    <div id="stats">waiting for data…</div>
    <script>
      const cv = document.getElementById('scope');
      const ctx = cv.getContext('2d');
      const stats = document.getElementById('stats');

      // Throughput accounting, reset once a second.
      let frames = 0, bytes = 0, last = performance.now();

      function fit() {
        const r = window.devicePixelRatio || 1;
        cv.width = cv.clientWidth * r;
        cv.height = cv.clientHeight * r;
        ctx.setTransform(r, 0, 0, r, 0, 0);
      }
      window.addEventListener('resize', fit);
      fit();

      function draw(samples) {
        const w = cv.clientWidth, h = cv.clientHeight, mid = h / 2;
        ctx.clearRect(0, 0, w, h);
        // midline
        ctx.strokeStyle = '#1e293b'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(w, mid); ctx.stroke();
        // waveform
        ctx.strokeStyle = '#34d399'; ctx.lineWidth = 1.5;
        ctx.beginPath();
        for (let i = 0; i < samples.length; i++) {
          const x = (i / (samples.length - 1)) * w;
          const y = mid - samples[i] * (mid * 0.9);
          i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        }
        ctx.stroke();
      }

      canvas.onPush((d) => {
        // The two streams share this callback — branch on the payload type.
        if (d instanceof ArrayBuffer) {
          const samples = new Float32Array(d);   // zero-copy view of the raw bytes
          bytes += d.byteLength;
          frames += 1;
          draw(samples);
          const now = performance.now();
          if (now - last >= 1000) {
            const fps = (frames * 1000 / (now - last)).toFixed(0);
            const kb = (bytes / 1024 / ((now - last) / 1000)).toFixed(0);
            stats.textContent = `${fps} fps   ${kb} KB/s   ${N} samples/frame (binary)`;
            frames = 0; bytes = 0; last = now;
          }
        } else {
          // A JSON push (control/state) would land here instead.
          stats.textContent = 'control: ' + JSON.stringify(d);
        }
      });
    </script>
  </body>
</html>
""".replace("${N}", str(N))

scope = canvas.custom(
    html=SCOPE_HTML, name="scope", x=40, y=40, w=560, h=300,
    queue="latest",         # drop stale buffers for a slow viewer (like VideoFeed)
)

freq = canvas.slider("frequency", min=1, max=20, default=4, below=scope,
                     label="signal frequency (Hz)")


@canvas.background
def stream():
    """Pack a window of float32 samples and push the raw bytes ~FPS times/sec."""
    period = 1.0 / FPS
    phase = 0.0
    while True:
        f = freq.value                       # live-driven by the slider
        # One sweep: a sine at the chosen frequency plus a little noise, so the
        # trace clearly moves. struct packs N little-endian float32s into bytes.
        samples = [
            math.sin(phase + 2 * math.pi * f * i / N)
            + 0.05 * math.sin(37.0 * i / N)
            for i in range(N)
        ]
        scope.push_binary(struct.pack(f"<{N}f", *samples))
        phase += 0.30                        # scroll the waveform each frame
        time.sleep(period)


if __name__ == "__main__":
    canvas.serve(port=8000)
