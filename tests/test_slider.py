import json

import pycanvas


class FakeBridge:
    def __init__(self):
        self.sent = []

    def broadcast(self, msg):
        self.sent.append(msg)


def test_slider_register_props():
    s = pycanvas.Slider("servo", min=0, max=180, default=90)
    s._bind("abc", FakeBridge())
    props = s.register_props()
    # Slider is now a native React panel: its config rides in the JSON `data`
    # prop (replayed on reconnect), not as top-level shape props.
    assert props["label"] == "servo"
    assert props["w"] == 240 and props["h"] == 96
    data = json.loads(props["data"])
    assert data == {
        "min": 0, "max": 180, "step": 1, "default": 90,
        "value": 90, "on_release": False,
    }
    assert s.value == 90


def test_slider_float_step_in_props():
    s = pycanvas.Slider("gain", min=0, max=1, default=0.5, step=0.1)
    s._bind("abc", FakeBridge())
    data = json.loads(s.register_props()["data"])
    assert data["step"] == 0.1
    assert data["value"] == 0.5


def test_slider_input_updates_value_and_fires_callback():
    s = pycanvas.Slider("servo", min=0, max=180)
    s._bind("abc", FakeBridge())

    seen = []
    s.on_change(lambda v: seen.append(v))

    s._handle_input({"value": 120})
    assert s.value == 120
    assert seen == [120]


def test_slider_update_broadcasts():
    bridge = FakeBridge()
    s = pycanvas.Slider("servo", min=0, max=180, default=10)
    s._bind("abc", bridge)

    s.update(55)
    assert s.value == 55
    # Streams in over the push channel (the React `value` prop), not a shape prop.
    assert bridge.sent == [
        {"type": "update", "id": "abc", "payload": {"post": 55}}
    ]
