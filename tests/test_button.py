import json

import danvas


class FakeBridge:
    def __init__(self):
        self.sent = []

    def broadcast(self, msg, exclude=None, **_kw):
        self.sent.append(msg)

    def broadcast_binary(self, data, **_kw):
        pass


def test_on_click_fires_with_no_args_and_counts():
    btn = danvas.Button("go", text="Run")
    btn._bind("b1", FakeBridge())
    calls = []
    btn.on_click(lambda: calls.append(1))

    btn._handle_input({"click": True})
    btn._handle_input({"click": True})

    assert calls == [1, 1]
    assert btn.value == 2  # running click count


def _face(btn):
    """The button's face text, read out of the React `data` JSON prop."""
    return json.loads(btn.register_props()["data"])["text"]


def test_text_defaults_to_name_and_is_a_shape_prop():
    btn = danvas.Button("save")
    assert _face(btn) == "save"
    btn2 = danvas.Button("x", text="Save now")
    assert _face(btn2) == "Save now"


def test_update_changes_the_face_text_live_and_persists():
    btn = danvas.Button("toggle", text="Start")
    bridge = FakeBridge()
    btn._bind("b1", bridge)

    btn.update("Pause")

    # Pushed to the browser as a React props delta (`data_patch`, only the changed
    # key) — the frontend merges it into the panel's current props...
    assert bridge.sent[-1]["type"] == "update"
    assert bridge.sent[-1]["id"] == "b1"
    assert bridge.sent[-1]["payload"]["data_patch"]["text"] == "Pause"
    # ...and stored, so a reconnecting client replays the current face.
    assert _face(btn) == "Pause"


def test_button_factory_inserts_and_places():
    canvas = danvas.Canvas()
    btn = canvas.button("go", text="Start", x=10, y=20)
    # Button is now a native React panel (mounted by ReactHost).
    assert btn.component == "React"
    assert btn.x == 10 and btn.y == 20
