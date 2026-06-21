"""Device classification + the on_connect hook (adapt-to-mobile)."""

import danvas
from danvas.bridge import Bridge, _device_from_ua


def test_device_from_user_agent():
    assert _device_from_ua(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)") == "mobile"
    assert _device_from_ua(
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) Mobile Safari") == "mobile"
    assert _device_from_ua(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120") == "desktop"
    assert _device_from_ua(None) == "desktop"   # missing header -> desktop
    assert _device_from_ua("") == "desktop"


def test_viewer_dict_carries_device():
    bridge = Bridge()
    v = bridge._make_viewer(role=None, device="mobile")
    assert v["device"] == "mobile"
    # the full, uniform shape every handler sees
    assert set(v) == {"id", "name", "color", "cursor", "device", "role"}


def test_make_viewer_defaults_to_desktop():
    # merge host / callers that don't classify a device get a sane default
    assert Bridge()._make_viewer()["device"] == "desktop"


def test_on_connect_fires_with_viewer():
    canvas = danvas.Canvas()
    seen = []
    canvas.on_connect(lambda v: seen.append(v))
    canvas._bridge._tap_connect({"id": "a1", "device": "mobile", "role": None})
    assert seen == [{"id": "a1", "device": "mobile", "role": None}]


def test_off_connect_removes_the_observer():
    canvas = danvas.Canvas()
    seen = []
    fn = canvas.on_connect(lambda v: seen.append(v))
    canvas.off_connect(fn)
    canvas._bridge._tap_connect({"id": "a1"})
    assert seen == []


def test_on_disconnect_fires_with_viewer():
    canvas = danvas.Canvas()
    left = []
    canvas.on_disconnect(lambda v: left.append(v))
    canvas._bridge._tap_disconnect({"id": "a1", "name": "Fox"})
    assert left == [{"id": "a1", "name": "Fox"}]


def test_off_disconnect_removes_the_observer():
    canvas = danvas.Canvas()
    left = []
    fn = canvas.on_disconnect(lambda v: left.append(v))
    canvas.off_disconnect(fn)
    canvas._bridge._tap_disconnect({"id": "a1"})
    assert left == []
