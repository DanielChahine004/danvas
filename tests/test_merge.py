"""The merge machinery (danvas.merge._MergeHost): per-connection source sets, the
upstream pool, id namespacing + interaction routing, the proxy input echo, and the
per-source auth handshake. The same host powers the dedicated ``Merge`` server and
a normal ``Canvas`` served with ``merge=True`` (canvas-as-hub).

These drive the host's internals directly against fake browser/upstream sockets on
a real event loop, so no network is touched. `_run_upstream` (the real outbound
connection loop) is parked with a dummy task or monkeypatched to a no-op, so
attaching a source never dials out.
"""

import asyncio
import json

import pytest

import danvas
from danvas import merge as merge_mod
from danvas.bridge import Bridge
from danvas.merge import MergeBridge, _MergeHost, _Conn, _parse_source


class FakeWS:
    """A browser connection that records the frames the bridge sends it."""
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def send_bytes(self, data):  # unused; merge doesn't relay binary
        pass

    def types(self, kind):
        return [m for m in self.sent if m.get("type") == kind]


class FakeUpstreamWS:
    """A source connection that records what the merge host forwards upstream."""
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(json.loads(text))


def _host():
    """A MergeBridge with a running loop; return (bridge, host)."""
    b = MergeBridge()
    b._loop = asyncio.get_running_loop()
    return b, b._merge


def _parts(host="127.0.0.1", port=8001):
    return ("http", host, port, False)


def _browser(b, host):
    ws = FakeWS()
    b._connections.add(ws)
    conn = _Conn(ws)
    host._conns[ws] = conn
    return ws, conn


def _park(host, up):
    """Give an upstream a harmless cancellable task so _attach won't start the
    real _run_upstream (which would dial the network)."""
    up._task = host._loop.create_task(asyncio.sleep(3600))


async def _settle():
    await asyncio.sleep(0.02)


# -- pure helpers --------------------------------------------------------------

def test_parse_source_forms():
    ws_uri, parts, label = _parse_source(8001)
    assert ws_uri == "ws://localhost:8001/ws" and label == "localhost:8001"
    assert parts == ("http", "localhost", 8001, False)
    ws_uri, parts, label = _parse_source("host:8002")
    assert ws_uri == "ws://host:8002/ws" and parts[1:] == ("host", 8002, False)
    ws_uri, parts, label = _parse_source("https://x.loca.lt")
    assert ws_uri == "wss://x.loca.lt/ws" and parts == ("https", "x.loca.lt", 443, True)


def test_namespacing_roundtrip():
    assert _MergeHost._ns("s3", "abc") == "s3:abc"
    assert _MergeHost._strip("s3:abc") == ("s3", "abc")
    assert _MergeHost._strip("s3:a:b") == ("s3", "a:b")  # split on first colon only


# -- upstream pool: keyed by (uri, cookie), tags, ref-counting ----------------

def test_pool_keys_by_uri_and_cookie():
    async def run():
        _b, h = _host()
        up_open = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        assert h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0)) is up_open
        up_auth = h._get_or_create_upstream("ws://P/ws", _parts(), "P", "tok", (0, 0))
        assert up_auth is not up_open
        assert up_open.tag != up_auth.tag
    asyncio.run(run())


def test_ref_counting_tears_down_on_last_release():
    async def run():
        b, h = _host()
        _wsA, connA = _browser(b, h)
        _wsB, connB = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        h._attach(connA, up)
        h._attach(connB, up)
        assert up.refs == 2
        h._release(connA, up.key)
        assert up.refs == 1 and up.key in h._upstreams
        h._release(connB, up.key)
        assert up.refs == 0
        assert up.key not in h._upstreams and up.tag not in h._tag_to_upstream
    asyncio.run(run())


# -- per-connection fan-out ----------------------------------------------------

