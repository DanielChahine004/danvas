"""The shared property plane (set_props) + input-event subscription (subscribe).

The canvas as a shared document: any peer may write any panel's properties —
the write applies at the owner through its real setters, so ordering-through-
the-owner is the last-writer-wins — and any peer may receive a panel's input
events without owning it. Browsers pass the same gate as input; process peers
(dial-in sources, merge proxies) are authoritative and stop only at a hard
lock.
"""

import asyncio
import json

import danvas
from danvas.merge import MergeBridge, _Conn, _MergeHost
from danvas.source import SourceClient


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def send_bytes(self, data):
        pass


# -- _apply_props: the owner-side write path -----------------------------------

def test_apply_props_goes_through_real_setters():
    c = danvas.Canvas()
    s = c.slider("servo", min=0, max=180)
    c._bridge._apply_props(s, {"min": 10, "max": 90})
    assert (s.min, s.max) == (10, 90)


def test_apply_props_routes_layout_keys_through_set_layout():
    c = danvas.Canvas()
    s = c.slider("servo", min=0, max=180)
    c.insert(s, x=10, y=10)
    c._bridge._apply_props(s, {"x": 300, "y": 40, "opacity": 0.5, "min": 20})
    assert (s.x, s.y) == (300, 40)
    assert abs(s.opacity - 0.5) < 1e-9
    assert s.min == 20


def test_apply_props_drops_unknown_and_rejected_keys_harmlessly():
    c = danvas.Canvas()
    s = c.slider("servo", min=0, max=180)
    c._bridge._apply_props(s, {"no_such_prop": 1, "min": 30})   # must not raise
    assert s.min == 30
    assert not hasattr(s, "no_such_prop")


# -- gating: browsers petition, processes are authoritative, locked is a wall ---

def _gate(canvas, ws, comp, props, *, source=False):
    """Run the set_props gate exactly as _on_message does; record whether the
    write was submitted (dispatch is monkeypatched out for determinism)."""
    b = canvas._bridge
    if source:
        b._source_conns.add(ws)
    applied = []
    b._dispatch = type("D", (), {"submit": staticmethod(
        lambda fn: applied.append(fn))})()
    b._on_message(ws, json.dumps(
        {"type": "set_props", "id": comp.id, "props": props}))
    return applied


def test_browser_set_props_respects_operable_gate():
    c = danvas.Canvas()
    s = c.slider("servo", min=0, max=180, operable=False)
    ws = FakeWS()
    c._bridge._viewers[ws] = {"id": "v1", "role": None}
    assert _gate(c, ws, s, {"min": 5}) == []           # browser: blocked


def test_process_set_props_is_authoritative_but_locked_wins():
    c = danvas.Canvas()
    s = c.slider("servo", min=0, max=180, operable=False)
    ws = FakeWS()
    c._bridge._viewers[ws] = {"id": "v1", "role": None}
    assert len(_gate(c, ws, s, {"min": 5}, source=True)) == 1   # process: allowed
    s.locked = True
    ws2 = FakeWS()
    c._bridge._viewers[ws2] = {"id": "v2", "role": None}
    assert _gate(c, ws2, s, {"min": 5}, source=True) == []      # hard lock: no


# -- subscribe: input events fan to subscribers, originator excluded ------------

def test_subscribe_relays_input_copies():
    async def run():
        c = danvas.Canvas()
        btn = c.button("go")
        b = c._bridge
        b._loop = asyncio.get_running_loop()
        b._dispatch = type("D", (), {"submit": staticmethod(lambda fn: None)})()
        actor, listener = FakeWS(), FakeWS()
        for ws in (actor, listener):
            b._connections.add(ws)
            b._send_locks[ws] = asyncio.Lock()
            b._viewers[ws] = {"id": id(ws), "role": None}
        b._on_message(listener, json.dumps({"type": "subscribe", "id": btn.id}))
        b._on_message(actor, json.dumps(
            {"type": "input", "id": btn.id, "payload": {"clicks": 1}}))
        await asyncio.sleep(0.02)
        got = [m for m in listener.sent if m.get("type") == "input"]
        assert got == [{"type": "input", "id": btn.id, "payload": {"clicks": 1}}]
        assert not [m for m in actor.sent if m.get("type") == "input"]
        # unsubscribe stops the copies
        b._on_message(listener, json.dumps({"type": "unsubscribe", "id": btn.id}))
        b._on_message(actor, json.dumps(
            {"type": "input", "id": btn.id, "payload": {"clicks": 2}}))
        await asyncio.sleep(0.02)
        assert len([m for m in listener.sent if m.get("type") == "input"]) == 1
    asyncio.run(run())


# -- hub routing: petitions cross the merge/dial-in fabric ----------------------

