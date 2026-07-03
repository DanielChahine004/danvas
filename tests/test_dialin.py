"""Dial-in sources: a process CONNECTS TO the hub (?source=1) instead of
serving a canvas for the hub to dial — the polyglot on-ramp. Its frames ride
the same upstream machinery (namespacing, caching, fan-out, retention); it is
also a subscriber (the bridge replays the hub's state to it on connect).

Unit half: the host internals against fake sockets (test_merge.py harness).
E2E half: a real canvas app over FastAPI's TestClient — one socket dials in as
a source, another connects as a browser.
"""

import asyncio
import json
import threading

import danvas
from danvas.merge import MergeBridge, _Conn, _MergeHost
from danvas.source import SourceClient


class FakeWS:
    """Stands in for both a browser and an inbound source socket (the dial-in
    path talks back through _InboundWS -> send_text)."""
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def send_bytes(self, data):
        pass

    def types(self, kind):
        return [m for m in self.sent if m.get("type") == kind]


def _host(retain=None):
    b = MergeBridge() if retain is None else MergeBridge(retain=retain)
    b._loop = asyncio.get_running_loop()
    return b, b._merge


def _browser(b, host):
    ws = FakeWS()
    b._connections.add(ws)
    conn = _Conn(ws)
    host._conns[ws] = conn
    return ws, conn


async def _settle():
    await asyncio.sleep(0.02)


def _dial(h, label="rust"):
    ws = FakeWS()
    h.on_connect(ws, {"source": "1", "label": label})
    return ws, h._dialins[ws]


# -- unit: the host side --------------------------------------------------------

def test_dialin_registers_fan_out_namespaced_to_browsers():
    async def run():
        b, h = _host()
        bws, _conn = _browser(b, h)
        sws, up = _dial(h)
        assert up.dialin and up.status == "live"
        handled = await h.route(sws, json.dumps(
            {"type": "register", "id": "p1", "component": "Slider",
             "props": {"min": 0}, "x": 10, "y": 20}))
        assert handled is True
        await _settle()
        regs = bws.types("register")
        assert regs and regs[-1]["id"] == f"{up.tag}:p1"
        assert up.registers[f"{up.tag}:p1"]["props"] == {"min": 0}
    asyncio.run(run())


def test_browser_input_routes_back_to_the_dialin_socket():
    async def run():
        b, h = _host()
        bws, _conn = _browser(b, h)
        sws, up = _dial(h)
        await h.route(sws, json.dumps(
            {"type": "register", "id": "p1", "component": "Slider", "props": {}}))
        await _settle()
        handled = await h.route(bws, json.dumps(
            {"type": "input", "id": f"{up.tag}:p1", "payload": {"value": 7}}))
        assert handled is True
        await _settle()
        # arrived on the source socket with the namespace stripped
        assert sws.sent[-1] == {"type": "input", "id": "p1", "payload": {"value": 7}}
    asyncio.run(run())


def test_late_browser_gets_dialin_replay():
    async def run():
        b, h = _host()
        sws, up = _dial(h)
        await h.route(sws, json.dumps(
            {"type": "register", "id": "p1", "component": "Label", "props": {}}))
        await h.route(sws, json.dumps(
            {"type": "update", "id": "p1", "payload": {"value": "hi"}}))
        await _settle()
        bws, conn = _browser(b, h)
        h.on_connect(bws, {})           # replaces the raw _browser wiring
        await _settle()
        conn2 = h._conns[bws]
        assert up.key in conn2.sources
        assert [m["id"] for m in bws.types("register")] == [f"{up.tag}:p1"]
        upd = [m for m in bws.types("update") if m["id"] == f"{up.tag}:p1"]
        assert upd and upd[-1]["payload"] == {"value": "hi"}
    asyncio.run(run())


def test_dialin_disconnect_retains_frozen_and_redial_replaces():
    async def run():
        b, h = _host()                   # retain defaults on
        bws, _conn = _browser(b, h)
        sws, up = _dial(h, label="telemetry")
        await h.route(sws, json.dumps(
            {"type": "register", "id": "p1", "component": "Slider", "props": {}}))
        await _settle()
        bws.sent.clear()
        h.on_disconnect(sws)             # the source process died
        await _settle()
        assert up.status == "offline" and up.registers      # held
        frozen = [m for m in bws.types("update") if m["id"] == f"{up.tag}:p1"]
        assert frozen and frozen[-1]["payload"] == {"operable": False,
                                                    "opacity": 0.45}
        # next life: same label dials back in -> stale frames replaced
        bws.sent.clear()
        sws2, up2 = _dial(h, label="telemetry")
        assert up2 is up and up.status == "live"            # same identity
        await h.route(sws2, json.dumps(
            {"type": "register", "id": "p2", "component": "Slider", "props": {}}))
        await _settle()
        assert f"{up.tag}:p1" in {m["id"] for m in bws.types("remove")}
        assert [m["id"] for m in bws.types("register")] == [f"{up.tag}:p2"]
    asyncio.run(run())


def test_dialin_no_retain_drops_and_forgets():
    async def run():
        b, h = _host(retain=False)
        bws, conn = _browser(b, h)
        sws, up = _dial(h)
        await h.route(sws, json.dumps(
            {"type": "register", "id": "p1", "component": "Slider", "props": {}}))
        await _settle()
        h.on_disconnect(sws)
        await _settle()
        assert bws.types("remove")
        assert up.key not in h._upstreams and up.key not in conn.sources
    asyncio.run(run())


