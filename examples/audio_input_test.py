"""Demo: browser microphone → Python via canvas.requestMicrophone().

This demonstrates the microphone binary input channel:

  canvas.requestMicrophone()  →  @panel.on_binary receives int16 PCM chunks
  canvas.onPush(fn)           ←  same chunks forwarded back for local display

The parent page captures microphone audio (getUserMedia runs in the main page,
not the sandboxed iframe — same constraint as camera), converts each chunk from
float32 to int16 PCM, and sends it to Python as a BIN_INPUT binary frame.

Python receives:
  @panel.on('mic_start')  – fires once with { sampleRate, channels }
  @panel.on_binary        – fires per chunk with raw int16 PCM bytes

The panel also receives the same chunks via canvas.onPush so it can show a
live volume bar without a Python round-trip.
"""

import numpy as np
import pycanvas

canvas = pycanvas.Canvas()

info  = canvas.label("stream info", value="tap Start Mic in the panel")
level = canvas.label("dB level",    value="—", x=300, y=40)

panel = canvas.custom(label="mic input", x=40, y=160, w=520, h=200, html="""
  <style>
    body { display:flex; flex-direction:column; gap:10px; padding:12px }
    button { padding:6px 18px; border-radius:6px; border:1px solid #ccc;
             background:#f5f5f5; cursor:pointer; font-size:13px }
    button:disabled { opacity:.4; cursor:default }
    #bar-bg { background:#e5e7eb; border-radius:6px; height:22px; overflow:hidden }
    #bar    { background:#22c55e; height:100%; width:0%; border-radius:6px;
              transition:width .05s }
    #info   { font-size:11px; color:#6b7280 }
  </style>
  <button id="btn" onclick="start()">Start Mic</button>
  <div id="bar-bg"><div id="bar"></div></div>
  <div id="info">waiting…</div>
  <script>
    function start() {
      canvas.requestMicrophone();
      document.getElementById('btn').disabled = true;
      document.getElementById('info').textContent = 'capturing…';
    }

    canvas.onPush(function(data) {
      if (!(data instanceof ArrayBuffer)) return;
      // Compute RMS from int16 samples and show a live volume bar.
      var s = new Int16Array(data), sum = 0;
      for (var i = 0; i < s.length; i++) sum += s[i] * s[i];
      var rms = Math.sqrt(sum / s.length) / 32768;
      // Map 0–1 RMS to 0–100% with slight boost so quiet room still shows
      var pct = Math.min(100, rms * 400);
      document.getElementById('bar').style.width = pct + '%';
      document.getElementById('info').textContent =
        s.length + ' samples/chunk  ·  ' + (rms * 100).toFixed(2) + '% RMS';
    });
  </script>
""")


@panel.on('mic_start')
def mic_started(msg, viewer):
    sr = msg.get('sampleRate', '?')
    ch = msg.get('channels', 1)
    info.update(f"{sr} Hz · {ch}ch · viewer: {viewer.get('name', '?')}")


@panel.on_binary
def got_audio(data: bytes, viewer):
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(samples ** 2)))
    db  = 20 * np.log10(max(rms, 1e-9))
    level.update(f"{db:+.1f} dB  ({len(samples)} samples)")


canvas.serve(port=8000, tunnel=True, ui_inspector=True)
