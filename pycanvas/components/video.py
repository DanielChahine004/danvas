"""VideoFeed: streams OpenCV frames to the browser as base64 JPEG."""

import base64

import cv2

from .base import BaseComponent


class VideoFeed(BaseComponent):
    component = "VideoFeed"
    default_w = 340
    default_h = 280

    def __init__(self, label, quality=70):
        super().__init__(label=label)
        self._quality = int(quality)

    def update(self, frame):
        """Encode an OpenCV BGR frame to JPEG and push it to the browser."""
        ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._quality]
        )
        if not ok:
            return
        b64 = base64.b64encode(buf).decode("ascii")
        self._send_update({"src": f"data:image/jpeg;base64,{b64}"})
