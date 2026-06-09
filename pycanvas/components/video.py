"""VideoFeed: streams OpenCV frames to the browser as binary JPEG."""

import cv2

from .base import BaseComponent
from ..bridge import BINARY_VIDEO


class VideoFeed(BaseComponent):
    component = "VideoFeed"
    default_w = 340
    default_h = 280

    def __init__(self, name, quality=70, label=None):
        super().__init__(name=name, label=label)
        self._quality = int(quality)

    def update(self, frame):
        """Encode an OpenCV BGR frame to JPEG and push it to the browser.

        The JPEG bytes ride a binary WebSocket frame (no base64, no JSON), so the
        browser feeds them straight into a Blob — markedly less CPU and ~33% less
        bytes on the wire than a base64 data-URL at the same quality.
        """
        ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._quality]
        )
        if not ok:
            return
        self._send_binary(BINARY_VIDEO, buf.tobytes())
