"""End-to-end WebSocket protocol test using FastAPI's TestClient."""

import time

from fastapi.testclient import TestClient

import pycanvas
from pycanvas import server


def build_client():
    canvas = pycanvas.Canvas()
    slider = canvas.insert(pycanvas.Slider(label="servo", min=0, max=180, default=90))
    label = canvas.insert(pycanvas.Label(label="status", value="idle"))
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
