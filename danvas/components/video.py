"""VideoFeed: streams OpenCV frames to the browser as binary JPEG.

Rendered as a native React panel (mounted by ReactHost): each frame rides a
*binary* WebSocket frame (no base64, no JSON, no shape-prop churn) and the panel
paints it with ``canvas.onFrame`` — wrapping each ``ArrayBuffer`` in a Blob ->
object URL -> ``<img>`` and revoking the previous URL once the next frame paints,
so the stream can't leak memory and never triggers a React re-render. The Python
side (OpenCV JPEG encoding, or pre-encoded bytes) is unchanged.
"""

from . import _theme
from .react import React
from ..bridge import BINARY_VIDEO  # noqa: F401 – re-exported for bake.py discovery

# The panel: subscribe to the binary push stream and paint each JPEG frame to an
# <img>. ``onFrame`` delivers each frame as a zero-copy ArrayBuffer (no React
# re-render); we revoke the prior frame's object URL once the next one paints.
# Authored as a plain string so its JSX braces survive — nothing is substituted.
_VIDEO_SOURCE = r"""
function Component({ canvas }) {
  const imgRef = React.useRef(null);
  const urlRef = React.useRef(null);
  const [live, setLive] = React.useState(false);
  React.useEffect(() => {
    const off = canvas.onFrame((d) => {
      if (!(d instanceof ArrayBuffer)) return;
      const el = imgRef.current;
      if (!el) return;
      const url = URL.createObjectURL(new Blob([d], { type: "image/jpeg" }));
      const prev = urlRef.current;
      // Revoke the prior frame's URL only after the new one has painted.
      el.onload = () => { if (prev) URL.revokeObjectURL(prev); };
      urlRef.current = url;
      el.src = url;
      setLive(true);  // React bails if already true, so this is cheap per frame
    });
    return () => {
      off();
      if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null; }
    };
  }, [canvas]);
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
      background: "var(--pc-video-bg, #000)", borderRadius: 4, overflow: "hidden", height: "100%" }}>
      <img ref={imgRef} draggable={false}
        style={{ width: "100%", height: "100%", objectFit: "contain",
          pointerEvents: "none", display: live ? "block" : "none" }} />
      {!live && <span style={{ color: "var(--pc-muted)", fontSize: 13 }}>no signal</span>}
    </div>
  );
}
"""


class VideoFeed(React):
    default_w = 340
    default_h = 280
    BINARY_TYPE = BINARY_VIDEO

    def __init__(self, name, quality=70, label=None, encode=True, queue="latest",
                 color=None):
        # Live video defaults to the ``latest`` queue policy: if a viewer falls
        # behind, stale frames are dropped so latency stays bounded rather than
        # piling up. Pass ``queue="fifo"`` to instead deliver every frame.
        super().__init__(source=_VIDEO_SOURCE, name=name, label=label, queue=queue)
        self._init_color(color)
        self._quality = int(quality)
        self._encode = bool(encode)

    def update(self, frame):
        """Push one frame to the browser as a binary JPEG WebSocket frame.

        With ``encode=True`` (default) ``frame`` is an OpenCV BGR array, encoded
        to JPEG here. With ``encode=False`` ``frame`` must already be **JPEG
        bytes** — e.g. produced by a hardware encoder (NVJPG/GStreamer) — and is
        sent as-is, skipping ``cv2.imencode`` entirely. Either way the bytes ride
        a binary frame (no base64, no JSON), painted by the panel's ``onFrame``.
        """
        if self._encode:
            try:
                # Imported via importlib so PyInstaller's analysis can't see it
                # and bundle OpenCV into a baked app that never encodes frames;
                # bake() bundles cv2 when a VideoFeed is on the canvas.
                import importlib

                cv2 = importlib.import_module("cv2")
            except ImportError as exc:
                # OpenCV lives in the optional [video] extra so a slider-only
                # install stays lightweight. Only the encode path needs it; pass
                # encode=False to stream pre-encoded JPEG bytes with no OpenCV.
                raise RuntimeError(
                    "VideoFeed(encode=True) needs OpenCV. Install it with "
                    "pip install 'danvas[video]', or pass encode=False to send "
                    "JPEG bytes you've already encoded."
                ) from exc
            ok, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._quality]
            )
            if not ok:
                return
            data = buf.tobytes()
        else:
            # Pre-encoded path: accept bytes/bytearray/memoryview (or a numpy
            # buffer of JPEG bytes) without re-encoding.
            if isinstance(frame, (bytes, bytearray, memoryview)):
                data = bytes(frame)
            else:
                data = bytes(frame)  # e.g. a numpy uint8 buffer of JPEG bytes
        if not data:
            return
        self.push_binary(data)