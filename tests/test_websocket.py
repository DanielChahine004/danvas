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


def test_register_messages_sent_on_connect():
    canvas, slider, label, app = build_client()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            msgs = [ws.receive_json() for _ in range(4)]

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
                ws.receive_json()
            ws.send_json({"type": "input", "id": slider.id, "payload": {"value": 150}})
            # give the server loop a moment to route the message
            for _ in range(50):
                if slider.value == 150:
                    break
                time.sleep(0.02)

    assert slider.value == 150
    assert fired == [150]


def test_layout_message_syncs_python_geometry():
    canvas, slider, label, app = build_client()
    seen = []
    slider.on_layout(lambda c: seen.append((c.x, c.y, c.w, c.h)))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(4):
                ws.receive_json()
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
                ws.receive_json()

            # request_snapshot blocks the caller, so run it off-thread and
            # answer the get_snapshot request from this (the client) thread.
            result = {}
            t = threading.Thread(
                target=lambda: result.update(
                    data=canvas._bridge.request_snapshot(timeout=3)
                )
            )
            t.start()

            req = ws.receive_json()
            assert req["type"] == "get_snapshot" and "reqId" in req
            ws.send_json({"type": "snapshot", "reqId": req["reqId"], "data": fake_doc})
            t.join(timeout=3)

    assert result["data"] == fake_doc
