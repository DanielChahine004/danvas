"""Cross-process identity: the danvas name= follows a panel onto the wire, so
canvas["name"] resolves peers' panels (a RemoteHandle: mirror reads, shared-
plane writes, subscription events) — and one button can run handlers in two
processes at once.
"""

import json
import time

import danvas
from danvas.merge import _MergeHost
from danvas.remote import RemoteCanvas, RemoteHandle


def _canvas():
    c = RemoteCanvas("127.0.0.1:8000", label="rig")
    sent = []
    c._client._send = lambda msg: sent.append(msg)
    return c, sent


def test_register_frame_carries_the_name():
    c = danvas.Canvas()
    s = c.slider("servo", min=0, max=180)
    msg = c._bridge.register_message(s)
    assert msg["name"] == "servo"


def test_foreign_lookup_by_name_returns_a_live_handle():
    c, sent = _canvas()
    # a peer's panel arrives on the mirror stream
    c._client._handle({"type": "register", "id": "abc123", "name": "servo",
                       "component": "React", "props": {"min": 0}, "x": 5})
    h = c["servo"]
    assert isinstance(h, RemoteHandle)
    assert h.id == "abc123" and h.name == "servo"
    assert "servo" in c
    # reads come from the replica (props, then streamed state)
    assert h.min == 0
    c._client._handle({"type": "update", "id": "abc123",
                       "payload": {"value": 7}})
    assert h.value == 7
    # writes ride the shared plane
    h.max = 90
    assert sent[-1] == {"type": "set_props", "id": "abc123",
                        "props": {"max": 90}}
    h.set_layout(x=500)
    assert sent[-1] == {"type": "set_props", "id": "abc123",
                        "props": {"x": 500}}


def test_own_panel_wins_a_name_collision():
    c, sent = _canvas()
    mine = c.slider("servo", min=0, max=10)
    c._client._handle({"type": "register", "id": "foreign", "name": "servo",
                       "component": "React", "props": {}})
    assert c["servo"] is mine                    # native object, not a handle


def test_unknown_name_still_raises_keyerror():
    c, _ = _canvas()
    import pytest
    with pytest.raises(KeyError):
        c["nope"]


def test_handle_event_sugar_subscribes_and_dispatches():
    c, sent = _canvas()
    c._client._handle({"type": "register", "id": "b1", "name": "go",
                       "component": "React", "props": {}})
    clicks, changes = [], []

    @c["go"].on_click
    def _():
        clicks.append(1)

    @c["go"].on_change
    def _(v):
        changes.append(v)

    assert {"type": "subscribe", "id": "b1"} in sent
    # events for the subscribed panel dispatch through the client
    c._client._handle({"type": "input", "id": "b1", "payload": {"value": 3}})
    assert clicks == [1]
    assert changes == [3]


# -- e2e: one Python button, handlers firing in BOTH processes ------------------

def test_e2e_button_runs_handlers_in_both_processes():
    from fastapi.testclient import TestClient
    from danvas import server

    canvas = danvas.Canvas()
    go = canvas.button("go")
    python_side = []
    go.on_click(lambda: python_side.append(1))
    canvas._bridge._merge = _MergeHost(canvas._bridge)
    app = server.create_app(canvas._bridge, open_browser=False)

    with TestClient(app) as client:
        with client.websocket_connect("/ws?source=1&label=rust") as src:
            # the peer resolves the button BY NAME from the register stream
            btn_id = None
            while btn_id is None:
                m = src.receive_json()
                if m.get("type") == "register" and m.get("name") == "go":
                    btn_id = m["id"]
            assert btn_id == go.id
            src.send_text(json.dumps({"type": "subscribe", "id": btn_id}))

            with client.websocket_connect("/ws") as browser:
                browser.send_text(json.dumps(
                    {"type": "input", "id": btn_id, "payload": {"clicks": 1}}))
                # the peer process got the event…
                while True:
                    m = src.receive_json()
                    if m.get("type") == "input" and m.get("id") == btn_id:
                        break
                # …and the Python handler ran too — one button, two processes
                for _ in range(100):
                    if python_side:
                        break
                    time.sleep(0.01)
                assert python_side == [1]
