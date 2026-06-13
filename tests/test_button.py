import pycanvas


class FakeBridge:
    def __init__(self):
        self.sent = []

    def broadcast(self, msg, exclude=None):
        self.sent.append(msg)

    def broadcast_binary(self, data):
        pass


def test_on_click_fires_with_no_args_and_counts():
    btn = pycanvas.Button("go", text="Run")
    btn._bind("b1", FakeBridge())
    calls = []
    btn.on_click(lambda: calls.append(1))

    btn._handle_input({"click": True})
    btn._handle_input({"click": True})

    assert calls == [1, 1]
    assert btn.value == 2  # running click count


def test_text_defaults_to_name_and_is_a_shape_prop():
    btn = pycanvas.Button("save")
    assert btn.register_props()["text"] == "save"
    btn2 = pycanvas.Button("x", text="Save now")
    assert btn2.register_props()["text"] == "Save now"


def test_update_changes_the_face_text_live_and_persists():
    btn = pycanvas.Button("toggle", text="Start")
    bridge = FakeBridge()
    btn._bind("b1", bridge)

    btn.update("Pause")

    # Pushed to the browser as a `text` prop update...
    assert bridge.sent[-1] == {"type": "update", "id": "b1",
                               "payload": {"text": "Pause"}}
    # ...and stored, so a reconnecting client replays the current face.
    assert btn.register_props()["text"] == "Pause"


def test_button_factory_inserts_and_places():
    canvas = pycanvas.Canvas()
    btn = canvas.button("go", text="Start", x=10, y=20)
    assert btn.component == "Button"
    assert btn.x == 10 and btn.y == 20
