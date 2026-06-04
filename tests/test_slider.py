import pycanvas


class FakeBridge:
    def __init__(self):
        self.sent = []

    def broadcast(self, msg):
        self.sent.append(msg)


def test_slider_register_props():
    s = pycanvas.Slider(label="servo", min=0, max=180, default=90)
    s._bind("abc", FakeBridge())
    props = s.register_props()
    # Size defaults are always present so comp.w/h read real numbers.
    assert props == {
        "label": "servo",
        "min": 0,
        "max": 180,
        "value": 90,
        "w": 240,
        "h": 96,
    }
    assert s.value == 90


def test_slider_input_updates_value_and_fires_callback():
    s = pycanvas.Slider(label="servo", min=0, max=180)
    s._bind("abc", FakeBridge())

    seen = []
    s.on_change(lambda v: seen.append(v))

    s._handle_input({"value": 120})
    assert s.value == 120
    assert seen == [120]


def test_slider_update_broadcasts():
    bridge = FakeBridge()
    s = pycanvas.Slider(label="servo", min=0, max=180, default=10)
    s._bind("abc", bridge)

    s.update(55)
    assert s.value == 55
    assert bridge.sent == [
        {"type": "update", "id": "abc", "payload": {"value": 55}}
    ]
