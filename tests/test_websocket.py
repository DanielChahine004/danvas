"""End-to-end WebSocket protocol test using FastAPI's TestClient."""

import threading
import time

from fastapi.testclient import TestClient

import pycanvas
from pycanvas import server


def build_client():
    canvas = pycanvas.Canvas()
    slider = canvas.insert(pycanvas.Slider("servo", min=0, max=180, default=90))
    label = canvas.insert(pycanvas.Label("status", value="idle"))
    app = server.create_app(canvas._bridge, open_browser=False)
    return canvas, slider, label, app


_NON_PROTOCOL = {"presence", "welcome", "chat"}


def _recv(ws):
    """Receive the next component-protocol message.

    On connect the server also sends identity/presence/chat traffic (``welcome``,
    ``presence`` roster, chat history) which interleaves with the register/update
    replay. These tests don't care about it, so skip it and return the next real
    message.
    """
    while True:
        msg = ws.receive_json()
        if msg.get("type") not in _NON_PROTOCOL:
            return msg


def test_register_messages_sent_on_connect():
    canvas, slider, label, app = build_client()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            msgs = [_recv(ws) for _ in range(4)]

    types = [(m["type"], m.get("component")) for m in msgs]
    # Each component yields a register followed by an initial-state update.
    assert ("register", "Slider") in types
    assert ("register", "Label") in types
    regs = {m["id"]: m for m in msgs if m["type"] == "register"}
    assert regs[slider.id]["props"]["max"] == 180


def test_input_message_updates_python_value():
    canvas, slider, label, app = build_client()
    fired = []
    slider.on_change(lambda v: fired.append(v))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            # drain initial register/update traffic
            for _ in range(4):
                _recv(ws)
            ws.send_json({"type": "input", "id": slider.id, "payload": {"value": 150}})
            # give the server loop a moment to route the message
            for _ in range(50):
                if slider.value == 150:
                    break
                time.sleep(0.02)

    assert slider.value == 150
    assert fired == [150]


def test_rapid_inputs_processed_in_order():
    """A burst of inputs (a slider drag) settles on the last value, in order.

    Inputs run on the bridge's single FIFO dispatch thread, so even a slow
    callback can't reorder them or leave the value on a stale frame.
    """
    canvas, slider, label, app = build_client()
    fired = []
    slider.on_change(lambda v: fired.append(v))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(4):
                _recv(ws)
            for v in (10, 20, 30, 40, 50):
                ws.send_json({"type": "input", "id": slider.id,
                              "payload": {"value": v}})
            for _ in range(50):
                if slider.value == 50:
                    break
                time.sleep(0.02)

    assert slider.value == 50
    assert fired == [10, 20, 30, 40, 50]


def test_layout_message_syncs_python_geometry():
    canvas, slider, label, app = build_client()
    seen = []
    slider.on_layout(lambda c: seen.append((c.x, c.y, c.w, c.h)))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(4):
                _recv(ws)
            # User dragged/resized the panel in the browser.
            ws.send_json(
                {"type": "layout", "id": slider.id,
                 "x": 300, "y": 150, "w": 320, "h": 120, "rotation": 0}
            )
            for _ in range(50):
                if slider.x == 300:
                    break
                time.sleep(0.02)

    assert (slider.x, slider.y, slider.w, slider.h) == (300, 150, 320, 120)
    assert seen and seen[-1] == (300, 150, 320, 120)


def test_snapshot_request_response():
    canvas, slider, label, app = build_client()
    fake_doc = {"document": {"store": {}}, "session": {}}

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(4):
                _recv(ws)

            # request_snapshot blocks the caller, so run it off-thread and
            # answer the get_snapshot request from this (the client) thread.
            result = {}
            t = threading.Thread(
                target=lambda: result.update(
                    data=canvas._bridge.request_snapshot(timeout=3)
                )
            )
            t.start()

            req = _recv(ws)
            assert req["type"] == "get_snapshot" and "reqId" in req
            ws.send_json({"type": "snapshot", "reqId": req["reqId"], "data": fake_doc})
            t.join(timeout=3)

    assert result["data"] == fake_doc


def _wait_presence(ws, limit=12):
    """Return the count from the next presence message (skipping other traffic)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == "presence":
            return msg["count"]
    raise AssertionError("no presence message arrived")


def _welcome(ws, limit=12):
    """Return the identity from the server's welcome message."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == "welcome":
            return msg["you"]
    raise AssertionError("no welcome message arrived")


def _wait_type(ws, kind, limit=20):
    """Return the next message of the given type (skipping others)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == kind:
            return msg
    raise AssertionError(f"no {kind} message arrived")


def test_chat_relay_carries_sender_identity():
    canvas, slider, label, app = build_client()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws1:
            me1 = _welcome(ws1)
            with client.websocket_connect("/ws") as ws2:
                me2 = _welcome(ws2)
                assert me1["id"] != me2["id"]  # distinct identities
                ws1.send_json({"type": "chat", "text": "hello"})
                got = _wait_type(ws2, "chat")
                # The server stamps identity from its own record, not the client.
                assert got["text"] == "hello"
                assert got["id"] == me1["id"]
                assert got["name"] == me1["name"]


def test_set_name_updates_roster():
    canvas, slider, label, app = build_client()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            _welcome(ws)
            ws.send_json({"type": "set_name", "name": "  Captain  "})
            for _ in range(20):
                msg = ws.receive_json()
                if msg.get("type") == "presence" and any(
                    v["name"] == "Captain" for v in msg.get("viewers", [])
                ):
                    break  # name applied and trimmed in the roster
            else:
                raise AssertionError("rename not reflected in the roster")


def test_chat_history_replayed_to_late_joiner():
    canvas, slider, label, app = build_client()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws1:
            _welcome(ws1)
            ws1.send_json({"type": "chat", "text": "first"})
            _wait_type(ws1, "chat")  # let the server record it
            with client.websocket_connect("/ws") as ws2:
                # A fresh viewer replays the conversation so far.
                assert _wait_type(ws2, "chat")["text"] == "first"


def test_post_chat_from_python():
    canvas, slider, label, app = build_client()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            _welcome(ws)
            canvas._bridge.post_chat("server says hi", name="host")
            got = _wait_type(ws, "chat")
            assert got["text"] == "server says hi" and got["name"] == "host"


def test_presence_count_broadcast():
    canvas, slider, label, app = build_client()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws1:
            # The first viewer is told the count is 1.
            assert _wait_presence(ws1) == 1
            with client.websocket_connect("/ws") as ws2:
                # A second viewer joining bumps the count broadcast to everyone.
                assert _wait_presence(ws1) == 2
                assert _wait_presence(ws2) == 2
            # ws2 left the with-block; ws1 should see the count fall back to 1.
            assert _wait_presence(ws1) == 1
