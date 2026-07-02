import danvas
from danvas.bridge import BINARY_CUSTOM


class FakeBridge:
    def __init__(self):
        self.binary = []      # data via broadcast_binary (fifo)
        self.conflated = []   # (comp_id, data) via broadcast_conflated (latest)

    def broadcast(self, msg, **_kw):
        pass

    def broadcast_binary(self, data, **_kw):
        self.binary.append(data)

    def broadcast_conflated(self, comp_id, *, msg=None, data=None, exclude=None, **_kw):
        self.conflated.append((comp_id, data))


def test_push_binary_sends_binary_frame():
    bridge = FakeBridge()
    panel = danvas.Custom(html="<div></div>", name="cam")
    panel._bind("c1", bridge)
    panel.push_binary(b"\x00\x01\x02raw-bytes")

    # Custom defaults to the fifo queue -> plain binary broadcast.
    assert bridge.conflated == []
    assert len(bridge.binary) == 1
    data = bridge.binary[0]
    # Header: [type][idLen][id bytes], then the raw payload, unencoded.
    assert data[0] == BINARY_CUSTOM
    id_len = data[1]
    assert data[2:2 + id_len] == b"c1"
    assert data[2 + id_len:] == b"\x00\x01\x02raw-bytes"


def test_push_binary_accepts_bytearray_and_memoryview():
    bridge = FakeBridge()
    panel = danvas.Custom(html="<div></div>", name="cam")
    panel._bind("c1", bridge)
    panel.push_binary(bytearray(b"abc"))
    panel.push_binary(memoryview(b"def"))
    payloads = [d[2 + d[1]:] for d in bridge.binary]
    assert payloads == [b"abc", b"def"]


def test_push_binary_latest_queue_conflates():
    bridge = FakeBridge()
    panel = danvas.Custom(html="<div></div>", name="cam")
    panel.queue = "latest"   # like VideoFeed: drop stale buffers for a slow viewer
    panel._bind("c1", bridge)
    panel.push_binary(b"frame")

    assert bridge.binary == []
    assert len(bridge.conflated) == 1
    comp_id, data = bridge.conflated[0]
    assert comp_id == "c1"
    assert data[0] == BINARY_CUSTOM
