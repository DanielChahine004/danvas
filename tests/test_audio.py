import numpy as np

import danvas
from danvas.bridge import BINARY_REACT


class FakeBridge:
    def __init__(self):
        self.sent = []
        self.binary = []

    def broadcast(self, msg, **_kw):
        self.sent.append(msg)

    def broadcast_binary(self, data, **_kw):
        self.binary.append(data)

    def broadcast_conflated(self, key, data, **_kw):
        self.binary.append(data)


def test_audio_update_sends_binary_pcm_frame():
    bridge = FakeBridge()
    feed = danvas.AudioFeed("mic", sample_rate=16000)
    feed._bind("a1", bridge)

    samples = (np.sin(np.linspace(0, 6.28, 256)) * 30000).astype("<i2")
    feed.update(samples)

    # No JSON update; the PCM rides one binary frame.
    assert bridge.sent == []
    assert len(bridge.binary) == 1
    data = bridge.binary[0]

    # Header: [type][idLen][id bytes], then the raw int16 PCM, byte-exact.
    assert data[0] == BINARY_REACT
    id_len = data[1]
    assert data[2:2 + id_len] == b"a1"
    payload = data[2 + id_len:]
    assert np.array_equal(np.frombuffer(payload, "<i2"), samples)


def test_audio_empty_chunk_sends_nothing():
    bridge = FakeBridge()
    feed = danvas.AudioFeed("mic")
    feed._bind("a1", bridge)
    feed.update(np.zeros(0, dtype="<i2"))
    assert bridge.binary == [] and bridge.sent == []
