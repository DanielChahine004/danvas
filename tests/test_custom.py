import pycanvas


class FakeBridge:
    def __init__(self):
        self.plain = []

    def broadcast(self, msg, exclude=None):
        self.plain.append(msg)

    def broadcast_binary(self, data):
        pass


def _panel():
    p = pycanvas.Custom(html="<div></div>", name="p")
    p._bind("c1", FakeBridge())
    return p


def test_on_routes_by_event_field():
    panel = _panel()
    seen = []
    panel.on("rotate")(lambda msg: seen.append(("rot", msg["deg"])))
    panel.on("reset")(lambda msg: seen.append(("rst", None)))

    panel._handle_input({"event": "rotate", "deg": 42})
    panel._handle_input({"event": "reset"})
    panel._handle_input({"event": "unhandled"})  # no handler -> ignored

    assert seen == [("rot", 42), ("rst", None)]


def test_on_message_is_catch_all_and_fires_alongside_keyed():
    panel = _panel()
    every, keyed = [], []
    panel.on_message(lambda msg: every.append(msg.get("event")))
    panel.on("rotate")(lambda msg: keyed.append(msg["deg"]))

    panel._handle_input({"event": "rotate", "deg": 7})
    panel._handle_input({"event": "other"})

    assert keyed == [7]                 # keyed handler only for its event
    assert every == ["rotate", "other"] # catch-all sees both


def test_custom_event_key_is_configurable():
    panel = pycanvas.Custom(html="", name="p", event_key="type")
    panel._bind("c1", FakeBridge())
    seen = []
    panel.on("ping")(lambda msg: seen.append(msg))
    panel._handle_input({"type": "ping", "n": 1})
    assert seen == [{"type": "ping", "n": 1}]


def test_onpush_helper_is_injected_into_html():
    panel = _panel()
    html = panel.register_props()["html"]
    # The symmetric helper exposes both directions in the iframe.
    assert "canvas.send" not in html or "send:function" in html
    assert "onPush:function" in html
    assert "send:function" in html


def test_push_streams_without_reload():
    panel = _panel()
    panel.push({"deg": 90})
    assert panel._bridge.plain[-1]["payload"] == {"post": {"deg": 90}}
