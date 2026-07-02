import numpy as np
import pytest

# VideoFeed(encode=True) encodes frames with OpenCV, an optional (~90 MB)
# dependency. Skip the whole module when it's absent rather than failing — CI
# deliberately doesn't install the heavy video extra.
pytest.importorskip("cv2")

import danvas
from danvas.bridge import BINARY_VIDEO


class FakeBridge:
    def __init__(self):
        self.binary = []      # (comp_id, data) via broadcast_binary (fifo)
        self.conflated = []   # (comp_id, data) via broadcast_conflated (latest)

    def broadcast(self, msg, **_kw):
        pass

    def broadcast_binary(self, data, **_kw):
        self.binary.append(data)

    def broadcast_conflated(self, comp_id, *, msg=None, data=None, exclude=None, **_kw):
        self.conflated.append((comp_id, data))


def _frame():
    f = np.zeros((48, 64, 3), dtype=np.uint8)
    f[:, :, 1] = 255  # green
    return f


def test_video_update_sends_binary_jpeg_frame():
    bridge = FakeBridge()
    feed = danvas.VideoFeed("cam")  # defaults to queue="latest"
    feed._bind("v1", bridge)
    feed.update(_frame())

    # Live video defaults to the conflated (latest) path, not plain broadcast.
    assert bridge.binary == []
    assert len(bridge.conflated) == 1
    comp_id, data = bridge.conflated[0]
    assert comp_id == "v1"
    # Header: [type][idLen][id bytes], then the raw JPEG payload.
    assert data[0] == BINARY_VIDEO
    id_len = data[1]
    assert data[2:2 + id_len] == b"v1"
    assert data[2 + id_len:2 + id_len + 2] == b"\xff\xd8"  # JPEG SOI marker


def test_video_fifo_uses_plain_binary_broadcast():
    bridge = FakeBridge()
    feed = danvas.VideoFeed("cam", queue="fifo")
    feed._bind("v1", bridge)
    feed.update(_frame())
    assert bridge.conflated == []
    assert len(bridge.binary) == 1


def test_video_encode_false_sends_bytes_unchanged():
    bridge = FakeBridge()
    feed = danvas.VideoFeed("cam", encode=False, queue="fifo")
    feed._bind("v1", bridge)
    jpeg = b"\xff\xd8\xff\xe0already-encoded\xff\xd9"
    feed.update(jpeg)
    data = bridge.binary[0]
    id_len = data[1]
    assert data[2 + id_len:] == jpeg  # passed straight through, no re-encode
