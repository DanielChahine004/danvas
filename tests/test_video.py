import numpy as np

import pycanvas


class FakeBridge:
    def __init__(self):
        self.sent = []

    def broadcast(self, msg):
        self.sent.append(msg)


def test_video_update_encodes_base64_jpeg():
    bridge = FakeBridge()
    feed = pycanvas.VideoFeed(label="cam")
    feed._bind("v1", bridge)

    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:, :, 1] = 255  # green
    feed.update(frame)

    assert len(bridge.sent) == 1
    msg = bridge.sent[0]
    assert msg["type"] == "update"
    assert msg["id"] == "v1"
    assert msg["payload"]["src"].startswith("data:image/jpeg;base64,")