def test_fanout_is_per_connection():
    async def run():
        b, h = _host()
        wsA, connA = _browser(b, h)   # A sees P and Q
        wsB, connB = _browser(b, h)   # B sees only P
        upP = h._get_or_create_upstream("ws://P/ws", _parts(port=8001), "P", None, (0, 0))
        upQ = h._get_or_create_upstream("ws://Q/ws", _parts(port=8002), "Q", None, (0, 0))
        _park(h, upP)
        _park(h, upQ)
        h._attach(connA, upP)
        h._attach(connA, upQ)
        h._attach(connB, upP)
        h._ingest(upP, json.dumps({"type": "register", "id": "p1", "component": "Slider", "props": {}}))
        h._ingest(upQ, json.dumps({"type": "register", "id": "q1", "component": "Label", "props": {}}))
        await _settle()
        a_ids = {m["id"] for m in wsA.types("register")}
        b_ids = {m["id"] for m in wsB.types("register")}
        assert a_ids == {f"{upP.tag}:p1", f"{upQ.tag}:q1"}
        assert b_ids == {f"{upP.tag}:p1"}
    asyncio.run(run())


# -- id namespacing + interaction routing back to the owning source -----------

def test_input_routes_to_owning_source_with_stripped_id():
    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        up.ws = FakeUpstreamWS()
        h._attach(conn, up)
        handled = await h.route(ws, json.dumps(
            {"type": "input", "id": f"{up.tag}:panel1", "payload": {"value": 5}}))
        assert handled is True
        assert up.ws.sent == [{"type": "input", "id": "panel1", "payload": {"value": 5}}]
    asyncio.run(run())


def test_own_panel_interaction_is_not_intercepted():
    # A bare id (the hub's OWN panel) is left for the base dispatch.
    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        handled = await h.route(ws, json.dumps(
            {"type": "input", "id": "a-bare-uuid", "payload": {"value": 1}}))
        assert handled is False
    asyncio.run(run())


def test_region_offset_applied_down_and_removed_up():
    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (100, 0))
        _park(h, up)
        up.ws = FakeUpstreamWS()
        h._attach(conn, up)
        h._ingest(up, json.dumps({"type": "register", "id": "p", "component": "Slider",
                                  "props": {}, "x": 10, "y": 20}))
        await _settle()
        reg = ws.types("register")[0]
        assert (reg["x"], reg["y"]) == (110, 20)
        await h.route(ws, json.dumps({"type": "layout", "id": f"{up.tag}:p", "x": 150, "y": 25}))
        assert up.ws.sent == [{"type": "layout", "id": "p", "x": 50, "y": 25}]
    asyncio.run(run())


def test_arrow_endpoints_are_namespaced():
    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        h._attach(conn, up)
        h._ingest(up, json.dumps({"type": "arrow", "id": "a1", "start": "n1",
                                  "end": "n2", "props": {}}))
        await _settle()
        arr = ws.types("arrow")[0]
        assert arr["id"] == f"{up.tag}:a1"
        assert arr["start"] == f"{up.tag}:n1" and arr["end"] == f"{up.tag}:n2"
    asyncio.run(run())


# -- hide is client-side: the host holds no hidden state ----------------------

def test_hide_is_client_side_no_server_state():
    import inspect
    conn = _Conn(object())
    assert not hasattr(conn, "hidden")               # no per-conn hidden set
    src = inspect.getsource(_MergeHost.route)
    assert "merge_toggle" not in src                 # no server-side toggle handler


# -- changes made THROUGH the merged view stay in the cache (hide/show) --------

