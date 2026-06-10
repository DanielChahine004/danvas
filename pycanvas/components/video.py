"""VideoFeed: streams OpenCV frames to the browser as binary JPEG."""

import cv2

from .base import BaseComponent
from ..bridge import BINARY_VIDEO


class VideoFeed(BaseComponent):
    component = "VideoFeed"
    default_w = 340
    default_h = 280

    def __init__(self, name, quality=70, label=None, encode=True, queue="latest"):
        # Live video defaults to the ``latest`` queue policy: if a viewer falls
        # behind, stale frames are dropped so latency stays bounded rather than
        # piling up. Pass ``queue="fifo"`` to instead deliver every frame.
        super().__init__(name=name, label=label, queue=queue)
        self._quality = int(quality)
        self._encode = bool(encode)

    def update(self, frame):
        """Push one frame to the browser as a binary JPEG WebSocket frame.

        With ``encode=True`` (default) ``frame`` is an OpenCV BGR array, encoded
        to JPEG here. With ``encode=False`` ``frame`` must already be **JPEG
        bytes** — e.g. produced by a hardware encoder (NVJPG/GStreamer) — and is
        sent as-is, skipping ``cv2.imencode`` entirely. Either way the bytes ride
        a binary frame (no base64, no JSON), fed straight into a Blob.
        """
        if self._encode:
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
        self._send_binary(BINARY_VIDEO, data)