def test_browser_leaving_does_not_tear_down_a_dialin():
    async def run():
        b, h = _host()
        bws, _conn = _browser(b, h)
        h.on_connect(bws, {})            # real conn registration
        sws, up = _dial(h)
        h.on_disconnect(bws)             # the only browser leaves
        assert up.key in h._upstreams    # source outlives browser refs
        assert h._dialins[sws] is up
    asyncio.run(run())


def test_dialin_offset_positioning_works_via_pseudo_uri():
    async def run():
        b, h = _host()
        bws, _conn = _browser(b, h)
        sws, up = _dial(h, label="rig")
        await h.route(sws, json.dumps(
            {"type": "register", "id": "p", "component": "Label",
             "props": {}, "x": 10, "y": 20}))
        await _settle()
        h.set_offset(up.ws_uri, (600, 0))          # "dialin:rig" resolves to itself
        await _settle()
        assert up.registers[f"{up.tag}:p"]["x"] == 610
        assert h.offset_of("dialin:rig") == (600.0, 0.0)
    asyncio.run(run())


def test_non_content_frames_from_a_source_fall_through_to_base():
    async def run():
        b, h = _host()
        sws, _up = _dial(h)
        assert await h.route(sws, json.dumps({"type": "heartbeat"})) is False
        assert await h.route(sws, json.dumps(
            {"type": "input", "id": "hub-panel", "payload": {}})) is False
    asyncio.run(run())


# -- e2e: a real canvas app, one source socket + one browser socket -------------

def test_e2e_dialin_source_on_a_real_canvas():
    from fastapi.testclient import TestClient
    from danvas import server

    canvas = danvas.Canvas()
    canvas.label("status", "idle")                      # the hub's own panel
    canvas._bridge._merge = _MergeHost(canvas._bridge)  # what serve(merge=True) does
    app = server.create_app(canvas._bridge, open_browser=False)

    with TestClient(app) as client:
        with client.websocket_connect("/ws?source=1&label=rust") as src:
            # the source is also a subscriber: it receives the hub's state
            seen = []
            while True:
                m = src.receive_json()
                seen.append(m.get("type"))
                if m.get("type") == "register":
                    break                                # hub's own label panel
            assert "welcome" in seen
            src.send_text(json.dumps(
                {"type": "register", "id": "temp", "component": "Slider",
                 "props": {"min": 0, "max": 100}, "x": 40, "y": 40}))
            src.send_text(json.dumps(
                {"type": "update", "id": "temp", "payload": {"value": 55}}))

            with client.websocket_connect("/ws") as browser:
                ns_reg = None
                while ns_reg is None:
                    m = browser.receive_json()
                    if m.get("type") == "register" and str(m["id"]).endswith(":temp"):
                        ns_reg = m
                assert ns_reg["props"] == {"min": 0, "max": 100}
                nsid = ns_reg["id"]
                # browser operates the source's panel -> routed to the source
                browser.send_text(json.dumps(
                    {"type": "input", "id": nsid, "payload": {"value": 70}}))
                while True:
                    m = src.receive_json()
                    if m.get("type") == "input":
                        assert m == {"type": "input", "id": "temp",
                                     "payload": {"value": 70}}
                        break


# -- SourceClient internals (no network) ----------------------------------------

def test_source_client_builds_frames_and_replay_cache():
    c = SourceClient("127.0.0.1:8000", label="rig")
    assert "source=1" in c._uri and "label=rig" in c._uri
    c.register("temp", "Slider", props={"min": 0}, x=10, y=20)
    c.update("temp", value=5)
    c.update("temp", color="red")
    assert c._registers["temp"]["component"] == "Slider"
    assert c._registers["temp"]["x"] == 10
    assert c._updates["temp"] == {"value": 5, "color": "red"}  # accumulated
    c.remove("temp")
    assert c._registers == {} and c._updates == {}


def test_source_client_dispatches_input_and_layout_and_taps():
    c = SourceClient(":8000", label="rig")
    got = {"input": None, "layout": None, "taps": []}

    @c.on_input("temp")
    def _(payload):
        got["input"] = payload

    @c.on_layout("temp")
    def _(msg):
        got["layout"] = msg

    c.on_frame(lambda m: got["taps"].append(m.get("type")))
    c._handle({"type": "input", "id": "temp", "payload": {"value": 3}})
    c._handle({"type": "layout", "id": "temp", "x": 9})
    c._handle({"type": "input", "id": "other", "payload": {}})   # not ours
    assert got["input"] == {"value": 3}
    assert got["layout"]["x"] == 9
    assert got["taps"] == ["input", "layout", "input"]           # taps see all


def test_source_client_replay_sends_registers_then_updates():
    c = SourceClient(":8000")
    c.register("a", "Label", props={})
    c.update("a", value="x")

    sent = []

    class Sock:
        async def send(self, text):
            sent.append(json.loads(text))

    asyncio.run(c._replay(Sock()))
    assert [m["type"] for m in sent] == ["register", "update"]
    assert sent[1]["payload"] == {"value": "x"}
