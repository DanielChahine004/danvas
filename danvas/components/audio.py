"""AudioFeed: streams PCM audio chunks to the browser for live playback.

Mirrors :class:`~danvas.VideoFeed` for sound. Capture audio however you like
(e.g. ``sounddevice``) and push raw PCM chunks; the browser schedules them
back-to-back through the Web Audio API so they play as a continuous stream::

    feed = canvas.audio("mic", sample_rate=16000)
    feed.update(chunk)   # call repeatedly with PCM samples

Like a webcam, this is a one-way server->browser push, so it pairs naturally
with a :class:`VideoFeed` (the two are independent streams — there is no tight
A/V sync). Browsers won't start audio until the user clicks the panel's enable
button, per the browser autoplay policy.
"""

from ..bridge import BINARY_REACT
from .react import React as _React

_SOURCE = '''
function Component({ canvas, props }) {
  const sampleRate = (props && props.sampleRate) || 16000;
  const channels = (props && props.channels) || 1;
  const [on, setOn] = React.useState(false);
  const ctxRef = React.useRef(null);
  const nextRef = React.useRef(0);
  const onRef = React.useRef(false);
  React.useEffect(() => { onRef.current = on; }, [on]);
  React.useEffect(() => {
    const LEAD = 0.12;
    return canvas.onFrame((payload) => {
      const ctx = ctxRef.current;
      if (!onRef.current || !ctx || !(payload instanceof ArrayBuffer)) return;
      let n = payload.byteLength;
      n -= n % 2;
      const pcm = new Int16Array(payload, 0, n / 2);
      const frames = Math.floor(pcm.length / channels);
      if (!frames) return;
      const buf = ctx.createBuffer(channels, frames, sampleRate);
      for (let ch = 0; ch < channels; ch++) {
        const out = buf.getChannelData(ch);
        for (let i = 0; i < frames; i++) out[i] = pcm[i * channels + ch] / 32768;
      }
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      const now = ctx.currentTime;
      let start = nextRef.current;
      if (start < now + 0.01) start = now + LEAD;
      src.start(start);
      nextRef.current = start + buf.duration;
    });
  }, [sampleRate, channels]);
  React.useEffect(() => {
    return () => {
      const ctx = ctxRef.current;
      if (ctx) ctx.close().catch(() => {});
      ctxRef.current = null;
    };
  }, []);
  const toggle = async () => {
    if (!on) {
      let ctx = ctxRef.current;
      if (!ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        ctx = new AC({ sampleRate });
        ctxRef.current = ctx;
      }
      try { await ctx.resume(); } catch {}
      nextRef.current = ctx.currentTime + 0.12;
      setOn(true);
    } else {
      setOn(false);
      const ctx = ctxRef.current;
      if (ctx) ctx.suspend().catch(() => {});
    }
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: 8, padding: 12 }}>
      <button
        onClick={toggle}
        style={{
          alignSelf: "flex-start",
          padding: "6px 12px",
          border: "none",
          borderRadius: 6,
          fontSize: 14,
          fontWeight: 600,
          cursor: "pointer",
          background: on ? "var(--pc-accent)" : "var(--pc-off-bg, #e5e7eb)",
          color: on ? "var(--pc-accent-text, #fff)" : "var(--pc-off-text, #374151)",
        }}
      >
        {on ? "🔊 Audio on" : "🔈 Enable audio"}
      </button>
      <div style={{ fontSize: 12, color: "var(--pc-muted, #9ca3af)" }}>
        {sampleRate} Hz · {channels === 1 ? "mono" : channels + " ch"}
      </div>
    </div>
  );
}
'''


class AudioFeed(_React):
    BINARY_TYPE = BINARY_REACT
    default_w = 260
    default_h = 120

    def __init__(self, name="audio", sample_rate=16000, channels=1, label=None, color=None,
                 queue="latest"):
        # Live audio defaults to ``latest``: if a viewer falls behind, stale
        # chunks are dropped rather than building a playback backlog. Pass
        # ``queue="fifo"`` only when every sample must arrive (e.g. recording).
        super().__init__(source=_SOURCE, name=name, label=label, color=color, queue=queue,
                         w=260, h=120,
                         props={"sampleRate": int(sample_rate), "channels": int(channels)})
        self._channels = int(channels)

    def update(self, chunk):
        """Push one chunk of PCM audio to the browser.

        ``chunk`` may be:

        - a NumPy array of ``float32`` in ``[-1, 1]`` (converted to int16),
        - a NumPy array already in ``int16``, or
        - raw ``bytes`` of little-endian int16 samples.

        For multi-channel audio pass shape ``(frames, channels)`` (or
        already-interleaved bytes); mono is ``(frames,)``.
        """
        pcm = self._to_int16_bytes(chunk)
        if not pcm:
            return
        # Rides a binary WebSocket frame (no base64, no JSON) straight to the Web
        # Audio scheduler — like VideoFeed. Bypasses tldraw shape props so
        # high-rate chunks never touch undo history.
        self.push_binary(pcm)

    @staticmethod
    def _to_int16_bytes(chunk):
        """Normalise any accepted chunk form to little-endian int16 bytes."""
        if isinstance(chunk, (bytes, bytearray, memoryview)):
            return bytes(chunk)
        # NumPy is only needed for the array path, so it's imported lazily here
        # (and lives in the ``[audio]`` extra) — a canvas of sliders/plots that
        # never streams audio doesn't pay for the ~60 MB dependency. Callers
        # already passing raw int16 ``bytes`` skip this branch entirely. The
        # import goes through importlib so PyInstaller's static analysis can't
        # see it and drag numpy (plus its MKL stack) into a baked app that never
        # streams array audio; bake() bundles numpy itself when an AudioFeed is
        # on the canvas (see danvas/bake.py).
        import importlib

        np = importlib.import_module("numpy")

        arr = np.asarray(chunk)
        if arr.size == 0:
            return b""
        if arr.dtype.kind == "f":
            arr = np.clip(arr, -1.0, 1.0)
            arr = (arr * 32767.0).astype("<i2")
        elif arr.dtype != np.dtype("<i2"):
            arr = arr.astype("<i2")
        # Interleave (frames, channels) -> flat; a 1-D array is already flat.
        return np.ascontiguousarray(arr).tobytes()