def test_layout_through_merge_is_cached_and_reaches_other_viewers():
    async def run():
        b, h = _host()
        wsA, connA = _browser(b, h)
        wsB, connB = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        up.ws = FakeUpstreamWS()
        h._attach(connA, up)
        h._attach(connB, up)
        nsid = f"{up.tag}:panel1"
        await h.route(wsA, json.dumps(
            {"type": "layout", "id": nsid, "x": 300, "y": 120, "w": 250, "h": 90}))
        await _settle()
        assert up.ws.sent and up.ws.sent[0]["id"] == "panel1" and up.ws.sent[0]["x"] == 300
        assert h._upstreams[up.key].updates[nsid]["x"] == 300
        b_geo = [m for m in wsB.sent if m.get("type") == "update" and m["id"] == nsid]
        a_geo = [m for m in wsA.sent if m.get("type") == "update" and m["id"] == nsid]
        assert b_geo and b_geo[-1]["payload"]["x"] == 300
        assert not a_geo
    asyncio.run(run())


def test_input_forwards_and_source_echo_cached_not_rubber_banded():
    async def run():
        b, h = _host()
        wsA, connA = _browser(b, h)
        wsB, connB = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        up.ws = FakeUpstreamWS()
        h._attach(connA, up)
        h._attach(connB, up)
        nsid = f"{up.tag}:slider1"
        await h.route(wsA, json.dumps({"type": "input", "id": nsid, "payload": {"value": 7}}))
        await _settle()
        assert up.ws.sent == [{"type": "input", "id": "slider1", "payload": {"value": 7}}]
        assert h._recent_input_mover(nsid) is connA
        # the source echoes its real state (a slider uses {post: v})
        h._ingest(up, json.dumps({"type": "update", "id": "slider1", "payload": {"post": 7}}))
        await _settle()
        assert up.updates[nsid] == {"post": 7}
        b_up = [m for m in wsB.sent if m.get("type") == "update" and m["id"] == nsid]
        a_up = [m for m in wsA.sent if m.get("type") == "update" and m["id"] == nsid]
        assert b_up and b_up[-1]["payload"] == {"post": 7}
        assert not a_up
    asyncio.run(run())


# -- base Bridge: a proxy connection is NOT excluded from its own input echo ---

def test_bridge_does_not_exclude_a_proxy_from_input_echo():
    class _WS:
        pass
    b = Bridge()
    b._loop = object()
    calls = []
    b._emit = lambda targets, msg: calls.append((list(targets), msg))
    proxy, other = _WS(), _WS()
    b._connections = {proxy, other}
    b._viewers = {proxy: {"id": "p", "role": None}, other: {"id": "o", "role": None}}
    b._proxy_conns = {proxy}
    sld = danvas.Slider("s")
    sld._bind("c1", b)
    b.add_component(sld)
    b._dispatch_input(sld, {"value": 7}, proxy)
    echo = [c for c in calls if c[1].get("type") == "update"]
    assert echo and proxy in echo[-1][0]
    calls.clear()
    b._dispatch_input(sld, {"value": 3}, other)
    echo = [c for c in calls if c[1].get("type") == "update"]
    assert echo and other not in echo[-1][0] and proxy in echo[-1][0]


# -- free-form drawing relay --------------------------------------------------

def test_source_ink_relays_down_namespaced_and_cached():
    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        h._attach(conn, up)
        h._ingest(up, json.dumps({"type": "draw", "diff": {
            "added": {"d1": {"id": "d1", "props": {"points": []}}}, "updated": {}, "removed": {}}}))
        await _settle()
        draws = [m for m in ws.sent if m.get("type") == "draw"]
        assert draws and f"{up.tag}:d1" in draws[-1]["diff"]["added"]
        assert draws[-1]["diff"]["added"][f"{up.tag}:d1"]["id"] == f"{up.tag}:d1"
        assert f"{up.tag}:d1" in up.drawings
    asyncio.run(run())


def test_editing_a_source_stroke_routes_back_up_stripped():
    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        up.ws = FakeUpstreamWS()
        h._attach(conn, up)
        handled = await h.route(ws, json.dumps({"type": "draw", "diff": {
            "removed": {f"{up.tag}:d1": {}}}}))
        assert handled is True
        await _settle()
        assert up.ws.sent == [{"type": "draw", "diff": {"added": {}, "updated": {}, "removed": {"d1": {}}}}]
    asyncio.run(run())


