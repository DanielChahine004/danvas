"""Hub retention (retain=True): a dead source's panels stay on the merged view,
frozen at their last-known state, until the source reconnects.

Same no-network harness as test_merge.py: the host internals are driven
directly against fake browser sockets on a real event loop.
"""

import asyncio
import json

import danvas
from danvas.merge import Merge, MergeBridge, _Conn, _MergeHost


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def send_bytes(self, data):
        pass

    def types(self, kind):
        return [m for m in self.sent if m.get("type") == kind]


def _host(retain=None):
    # retain=None takes MergeBridge's own default, so the default-check test
    # exercises the real signature rather than this helper's.
    b = MergeBridge() if retain is None else MergeBridge(retain=retain)
    b._loop = asyncio.get_running_loop()
    return b, b._merge


def _parts(port=8001):
    return ("http", "127.0.0.1", port, False)


def _browser(b, host):
    ws = FakeWS()
    b._connections.add(ws)
    conn = _Conn(ws)
    host._conns[ws] = conn
    return ws, conn


def _park(host, up):
    up._task = host._loop.create_task(asyncio.sleep(3600))


async def _settle():
    await asyncio.sleep(0.02)


def _seed(h, up):
    """One panel + one stroke ingested from the source."""
    h._ingest(up, json.dumps({"type": "register", "id": "p1",
                              "component": "Slider", "props": {}, "x": 10, "y": 20}))
    h._ingest(up, json.dumps({"type": "draw", "diff": {
        "added": {"d1": {"id": "d1", "props": {}}}, "updated": {}, "removed": {}}}))


# -- retain=False: the historical drop-on-offline, now opt-in ------------------

def test_retain_off_drops_on_offline():
    async def run():
        b, h = _host(retain=False)
        assert h.retain is False
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        h._attach(conn, up)
        _seed(h, up)
        await _settle()
        h._on_upstream_down(up)
        await _settle()
        assert ws.types("remove")            # panels torn down
        assert not up.registers and not up.drawings   # caches cleared
    asyncio.run(run())


# -- retain (the default): panels held and frozen ------------------------------

def test_retain_is_the_default():
    async def run():
        _b, h = _host()
        assert h.retain is True
    asyncio.run(run())


def test_retain_keeps_panels_and_freezes_them():
    async def run():
        b, h = _host(retain=True)
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        h._attach(conn, up)
        _seed(h, up)
        await _settle()
        ws.sent.clear()
        h._on_upstream_down(up)
        await _settle()
        assert not ws.types("remove")                 # nothing torn down
        assert up.registers                           # cache held
        nsid = f"{up.tag}:p1"
        frozen = [m for m in ws.types("update") if m["id"] == nsid]
        # non-operable AND dimmed: held data must not read as live
        assert frozen and frozen[-1]["payload"] == {"operable": False,
                                                    "opacity": 0.45}
        assert up.status == "offline"                 # roster shows the dot
        assert ws.types("merge_sources")
    asyncio.run(run())


def test_retained_source_replays_frozen_to_a_new_browser():
    # A browser joining WHILE the source is down still gets its last-known
    # panels + ink, with the freeze overlay appended.
    async def run():
        b, h = _host(retain=True)
        _ws0, conn0 = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        h._attach(conn0, up)
        _seed(h, up)
        await _settle()
        h._on_upstream_down(up)
        await _settle()

        ws2, conn2 = _browser(b, h)
        h._attach(conn2, up)
        await _settle()
        nsid = f"{up.tag}:p1"
        assert [m["id"] for m in ws2.types("register")] == [nsid]
        draws = [m for m in ws2.sent if m.get("type") == "draw"]
        assert draws and f"{up.tag}:d1" in draws[-1]["diff"]["added"]
        frozen = [m for m in ws2.types("update")
                  if m["id"] == nsid
                  and m["payload"] == {"operable": False, "opacity": 0.45}]
        assert frozen
    asyncio.run(run())


def test_input_to_a_retained_offline_source_is_swallowed():
    # up.ws is None while offline; routing an input must not raise and must not
    # dispatch anywhere.
    async def run():
        b, h = _host(retain=True)
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        h._attach(conn, up)
        _seed(h, up)
        await _settle()
        h._on_upstream_down(up)
        handled = await h.route(ws, json.dumps(
            {"type": "input", "id": f"{up.tag}:p1", "payload": {"value": 3}}))
        assert handled is True               # still owned by the merge plane
    asyncio.run(run())


def test_reconnect_tears_down_stale_frames_before_fresh_replay():
    # Panel ids are minted per run, so on reconnect the held frames are stale:
    # the host must remove them and let the new replay repopulate. Exercised at
    # the same point _run_upstream runs it, without dialing out.
    async def run():
        b, h = _host(retain=True)
        ws, conn = _browser(b, h)
        up = h._get_or_create_upstream("ws://P/ws", _parts(), "P", None, (0, 0))
        _park(h, up)
        h._attach(conn, up)
        _seed(h, up)
        await _settle()
        h._on_upstream_down(up)
        await _settle()
        ws.sent.clear()

        # what _run_upstream does on a successful reconnect:
        up.status = "live"
        if up.registers or up.arrows or up.drawings:
            for c in h._interested(up):
                h._send_source_teardown(c.ws, up)
            up.registers.clear(); up.updates.clear()
            up.arrows.clear(); up.drawings.clear()
        h._emit_sources_to_interested(up)
        # the restarted source replays with NEW ids
        h._ingest(up, json.dumps({"type": "register", "id": "p2",
                                  "component": "Slider", "props": {}, "x": 1, "y": 2}))
        await _settle()

        removed = {m["id"] for m in ws.types("remove")}
        assert f"{up.tag}:p1" in removed                  # stale panel gone
        draws = [m for m in ws.sent if m.get("type") == "draw"]
        assert draws and f"{up.tag}:d1" in draws[0]["diff"]["removed"]  # stale ink gone
        assert [m["id"] for m in ws.types("register")] == [f"{up.tag}:p2"]
        assert list(up.registers) == [f"{up.tag}:p2"]
    asyncio.run(run())


# -- plumbing: the flag reaches the host from every entry point ----------------

def test_retain_plumbing_from_merge_and_cli_and_canvas():
    import inspect
    import danvas.merge as merge_mod

    assert Merge()._bridge._merge.retain is True          # on by default
    assert Merge(retain=False)._bridge._merge.retain is False

    # the CLI opt-out exists and is forwarded
    assert "--no-retain" in inspect.getsource(merge_mod.main)
    assert "retain=not args.no_retain" in inspect.getsource(merge_mod.main)

    # canvas-as-hub: serve(merge_retain=...) exists and defaults on
    params = inspect.signature(danvas.Canvas.serve).parameters
    assert params["merge_retain"].default is True
    c = danvas.Canvas()
    c._bridge._merge = _MergeHost(c._bridge, retain=False)
    assert c._bridge._merge.retain is False
