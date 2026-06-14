import json
import struct

import pycanvas
from pycanvas.bridge import BINARY_REACT


class FakeBridge:
    def __init__(self):
        self.plain = []
        self.binary = []

    def broadcast(self, msg, exclude=None):
        self.plain.append(msg)

    def broadcast_binary(self, data):
        self.binary.append(data)


def _panel(**kw):
    p = pycanvas.React(source="function Component(){ return null }", name="p", **kw)
    p._bind("c1", FakeBridge())
    return p


def test_register_props_carries_source_and_json_props():
    panel = _panel(props={"label": "Taps"})
    props = panel.register_props()
    assert "function Component" in props["source"]
    assert json.loads(props["data"]) == {"label": "Taps"}


def test_update_merges_props_and_sends_json():
    panel = _panel(props={"label": "Taps"})
    panel.update(label="Hits", extra=1)
    # Merge semantics: untouched keys survive.
    assert json.loads(panel.register_props()["data"]) == {"label": "Hits", "extra": 1}
    sent = json.loads(panel._bridge.plain[-1]["payload"]["data"])
    assert sent == {"label": "Hits", "extra": 1}


def test_push_streams_without_prop_churn():
    panel = _panel()
    panel.push({"t": 90})
    assert panel._bridge.plain[-1]["payload"] == {"post": {"t": 90}}


def test_push_binary_sends_binary_frame_with_react_type():
    panel = _panel()  # React defaults to the fifo queue -> plain binary broadcast
    payload = struct.pack("<2f", 0.5, -0.25)
    panel.push_binary(payload)

    assert panel._bridge.plain == []  # no JSON update for a binary push
    assert len(panel._bridge.binary) == 1
    data = panel._bridge.binary[0]
    # Header: [type][idLen][id bytes], then the raw payload, unencoded.
    assert data[0] == BINARY_REACT
    id_len = data[1]
    assert data[2:2 + id_len] == b"c1"
    assert data[2 + id_len:] == payload


def test_auto_width_setter_toggles_and_pins():
    panel = _panel()
    assert panel.register_props()["autoW"] is False
    # comp.w = "auto" turns on content-fit width and tells the frontend.
    panel.w = "auto"
    assert panel._auto_w is True
    assert panel.register_props()["autoW"] is True
    assert panel._bridge.plain[-1]["payload"] == {"autoW": True}
    # A numeric width leaves auto-width mode (so the fit can't override it).
    panel.w = 320
    assert panel._auto_w is False
    assert {"autoW": False} in [m["payload"] for m in panel._bridge.plain]


def test_on_routes_by_event_field_and_catch_all():
    panel = _panel()
    keyed, every = [], []
    panel.on("ping")(lambda m: keyed.append(m["n"]))
    panel.on_message(lambda m: every.append(m.get("event")))

    panel._handle_input({"event": "ping", "n": 3})
    panel._handle_input({"event": "other"})

    assert keyed == [3]
    assert every == ["ping", "other"]
    assert panel.value == {"event": "other"}  # .value is the last message


def test_set_source_replaces_source_live():
    panel = _panel()
    panel.set_source("function Component(){ return 1 }")
    assert panel._bridge.plain[-1]["payload"] == {"source": "function Component(){ return 1 }"}


def test_event_key_is_configurable():
    panel = _panel(event_key="type")
    seen = []
    panel.on("go")(lambda m: seen.append(m))
    panel._handle_input({"type": "go", "n": 1})
    assert seen == [{"type": "go", "n": 1}]