def test_set_props_on_merged_panel_forwards_stripped_and_unoffset():
    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        h = b._merge
        ws = FakeWS()
        b._connections.add(ws)
        h._conns[ws] = _Conn(ws)
        up = h._get_or_create_upstream("ws://P/ws", ("http", "p", 80, False),
                                       "P", None, (100, 0))
        up._task = h._loop.create_task(asyncio.sleep(3600))

        class UpWS:
            sent = []
            async def send(self, text):
                UpWS.sent.append(json.loads(text))
        up.ws = UpWS()
        h._attach(h._conns[ws], up)
        handled = await h.route(ws, json.dumps(
            {"type": "set_props", "id": f"{up.tag}:p1",
             "props": {"min": 5, "x": 700}}))
        assert handled is True
        assert UpWS.sent == [{"type": "set_props", "id": "p1",
                              "props": {"min": 5, "x": 600}}]   # 700 - offset
    asyncio.run(run())


def test_dialin_source_can_subscribe_to_another_sources_panel():
    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        h = b._merge
        # source A dials in and registers a button
        aws = FakeWS()
        b._connections.add(aws)
        b._send_locks[aws] = asyncio.Lock()
        h.on_connect(aws, {"source": "1", "label": "A"})
        upA = h._dialins[aws]
        await h.route(aws, json.dumps(
            {"type": "register", "id": "go", "component": "Button", "props": {}}))
        nsid = f"{upA.tag}:go"
        # source B dials in and subscribes to A's button
        bws = FakeWS()
        b._connections.add(bws)
        b._send_locks[bws] = asyncio.Lock()
        h.on_connect(bws, {"source": "1", "label": "B"})
        assert await h.route(bws, json.dumps(
            {"type": "subscribe", "id": nsid})) is True
        # a browser clicks A's button -> A gets the input, B gets a copy
        vws = FakeWS()
        b._connections.add(vws)
        b._send_locks[vws] = asyncio.Lock()
        h.on_connect(vws, {})
        await h.route(vws, json.dumps(
            {"type": "input", "id": nsid, "payload": {"clicks": 1}}))
        await asyncio.sleep(0.02)
        assert {"type": "input", "id": "go", "payload": {"clicks": 1}} in aws.sent
        assert {"type": "input", "id": nsid, "payload": {"clicks": 1}} in bws.sent
    asyncio.run(run())


# -- e2e: a process rewrites a Python-borne slider, live -------------------------

def test_e2e_source_edits_python_slider_and_reacts_to_python_button():
    from fastapi.testclient import TestClient
    from danvas import server

    canvas = danvas.Canvas()
    servo = canvas.slider("servo", min=0, max=180)
    go = canvas.button("go")
    canvas._bridge._merge = _MergeHost(canvas._bridge)
    app = server.create_app(canvas._bridge, open_browser=False)

    with TestClient(app) as client:
        with client.websocket_connect("/ws?source=1&label=rust") as src:
            # learn the hub panels' ids from the subscriber stream
            seen_regs = set()
            while {servo.id, go.id} - seen_regs:
                m = src.receive_json()
                if m.get("type") == "register":
                    seen_regs.add(m["id"])
            # the "Rust" process rewrites the Python slider's range…
            src.send_text(json.dumps({"type": "set_props", "id": servo.id,
                                      "props": {"min": 10, "max": 90}}))
            # …and reacts to the Python button's clicks
            src.send_text(json.dumps({"type": "subscribe", "id": go.id}))

            # The setters broadcast update frames (shape is component-internal);
            # use their arrival as pacing and assert on the Python object.
            for _ in range(50):
                if (servo.min, servo.max) == (10, 90):
                    break
                src.receive_json()
            assert (servo.min, servo.max) == (10, 90)   # Python object followed

            with client.websocket_connect("/ws") as browser:
                browser.send_text(json.dumps(
                    {"type": "input", "id": go.id, "payload": {"clicks": 1}}))
                while True:
                    m = src.receive_json()
                    if m.get("type") == "input" and m.get("id") == go.id:
                        assert m["payload"] == {"clicks": 1}
                        break


# -- SourceClient surface ---------------------------------------------------------

def test_source_client_set_props_subscribe_and_mirror():
    c = SourceClient(":8000", label="rig")
    sent = []
    c._send = lambda msg: sent.append(msg)

    c.set_props("hub-panel", min=1, x=50)
    assert sent[-1] == {"type": "set_props", "id": "hub-panel",
                        "props": {"min": 1, "x": 50}}

    got = []
    c.subscribe("hub-btn", lambda p: got.append(p))
    assert sent[-1] == {"type": "subscribe", "id": "hub-btn"}
    c._handle({"type": "input", "id": "hub-btn", "payload": {"clicks": 2}})
    assert got == [{"clicks": 2}]

    # the mirror folds the hub's stream
    c._handle({"type": "register", "id": "p1", "component": "Slider",
               "props": {"min": 0}, "x": 5})
    c._handle({"type": "update", "id": "p1", "payload": {"value": 7}})
    assert c.panels["p1"]["component"] == "Slider"
    assert c.panels["p1"]["state"] == {"value": 7}
    c._handle({"type": "remove", "id": "p1"})
    assert "p1" not in c.panels

    # subscriptions replay on reconnect
    replayed = []

    class Sock:
        async def send(self, text):
            replayed.append(json.loads(text))

    asyncio.run(c._replay(Sock()))
    assert {"type": "subscribe", "id": "hub-btn"} in replayed
