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

from . import _jsx

_SOURCE = _jsx.load("audio")


class AudioFeed(_React):
    BINARY_TYPE = BINARY_REACT
    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {"sampleRate": "number", "channels": "number"},
        "updates": {},
        "events": [],
        "binary": "receives AUDIO (code 2): int16-LE PCM streamed by the owner",
    }
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
        # Audio scheduler — like VideoFeed. Bypasses shape props so
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