def test_pure_bare_ink_is_left_for_the_base_draw_path():
    # A fresh stroke (bare id) is the hub's own / merge-native ink; route declines
    # it (returns False) so the Bridge's normal draw handling stores + relays it.
    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        up.ws = FakeUpstreamWS()
        h._attach(conn, up)
        handled = await h.route(ws, json.dumps({"type": "draw", "diff": {
            "added": {"dX": {"id": "dX", "props": {}}}, "updated": {}, "removed": {}}}))
        assert handled is False           # delegated to base
        assert up.ws.sent == []           # never routed to a source
    asyncio.run(run())


def test_attach_replays_cached_source_ink():
    async def run():
        b, h = _host()
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        h._ingest(up, json.dumps({"type": "draw", "diff": {
            "added": {"d1": {"id": "d1", "props": {}}}, "updated": {}, "removed": {}}}))
        await _settle()
        ws, conn = _browser(b, h)
        h._attach(conn, up)
        await _settle()
        draws = [m for m in ws.sent if m.get("type") == "draw"]
        assert draws and f"{up.tag}:d1" in draws[-1]["diff"]["added"]
    asyncio.run(run())


# -- the per-source auth handshake (network mocked) ---------------------------

def test_open_source_attaches(monkeypatch):
    async def _noop(self, up):
        return
    monkeypatch.setattr(_MergeHost, "_run_upstream", _noop)
    monkeypatch.setattr(merge_mod, "_probe_source", lambda parts: "open")

    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        await h._add_source_for_conn(conn, "127.0.0.1:8001")
        await _settle()
        assert len(conn.sources) == 1
        assert ws.types("merge_sources")
    asyncio.run(run())


def test_protected_source_without_password_prompts(monkeypatch):
    monkeypatch.setattr(merge_mod, "_probe_source", lambda parts: "auth")

    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        await h._add_source_for_conn(conn, "127.0.0.1:8002")
        assert conn.sources == set()
        assert ws.types("merge_auth_required")
    asyncio.run(run())


def test_protected_source_with_correct_password_attaches(monkeypatch):
    async def _noop(self, up):
        return
    monkeypatch.setattr(_MergeHost, "_run_upstream", _noop)
    monkeypatch.setattr(merge_mod, "_authenticate", lambda parts, pw: "cookie-token")

    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        await h._add_source_for_conn(conn, "127.0.0.1:8002", password="secret")
        assert len(conn.sources) == 1
        up = h._upstreams[next(iter(conn.sources))]
        assert up.cookie == "cookie-token"
    asyncio.run(run())


def test_protected_source_with_wrong_password_fails(monkeypatch):
    monkeypatch.setattr(merge_mod, "_authenticate", lambda parts, pw: None)

    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        await h._add_source_for_conn(conn, "127.0.0.1:8002", password="wrong")
        assert conn.sources == set()
        assert ws.types("merge_auth_failed")
    asyncio.run(run())


def test_two_passwords_for_one_source_make_two_upstreams(monkeypatch):
    async def _noop(self, up):
        return
    monkeypatch.setattr(_MergeHost, "_run_upstream", _noop)
    tokens = iter(["tok-admin", "tok-viewer"])
    monkeypatch.setattr(merge_mod, "_authenticate", lambda parts, pw: next(tokens))

    async def run():
        b, h = _host()
        _wsA, connA = _browser(b, h)
        _wsB, connB = _browser(b, h)
        await h._add_source_for_conn(connA, "127.0.0.1:8002", password="admin-pw")
        await h._add_source_for_conn(connB, "127.0.0.1:8002", password="viewer-pw")
        assert len(h._upstreams) == 2
    asyncio.run(run())


# -- canvas-as-hub: serve(merge=True) enables the host on a normal canvas ------

