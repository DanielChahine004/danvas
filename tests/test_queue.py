import pytest

import pycanvas
from pycanvas.bridge import Bridge


class FakeBridge:
    def __init__(self):
        self.plain = []       # msgs via broadcast (fifo)
        self.conflated = []   # (comp_id, msg, data) via broadcast_conflated

    def broadcast(self, msg, exclude=None):
        self.plain.append(msg)

    def broadcast_binary(self, data):
        pass

    def broadcast_conflated(self, comp_id, *, msg=None, data=None, exclude=None):
        self.conflated.append((comp_id, msg, data))


def test_default_is_fifo_plain_broadcast():
    bridge = FakeBridge()
    label = pycanvas.Label("status")
    label._bind("l1", bridge)
    label.update("hello")
    assert bridge.conflated == []
    assert bridge.plain and bridge.plain[0]["payload"] == {"value": "hello"}


def test_latest_routes_dict_updates_to_conflated():
    bridge = FakeBridge()
    label = pycanvas.Label("status")
    label.queue = "latest"  # universal property, no constructor arg needed
    label._bind("l1", bridge)
    label.update("hello")
    assert bridge.plain == []
    comp_id, msg, data = bridge.conflated[0]
    assert comp_id == "l1" and msg["payload"] == {"value": "hello"} and data is None


def test_invalid_queue_rejected():
    # Via the constructor (VideoFeed forwards to the base) and via the property.
    with pytest.raises(ValueError):
        pycanvas.VideoFeed("x", queue="newest")
    with pytest.raises(ValueError):
        pycanvas.Label("x").queue = "newest"


def test_conflation_drops_stale_frames_under_backpressure():
    # With a blocked (slow) socket, many rapidly-pushed frames must collapse to
    # the newest rather than queueing — that's the latency-bounding guarantee.
    import asyncio

    async def scenario():
        bridge = Bridge()
        bridge._loop = asyncio.get_running_loop()
        sent = []
        release = asyncio.Event()

        class FakeWS:
            async def send_bytes(self, data):
                await release.wait()   # socket is "slow": blocks until released
                sent.append(bytes(data))

            async def send_text(self, _):
                pass

        ws = FakeWS()
        bridge._connections.add(ws)
        for i in range(5):
            bridge.broadcast_conflated("cam", data=b"\x01\x03cam" + bytes([i]))
            await asyncio.sleep(0.005)  # let the sender start and block
        release.set()
        await asyncio.sleep(0.05)       # let it drain
        return sent

    sent = asyncio.run(scenario())
    assert len(sent) <= 2               # at most one in-flight + the latest
    assert sent[-1].endswith(b"\x04")   # and the newest frame is what arrives


def test_merge_update_keeps_newest_per_key():
    # A pending partial update must not be lost when a later one touches other
    # keys: x from the first survives alongside w from the second.
    first = Bridge._merge_update(None, {"type": "update", "id": "p",
                                        "payload": {"x": 1}})
    merged = Bridge._merge_update(first, {"type": "update", "id": "p",
                                          "payload": {"w": 5}})
    assert merged["payload"] == {"x": 1, "w": 5}
    # A newer value for the same key wins.
    merged = Bridge._merge_update(merged, {"type": "update", "id": "p",
                                           "payload": {"x": 9}})
    assert merged["payload"]["x"] == 9
