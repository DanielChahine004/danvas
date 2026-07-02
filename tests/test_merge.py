"""The standing merge server (danvas.merge): per-connection source sets, the
upstream pool, id namespacing + interaction routing, the eye toggle, and the
per-source auth handshake.

These drive MergeBridge's internals directly against fake browser/upstream
sockets on a real event loop, so no network is touched. `_run_upstream` (the real
outbound connection loop) is either parked with a dummy task or monkeypatched to a
no-op, so attaching a source never dials out.
"""

import asyncio
import json

import pytest

from danvas import merge as merge_mod
from danvas.merge import MergeBridge, _Conn, _parse_source


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
    """A source connection that records what the merge server forwards upstream."""
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(json.loads(text))


def _parts(host="127.0.0.1", port=8001):
    return ("http", host, port, False)


def _browser(b):
    ws = FakeWS()
    b._connections.add(ws)
    conn = _Conn(ws)
    b._conns[ws] = conn
    return ws, conn


def _park(b, up):
    """Give an upstream a harmless cancellable task so _attach won't start the
    real _run_upstream (which would dial the network)."""
    up._task = b._loop.create_task(asyncio.sleep(3600))


async def _settle():
    # let the create_task-scheduled sends run
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
    assert MergeBridge._ns("s3", "abc") == "s3:abc"
    assert MergeBridge._strip("s3:abc") == ("s3", "abc")
    # an origid that itself contains a colon survives (split on the first only)
    assert MergeBridge._strip("s3:a:b") == ("s3", "a:b")


# -- upstream pool: keyed by (uri, cookie), tags, ref-counting ----------------

def test_pool_keys_by_uri_and_cookie():
    b = MergeBridge()
    up_open = b._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
    assert b._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0)) is up_open
    up_auth = b._get_or_create_upstream("ws://P/ws", _parts(), "P", "tok", (0, 0))
    assert up_auth is not up_open          # different credential -> different upstream
    assert up_open.tag != up_auth.tag      # distinct id namespaces


def test_ref_counting_tears_down_on_last_release():
    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        wsA, connA = _browser(b)
        wsB, connB = _browser(b)
        up = b._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(b, up)
        b._attach(connA, up)
        b._attach(connB, up)
        assert up.refs == 2
        b._release(connA, up.key)
        assert up.refs == 1 and up.key in b._upstreams
        b._release(connB, up.key)
        assert up.refs == 0
        assert up.key not in b._upstreams and up.tag not in b._tag_to_upstream
    asyncio.run(run())


# -- per-connection fan-out ----------------------------------------------------

def test_fanout_is_per_connection():
    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        wsA, connA = _browser(b)   # A sees P and Q
        wsB, connB = _browser(b)   # B sees only P
        upP = b._get_or_create_upstream("ws://P/ws", _parts(port=8001), "P", None, (0, 0))
        upQ = b._get_or_create_upstream("ws://Q/ws", _parts(port=8002), "Q", None, (0, 0))
        _park(b, upP)
        _park(b, upQ)
        b._attach(connA, upP)
        b._attach(connA, upQ)
        b._attach(connB, upP)
        b._ingest(upP, json.dumps({"type": "register", "id": "p1", "component": "Slider", "props": {}}))
        b._ingest(upQ, json.dumps({"type": "register", "id": "q1", "component": "Label", "props": {}}))
        await _settle()
        a_ids = {m["id"] for m in wsA.types("register")}
        b_ids = {m["id"] for m in wsB.types("register")}
        assert a_ids == {f"{upP.tag}:p1", f"{upQ.tag}:q1"}
        assert b_ids == {f"{upP.tag}:p1"}          # B never sees Q
    asyncio.run(run())


# -- id namespacing + interaction routing back to the owning source -----------

def test_input_routes_to_owning_source_with_stripped_id():
    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        ws, conn = _browser(b)
        up = b._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(b, up)
        up.ws = FakeUpstreamWS()
        b._attach(conn, up)
        await b._route_from_browser(conn, json.dumps(
            {"type": "input", "id": f"{up.tag}:panel1", "payload": {"value": 5}}))
        assert up.ws.sent == [{"type": "input", "id": "panel1", "payload": {"value": 5}}]
    asyncio.run(run())


def test_region_offset_applied_down_and_removed_up():
    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        ws, conn = _browser(b)
        up = b._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (100, 0))
        _park(b, up)
        up.ws = FakeUpstreamWS()
        b._attach(conn, up)
        b._ingest(up, json.dumps({"type": "register", "id": "p", "component": "Slider",
                                  "props": {}, "x": 10, "y": 20}))
        await _settle()
        reg = ws.types("register")[0]
        assert (reg["x"], reg["y"]) == (110, 20)      # offset applied downstream
        await b._route_from_browser(conn, json.dumps(
            {"type": "layout", "id": f"{up.tag}:p", "x": 150, "y": 25}))
        assert up.ws.sent == [{"type": "layout", "id": "p", "x": 50, "y": 25}]  # removed upstream
    asyncio.run(run())


