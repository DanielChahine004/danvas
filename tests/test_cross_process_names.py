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


# -- content-verb parity: peers replace content the way owners do ---------------

def test_set_props_value_routes_through_owner_update():
    c = danvas.Canvas()
    lbl = c.label("status", "idle")
    sld = c.slider("servo", min=0, max=180, default=90)
    fired = []
    sld.on_change(lambda v: fired.append(v))
    c._bridge._apply_props(lbl, {"value": "ready"})
    c._bridge._apply_props(sld, {"value": 120, "min": 10})
    assert lbl.value == "ready"          # a peer rewrote the label's text
    assert sld.value == 120 and sld.min == 10
    assert fired == []                   # update semantics: silent, no on_change


def test_remote_handle_update_is_native_shaped():
    c, sent = _canvas()
    c._client._handle({"type": "register", "id": "L1", "name": "status",
                       "component": "React", "props": {}})
    c["status"].update("ready")
    assert sent[-1] == {"type": "set_props", "id": "L1",
                        "props": {"value": "ready"}}
    c["status"].update("go", color=(0, 255, 0))
    assert sent[-1]["props"] == {"value": "go", "color": (0, 255, 0)}
    c["status"].value = "again"          # attribute form, same path
    assert sent[-1] == {"type": "set_props", "id": "L1",
                        "props": {"value": "again"}}


# -- ownership is visible: .owner + .sources -------------------------------------

def test_owner_rides_the_register_frame():
    c = danvas.Canvas()
    s = c.slider("servo", min=0, max=180)
    assert c._bridge.register_message(s)["owner"] == "host"
    assert s.owner == "host"                     # native twin

    rc, _sent = _canvas()
    rs = rc.slider("remote_servo", min=0, max=1)
    assert rc._bridge.register_message(rs)["owner"] == "rig"
    assert rs.owner == "rig"


def test_hub_restamps_relayed_panels_with_source_label():
    import asyncio
    from danvas.merge import MergeBridge

    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        h = b._merge
        ws = type("W", (), {"sent": []})()
        h.on_connect(ws, {"source": "1", "label": "telemetry"})
        up = h._dialins[ws]
        h._ingest(up, json.dumps({"type": "register", "id": "p1",
                                  "component": "Slider", "props": {},
                                  "owner": "host"}))     # source's self-view
        assert up.registers[f"{up.tag}:p1"]["owner"] == "telemetry"
    asyncio.run(run())


def test_remote_handle_owner_and_canvas_sources():
    c, _sent = _canvas()
    c._client._handle({"type": "register", "id": "a", "name": "servo",
                       "component": "React", "props": {}, "owner": "host"})
    c._client._handle({"type": "register", "id": "b", "name": "temp",
                       "component": "React", "props": {}, "owner": "cpp-rig"})
    assert c["servo"].owner == "host"
    assert c["temp"].owner == "cpp-rig"
    c.slider("mine", min=0, max=1)               # our own panel counts too
    assert c.sources == {"host": 1, "cpp-rig": 1, "rig": 1}


def test_hub_canvas_sources_property():
    c = danvas.Canvas()
    assert c.sources == []                       # merge host not enabled yet
    c._bridge._merge = _MergeHost(c._bridge)
    ws = type("W", (), {})()
    import asyncio

    async def run():
        c._bridge._loop = asyncio.get_running_loop()
        c._bridge._merge.on_connect(ws, {"source": "1", "label": "rig"})
        srcs = c.sources
        assert srcs == [{"label": "rig", "status": "live",
                         "dialin": True, "panels": 0}]
    asyncio.run(run())


# -- cross-source arrows: the replica makes foreign endpoints bindable -----------

def test_arrow_endpoints_compose_across_sources():
    import asyncio
    from danvas.merge import MergeBridge

    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        h = b._merge
        # the hub owns a panel of its own
        hub_panel = danvas.Slider("hub_servo")
        hub_panel._bind("HUBID", b)
        b.add_component(hub_panel)
        # source A contributes a panel
        aws = type("W", (), {"sent": []})()
        h.on_connect(aws, {"source": "1", "label": "A"})
        upA = h._dialins[aws]
        h._ingest(upA, json.dumps({"type": "register", "id": "pa",
                                   "component": "Label", "props": {}}))
        # source B draws: own panel -> hub panel, and own panel -> A's panel
        bws = type("W", (), {"sent": []})()
        h.on_connect(bws, {"source": "1", "label": "B"})
        upB = h._dialins[bws]
        h._ingest(upB, json.dumps({"type": "register", "id": "pb",
                                   "component": "Label", "props": {}}))
        h._ingest(upB, json.dumps({"type": "arrow", "id": "ar1",
                                   "start": "pb", "end": "HUBID",
                                   "props": {}}))
        h._ingest(upB, json.dumps({"type": "arrow", "id": "ar2",
                                   "start": "pb", "end": f"{upA.tag}:pa",
                                   "props": {}}))
        a1 = upB.arrows[f"{upB.tag}:ar1"]
        a2 = upB.arrows[f"{upB.tag}:ar2"]
        assert a1["start"] == f"{upB.tag}:pb"     # own endpoint: namespaced
        assert a1["end"] == "HUBID"               # hub endpoint: untouched
        assert a2["end"] == f"{upA.tag}:pa"       # other source's: untouched
    asyncio.run(run())


def test_remote_canvas_connect_binds_a_foreign_endpoint():
    c, sent = _canvas()
    mine = c.slider("mine", min=0, max=1)
    c._client._handle({"type": "register", "id": "PYID", "name": "servo",
                       "component": "React", "props": {}, "owner": "host"})
    arrow = c.connect(mine, c["servo"], text="feeds")
    frames = [m for m in sent if m.get("type") == "arrow"]
    assert frames[-1]["start"] == mine.id
    assert frames[-1]["end"] == "PYID"            # the composed foreign id
    # arrows survive a reconnect: they're in the replay
    replay = list(c._replay_frames())
    assert any(m.get("type") == "arrow" and m.get("end") == "PYID"
               for m in replay)
