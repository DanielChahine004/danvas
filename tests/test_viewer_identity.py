"""Every interaction callback can opt into the uploader/actor's viewer identity.

Declaring a trailing ``viewer`` parameter on a handler makes the component pass
the actor's roster dict ({id, name, color, role}); one-arg handlers are untouched.
These cover the input, request, and layout paths threaded through the bridge.
"""

import pycanvas
from pycanvas.bridge import Bridge

VIEWER = {"id": "v1", "name": "Fox", "color": "#ef4444", "role": "admin"}


def _bridge_with_viewer():
    bridge = Bridge()
    bridge._viewers["WS"] = dict(VIEWER)   # stand in for a live connection
    return bridge


def test_on_change_receives_viewer():
    bridge = _bridge_with_viewer()
    s = pycanvas.Slider("s")
    s._bind("s1", bridge)
    seen = {}
    s.on_change(lambda value, viewer: seen.update(viewer))

    bridge._dispatch_input(s, {"value": 42}, "WS")

    assert seen["name"] == "Fox" and seen["role"] == "admin"


def test_on_change_without_viewer_still_works():
    bridge = _bridge_with_viewer()
    s = pycanvas.Slider("s")
    s._bind("s1", bridge)
    got = []
    s.on_change(lambda value: got.append(value))

    bridge._dispatch_input(s, {"value": 7}, "WS")

    assert got == [7]


def test_on_layout_receives_viewer():
    bridge = _bridge_with_viewer()
    s = pycanvas.Slider("s")
    s._bind("s1", bridge)
    seen = {}
    s.on_layout(lambda comp, viewer: seen.update(viewer))

    bridge._dispatch_layout(s, {"x": 10, "y": 20}, "WS")

    assert seen["name"] == "Fox"
    assert s.x == 10 and s.y == 20   # geometry still applied


def test_on_layout_without_viewer_still_works():
    bridge = _bridge_with_viewer()
    s = pycanvas.Slider("s")
    s._bind("s1", bridge)
    moved = []
    s.on_layout(lambda comp: moved.append(comp))

    bridge._dispatch_layout(s, {"x": 1, "y": 2}, "WS")

    assert moved == [s]


def test_on_request_receives_viewer():
    bridge = _bridge_with_viewer()
    panel = pycanvas.React("function Component(){ return null; }", name="r")
    panel._bind("r1", bridge)
    got = {}

    @panel.on_request("ping")
    def _(req, viewer):
        got.update(viewer)
        return {"ok": True}

    bridge._dispatch_request(panel, "req1", {"event": "ping"}, "WS")

    assert got["name"] == "Fox" and got["role"] == "admin"


def test_on_request_without_viewer_still_works():
    bridge = _bridge_with_viewer()
    panel = pycanvas.React("function Component(){ return null; }", name="r")
    panel._bind("r1", bridge)

    @panel.on_request("double")
    def _(req):
        return {"n": req["n"] * 2}

    # No exception, and the reply is broadcast (loop is None, so it no-ops).
    bridge._dispatch_request(panel, "req2", {"event": "double", "n": 21}, "WS")


def test_unknown_socket_yields_empty_viewer_not_crash():
    bridge = Bridge()   # no viewers registered
    s = pycanvas.Slider("s")
    s._bind("s1", bridge)
    seen = []
    s.on_change(lambda value, viewer: seen.append(viewer))

    bridge._dispatch_input(s, {"value": 1}, "GHOST")

    assert seen == [{}]