def test_arrow_endpoints_are_namespaced():
    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        ws, conn = _browser(b)
        up = b._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(b, up)
        b._attach(conn, up)
        b._ingest(up, json.dumps({"type": "arrow", "id": "a1", "start": "n1",
                                  "end": "n2", "props": {}}))
        await _settle()
        arr = ws.types("arrow")[0]
        assert arr["id"] == f"{up.tag}:a1"
        assert arr["start"] == f"{up.tag}:n1" and arr["end"] == f"{up.tag}:n2"
    asyncio.run(run())


# -- eye toggle: hide removes, show replays -----------------------------------

def test_eye_toggle_hides_and_reshows_a_source():
    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        ws, conn = _browser(b)
        up = b._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(b, up)
        b._attach(conn, up)
        b._ingest(up, json.dumps({"type": "register", "id": "p", "component": "Slider", "props": {}}))
        await _settle()
        ws.sent.clear()
        await b._route_from_browser(conn, json.dumps(
            {"type": "merge_toggle", "sid": up.tag, "hidden": True}))
        assert any(m["type"] == "remove" and m["id"] == f"{up.tag}:p" for m in ws.sent)
        assert up.key in conn.hidden
        ws.sent.clear()
        await b._route_from_browser(conn, json.dumps(
            {"type": "merge_toggle", "sid": up.tag, "hidden": False}))
        await _settle()
        assert any(m["type"] == "register" and m["id"] == f"{up.tag}:p" for m in ws.sent)
        assert up.key not in conn.hidden
    asyncio.run(run())


# -- the per-source auth handshake (network mocked) ---------------------------

def test_open_source_attaches(monkeypatch):
    async def _noop(self, up):
        return
    monkeypatch.setattr(MergeBridge, "_run_upstream", _noop)
    monkeypatch.setattr(merge_mod, "_probe_source", lambda parts: "open")

    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        ws, conn = _browser(b)
        await b._add_source_for_conn(conn, "127.0.0.1:8001")
        await _settle()                             # _emit_sources schedules the roster
        assert len(conn.sources) == 1
        assert ws.types("merge_sources")            # roster emitted
    asyncio.run(run())


def test_protected_source_without_password_prompts(monkeypatch):
    monkeypatch.setattr(merge_mod, "_probe_source", lambda parts: "auth")

    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        ws, conn = _browser(b)
        await b._add_source_for_conn(conn, "127.0.0.1:8002")
        assert conn.sources == set()                # not attached
        assert ws.types("merge_auth_required")
    asyncio.run(run())


def test_protected_source_with_correct_password_attaches(monkeypatch):
    async def _noop(self, up):
        return
    monkeypatch.setattr(MergeBridge, "_run_upstream", _noop)
    monkeypatch.setattr(merge_mod, "_authenticate", lambda parts, pw: "cookie-token")

    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        ws, conn = _browser(b)
        await b._add_source_for_conn(conn, "127.0.0.1:8002", password="secret")
        assert len(conn.sources) == 1
        up = b._upstreams[next(iter(conn.sources))]
        assert up.cookie == "cookie-token"          # the source is viewed as that role
    asyncio.run(run())


def test_protected_source_with_wrong_password_fails(monkeypatch):
    monkeypatch.setattr(merge_mod, "_authenticate", lambda parts, pw: None)

    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        ws, conn = _browser(b)
        await b._add_source_for_conn(conn, "127.0.0.1:8002", password="wrong")
        assert conn.sources == set()
        assert ws.types("merge_auth_failed")
    asyncio.run(run())


def test_two_passwords_for_one_source_make_two_upstreams(monkeypatch):
    async def _noop(self, up):
        return
    monkeypatch.setattr(MergeBridge, "_run_upstream", _noop)
    tokens = iter(["tok-admin", "tok-viewer"])
    monkeypatch.setattr(merge_mod, "_authenticate", lambda parts, pw: next(tokens))

    async def run():
        b = MergeBridge()
        b._loop = asyncio.get_running_loop()
        _wsA, connA = _browser(b)
        _wsB, connB = _browser(b)
        await b._add_source_for_conn(connA, "127.0.0.1:8002", password="admin-pw")
        await b._add_source_for_conn(connB, "127.0.0.1:8002", password="viewer-pw")
        # same uri, different roles -> two upstreams (each role-filtered by the source)
        assert len(b._upstreams) == 2
    asyncio.run(run())