def test_canvas_serve_enables_merge_host_by_default():
    c = danvas.Canvas()
    assert c._bridge._merge is None                  # off until served
    from danvas.merge import _MergeHost as _MH
    c._bridge._merge = _MH(c._bridge)
    assert isinstance(c._bridge._merge, _MergeHost)


# -- per-source origin offset (hub-wide translate) -----------------------------

def test_set_offset_shifts_panels_and_leaves_source_untouched():
    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        up.ws = FakeUpstreamWS()
        h._attach(conn, up)
        h._ingest(up, json.dumps({"type": "register", "id": "p", "component": "Slider",
                                  "props": {}, "x": 10, "y": 20}))
        await _settle()
        ws.sent.clear()
        # translate the source's origin to (600, 0) — hub-wide
        h.set_offset(up.ws_uri, (600, 0))
        await _settle()
        # cached register shifted (so reconnects land at the new origin)
        assert up.registers[f"{up.tag}:p"]["x"] == 610
        # live browsers got a position update
        upd = [m for m in ws.sent if m.get("type") == "update" and m["id"] == f"{up.tag}:p"]
        assert upd and upd[-1]["payload"] == {"x": 610, "y": 20}
        assert up.offset == (600.0, 0.0)
        # a NEW panel registers already at the new origin
        h._ingest(up, json.dumps({"type": "register", "id": "q", "component": "Label",
                                  "props": {}, "x": 5, "y": 5}))
        await _settle()
        assert up.registers[f"{up.tag}:q"]["x"] == 605
        # a drag of a merged panel routes back MINUS the offset (source coords)
        await h.route(ws, json.dumps({"type": "layout", "id": f"{up.tag}:p", "x": 700, "y": 20}))
        assert up.ws.sent[-1] == {"type": "layout", "id": "p", "x": 100, "y": 20}
    asyncio.run(run())


def test_canvas_merge_at_sets_offset():
    c = danvas.Canvas()
    c.merge("127.0.0.1:8001", at=(600, 40))
    assert c._pending_merges == [("127.0.0.1:8001", None, (600.0, 40.0))]
    c.move_merge("127.0.0.1:8001", 120, 8)
    assert c._pending_merges == [("127.0.0.1:8001", None, (120.0, 8.0))]


# -- the code API: canvas.merge / unmerge / merges -----------------------------

def test_canvas_merge_queues_before_serve_and_reads_back():
    c = danvas.Canvas()
    c.merge("127.0.0.1:8001")
    c.merge("127.0.0.1:8002", password="x")
    assert c.merges == ["127.0.0.1:8001", "127.0.0.1:8002"]
    c.unmerge("127.0.0.1:8001")
    assert c.merges == ["127.0.0.1:8002"]


def test_canvas_merge_is_canvas_wide_and_live(monkeypatch):
    # A shared source added while serving attaches to every connected browser and
    # is what a fresh connection is seeded with — the canvas-wide twin of a UI add.
    async def _noop(self, up):
        return
    monkeypatch.setattr(_MergeHost, "_run_upstream", _noop)
    monkeypatch.setattr(merge_mod, "_authenticate", lambda parts, pw: "tok")

    async def run():
        b, h = _host()
        ws, conn = _browser(b, h)          # a browser already connected
        h.add_source("127.0.0.1:8001")     # code merge, live
        await _settle()
        assert conn.sources                 # attached to the live browser
        assert h.shared_specs() == ["127.0.0.1:8001"]
        # a fresh browser is seeded with the shared source on connect
        ws2 = FakeWS()
        b._connections.add(ws2)
        h.on_connect(ws2, {})              # registers its own _Conn
        await _settle()
        conn2 = h._conns[ws2]
        assert conn2.sources
        # remove it -> released from everyone
        h.remove_source("127.0.0.1:8001")
        await _settle()
        assert not conn.sources and not conn2.sources and h.shared_specs() == []
    asyncio.run(run())
