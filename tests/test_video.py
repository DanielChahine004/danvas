import numpy as np

import pycanvas
from pycanvas.bridge import BINARY_VIDEO


class FakeBridge:
    def __init__(self):
        self.sent = []
        self.binary = []

    def broadcast(self, msg):
        self.sent.append(msg)

    def broadcast_binary(self, data):
        self.binary.append(data)


def test_video_update_sends_binary_jpeg_frame():
    bridge = FakeBridge()
    feed = pycanvas.VideoFeed("cam")
    feed._bind("v1", bridge)

    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:, :, 1] = 255  # green
    feed.update(frame)

    # No JSON update is sent; the frame rides a single binary frame instead.
    assert bridge.sent == []
    assert len(bridge.binary) == 1
    data = bridge.binary[0]

    # Header: [type][idLen][id bytes], then the raw JPEG payload.
    assert data[0] == BINARY_VIDEO
    id_len = data[1]
    assert data[2:2 + id_len] == b"v1"
    payload = data[2 + id_len:]
    assert payload[:2] == b"\xff\xd8"  # JPEG SOI marker — real encoded bytes
