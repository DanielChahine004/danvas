"""Viewer cursor reporting: welcome flag + roster update, gated by the toggle."""

import time

from fastapi.testclient import TestClient

import pycanvas
from pycanvas import server


def _build(cursors):
    canvas = pycanvas.Canvas()
    canvas.label("status", value="idle")
    canvas._bridge._cursors = cursors          # serve() sets this; do it directly here
    app = server.create_app(canvas._bridge, open_browser=False)
    return canvas, app


def _welcome(ws):
    while True:
        msg = ws.receive_json()
        if msg.get("type") == "welcome":
            return msg


def _wait_cursor(canvas, timeout=2.0):
    """Poll the roster until the (async) cursor write lands, or give up."""
    end = time.time() + timeout
    while time.time() < end:
        vs = canvas.viewers
        if vs and vs[0].get("cursor"):
            return vs[0]["cursor"]
        time.sleep(0.01)
    return None


def test_welcome_advertises_cursor_flag():
    canvas, app = _build(cursors=True)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert _welcome(ws)["cursors"] is True

    canvas, app = _build(cursors=False)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert _welcome(ws)["cursors"] is False


def test_cursor_message_updates_viewer_when_enabled():
    canvas, app = _build(cursors=True)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            _welcome(ws)
            # Fresh viewer starts with no cursor.
            assert canvas.viewers[0]["cursor"] is None
            ws.send_json({"type": "cursor", "x": 120.5, "y": 64.0})
            assert _wait_cursor(canvas) == {"x": 120.5, "y": 64.0}


def test_cursor_ignored_when_disabled():
    canvas, app = _build(cursors=False)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            _welcome(ws)
            ws.send_json({"type": "cursor", "x": 1.0, "y": 2.0})
            # Feature off -> the position is dropped, never stored.
            assert _wait_cursor(canvas, timeout=0.3) is None
            assert canvas.viewers[0]["cursor"] is None


def _next_of_type(ws, wanted, timeout_msgs=50):
    for _ in range(timeout_msgs):
        msg = ws.receive_json()
        if msg.get("type") == wanted:
            return msg
    return None


def test_cursor_relayed_to_other_viewers():
    canvas, app = _build(cursors=True)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as a, \
                client.websocket_connect("/ws") as b:
            _welcome(a)
            _welcome(b)
            # a moves; b should receive a's cursor tagged with a's identity.
            a.send_json({"type": "cursor", "x": 50.0, "y": 70.0})
            relayed = _next_of_type(b, "cursor")
            assert relayed is not None
            assert relayed["x"] == 50.0 and relayed["y"] == 70.0
            assert relayed["id"] == canvas.viewers[0]["id"]
            assert "color" in relayed and "name" in relayed


def test_on_cursor_tap_fires():
    canvas, app = _build(cursors=True)
    seen = []
    canvas.on_cursor(lambda v: seen.append(v))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            _welcome(ws)
            ws.send_json({"type": "cursor", "x": 9.0, "y": 8.0})
            end = time.time() + 2.0
            while not seen and time.time() < end:
                time.sleep(0.01)
    assert seen and seen[0]["cursor"] == {"x": 9.0, "y": 8.0}
    assert "id" in seen[0] and "color" in seen[0]
