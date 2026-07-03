"""Hub conformance: protocol-v1 assertions against a REAL hub process.

This is the executable contract any hub implementation must pass — today the
Python one (``python -m danvas.merge``), later the Rust ``danvasd``. The hub
under test is chosen by the ``DANVAS_HUB_CMD`` env var, a format string run as
a shell-less argv after ``.format(port=...)`` splitting on ``|``::

    # default (Python hub):
    pytest tests/test_conformance.py
    # a candidate broker:
    DANVAS_HUB_CMD="broker/target/debug/danvasd|--port|{port}" pytest tests/test_conformance.py

Everything here speaks raw frames over real sockets — no danvas imports on the
client side beyond the websockets library — so it measures the wire, not the
implementation.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import pytest
from websockets.asyncio.client import connect as ws_connect

# -- the hub under test ---------------------------------------------------------


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def hub():
    """Spawn the hub under test once for the module; yield its port."""
    port = _free_port()
    cmd_tpl = os.environ.get("DANVAS_HUB_CMD")
    if cmd_tpl:
        cmd = [part.format(port=port) for part in cmd_tpl.split("|")]
    else:
        cmd = [sys.executable, "-m", "danvas.merge", "--port", str(port),
               "--no-open"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
                s.close()
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError(f"hub exited early: {cmd}")
                time.sleep(0.1)
        else:
            raise RuntimeError(f"hub never opened port {port}: {cmd}")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


class Peer:
    """One raw protocol connection (browser or dial-in source)."""

    def __init__(self, ws):
        self.ws = ws
        self.frames = []

    async def send(self, msg):
        await self.ws.send(json.dumps(msg))

    async def recv_until(self, pred, timeout=5.0):
        """Return the first (possibly already-buffered) frame matching pred."""
        for m in self.frames:
            if pred(m):
                return m
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"no matching frame; saw {[m.get('type') for m in self.frames][-12:]}")
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            if isinstance(raw, (bytes, bytearray)):
                continue
            m = json.loads(raw)
            self.frames.append(m)
            if pred(m):
                return m


def _browser(port):
    return ws_connect(f"ws://127.0.0.1:{port}/ws", max_size=None)


def _source(port, label):
    return ws_connect(
        f"ws://127.0.0.1:{port}/ws?source=1&label={label}", max_size=None)


def _run(coro):
    asyncio.run(asyncio.wait_for(coro, timeout=30))


# -- conformance assertions -------------------------------------------------------

def test_welcome_is_first_and_advertises_protocol_v1(hub):
    async def go():
        async with _browser(hub) as ws:
            p = Peer(ws)
            m = await p.recv_until(lambda m: True)         # the very first frame
            assert m["type"] == "welcome"
            assert m["protocol"] == 1
            assert "you" in m and "runId" in m
    _run(go())


def test_source_register_reaches_browser_namespaced_with_identity(hub):
    async def go():
        async with _source(hub, "c1") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "servo",
                            "component": "React", "props": {"data": "{}"},
                            "name": "servo", "owner": "host", "x": 10, "y": 20})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register"
                and str(m.get("id", "")).endswith(":servo"))
            assert reg["id"] != "servo"                    # namespaced
            assert reg["name"] == "servo"                  # identity preserved
            assert reg["owner"] == "c1"                    # re-stamped to label
            assert (reg["x"], reg["y"]) == (10, 20)
    _run(go())


def test_late_browser_gets_replayed_state(hub):
    async def go():
        async with _source(hub, "c2") as sws:
            src = Peer(sws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "p", "name": "p2",
                            "component": "React", "props": {}})
            await src.send({"type": "update", "id": "p",
                            "payload": {"value": 41}})
            await asyncio.sleep(0.2)                       # let the hub fold it
            async with _browser(hub) as bws:
                br = Peer(bws)
                reg = await br.recv_until(
                    lambda m: m.get("type") == "register"
                    and m.get("name") == "p2")
                upd = await br.recv_until(
                    lambda m: m.get("type") == "update"
                    and m.get("id") == reg["id"]
                    and (m.get("payload") or {}).get("value") == 41)
                assert upd
    _run(go())


def test_browser_input_routes_to_owner_stripped(hub):
    async def go():
        async with _source(hub, "c3") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "sl", "name": "sl3",
                            "component": "React", "props": {}})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "sl3")
            await br.send({"type": "input", "id": reg["id"],
                           "payload": {"value": 7}})
            got = await src.recv_until(lambda m: m.get("type") == "input")
            assert got == {"type": "input", "id": "sl",
                           "payload": {"value": 7}}        # namespace stripped
    _run(go())


def test_subscribe_fans_input_copies_to_non_owners(hub):
    async def go():
        async with _source(hub, "c4") as aws, _source(hub, "c5") as ows, \
                _browser(hub) as bws:
            owner, other, br = Peer(aws), Peer(ows), Peer(bws)
            await owner.recv_until(lambda m: m["type"] == "welcome")
            await other.recv_until(lambda m: m["type"] == "welcome")
            await owner.send({"type": "register", "id": "go", "name": "go4",
                              "component": "React", "props": {}})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "go4")
            await other.send({"type": "subscribe", "id": reg["id"]})
            await asyncio.sleep(0.2)
            await br.send({"type": "input", "id": reg["id"],
                           "payload": {"clicks": 1}})
            assert (await owner.recv_until(
                lambda m: m.get("type") == "input"))["id"] == "go"
            copy = await other.recv_until(lambda m: m.get("type") == "input")
            assert copy["id"] == reg["id"]                 # composed id for peers
            assert copy["payload"] == {"clicks": 1}
    _run(go())


def test_set_props_routes_to_owner(hub):
    async def go():
        async with _source(hub, "c6") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "sp", "name": "sp6",
                            "component": "React", "props": {}})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "sp6")
            await br.send({"type": "set_props", "id": reg["id"],
                           "props": {"min": 5}})
            got = await src.recv_until(lambda m: m.get("type") == "set_props")
            assert got == {"type": "set_props", "id": "sp",
                           "props": {"min": 5}}
    _run(go())


def test_retention_freezes_then_redial_replaces(hub):
    async def go():
        async with _browser(hub) as bws:
            br = Peer(bws)
            sws = await _source(hub, "c7")
            src = Peer(sws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "r", "name": "r7",
                            "component": "React", "props": {}})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "r7")
            nsid = reg["id"]
            await sws.close()                              # the source dies
            frozen = await br.recv_until(
                lambda m: m.get("type") == "update" and m.get("id") == nsid
                and (m.get("payload") or {}).get("operable") is False)
            assert frozen["payload"].get("opacity") is not None   # visibly held
            # next life under the same label: stale panel replaced by fresh one
            async with _source(hub, "c7") as sws2:
                src2 = Peer(sws2)
                await src2.recv_until(lambda m: m["type"] == "welcome")
                await src2.send({"type": "register", "id": "r2", "name": "r7b",
                                 "component": "React", "props": {}})
                await br.recv_until(
                    lambda m: m.get("type") == "remove" and m.get("id") == nsid)
                await br.recv_until(
                    lambda m: m.get("type") == "register"
                    and m.get("name") == "r7b")
    _run(go())


def test_cross_source_arrow_endpoints_pass_through(hub):
    async def go():
        async with _source(hub, "c8") as aws, _source(hub, "c9") as ows, \
                _browser(hub) as bws:
            a, b, br = Peer(aws), Peer(ows), Peer(bws)
            await a.recv_until(lambda m: m["type"] == "welcome")
            await b.recv_until(lambda m: m["type"] == "welcome")
            await a.send({"type": "register", "id": "pa", "name": "pa8",
                          "component": "React", "props": {}})
            rega = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "pa8")
            await b.send({"type": "register", "id": "pb", "name": "pb9",
                          "component": "React", "props": {}})
            regb = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "pb9")
            # b arrows its own panel to a's (composed id from its replica)
            await b.send({"type": "arrow", "id": "ar", "start": "pb",
                          "end": rega["id"], "props": {}})
            arr = await br.recv_until(lambda m: m.get("type") == "arrow")
            assert arr["start"] == regb["id"]              # own: namespaced
            assert arr["end"] == rega["id"]                # foreign: untouched
    _run(go())


def test_serves_the_frontend(hub):
    # A browser must be able to point straight at the hub: GET / is the app.
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{hub}/", timeout=5) as r:
        body = r.read(4096).decode("utf-8", "replace")
    assert "<html" in body.lower()


def test_roster_lists_dialin_sources_live_then_offline(hub):
    async def go():
        sws = await _source(hub, "c10")
        src = Peer(sws)
        await src.recv_until(lambda m: m["type"] == "welcome")
        await src.send({"type": "register", "id": "p", "name": "p10",
                        "component": "React", "props": {}})
        await asyncio.sleep(0.2)
        async with _browser(hub) as bws:
            br = Peer(bws)
            roster = await br.recv_until(
                lambda m: m.get("type") == "merge_sources"
                and any(s.get("label") == "c10" for s in m.get("sources", [])))
            entry = next(s for s in roster["sources"] if s["label"] == "c10")
            assert entry["status"] == "live"
            await sws.close()                          # the source dies
            gone = await br.recv_until(
                lambda m: m.get("type") == "merge_sources"
                and any(s.get("label") == "c10"
                        and s.get("status") == "offline"
                        for s in m.get("sources", [])))
            assert gone                                # retained, shown offline
    _run(go())


def test_source_ink_relays_namespaced_and_replays(hub):
    async def go():
        async with _source(hub, "c11") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "draw", "diff": {
                "added": {"d1": {"id": "d1", "x": 5, "props": {}}},
                "updated": {}, "removed": {}}})
            drew = await br.recv_until(
                lambda m: m.get("type") == "draw"
                and any(k.endswith(":d1")
                        for k in (m.get("diff", {}).get("added") or {})))
            nsid = next(k for k in drew["diff"]["added"] if k.endswith(":d1"))
            assert drew["diff"]["added"][nsid]["id"] == nsid   # record id remapped
            # a late browser gets the ink in its replay
            async with _browser(hub) as b2ws:
                b2 = Peer(b2ws)
                await b2.recv_until(
                    lambda m: m.get("type") == "draw"
                    and nsid in (m.get("diff", {}).get("added") or {}))
    _run(go())


def test_browser_ink_edit_routes_back_to_owner_stripped(hub):
    async def go():
        async with _source(hub, "c12") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "draw", "diff": {
                "added": {"dz": {"id": "dz", "props": {}}},
                "updated": {}, "removed": {}}})
            drew = await br.recv_until(
                lambda m: m.get("type") == "draw"
                and any(k.endswith(":dz")
                        for k in (m.get("diff", {}).get("added") or {})))
            nsid = next(k for k in drew["diff"]["added"] if k.endswith(":dz"))
            await br.send({"type": "draw", "diff": {
                "added": {}, "updated": {}, "removed": {nsid: {}}}})
            back = await src.recv_until(lambda m: m.get("type") == "draw")
            assert "dz" in (back["diff"].get("removed") or {})   # stripped
    _run(go())


def test_browser_drag_geometry_survives_replay(hub):
    # A browser's layout drag must fold into the hub's replay cache — the
    # owner deliberately doesn't echo layout back, so the hub is responsible.
    async def go():
        async with _source(hub, "c13") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "p", "name": "p13",
                            "component": "React", "props": {}, "x": 10, "y": 10})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "p13")
            await br.send({"type": "layout", "id": reg["id"], "x": 300, "y": 120})
            await asyncio.sleep(0.3)
            async with _browser(hub) as b2ws:
                b2 = Peer(b2ws)
                await b2.recv_until(
                    lambda m: m.get("id") == reg["id"] and (
                        (m.get("type") == "update"
                         and (m.get("payload") or {}).get("x") == 300)
                        or (m.get("type") == "register" and m.get("x") == 300)))
    _run(go())


def test_hub_native_ink_is_stored_and_replayed(hub):
    # Ink drawn ON the hub view (bare ids) is the hub's own annotation layer:
    # relayed to other viewers AND replayed to late joiners.
    async def go():
        async with _browser(hub) as bws:
            br = Peer(bws)
            await br.recv_until(lambda m: m["type"] == "welcome")
            await br.send({"type": "draw", "diff": {
                "added": {"hubink1": {"id": "hubink1", "x": 3, "props": {}}},
                "updated": {}, "removed": {}}})
            await asyncio.sleep(0.3)
            async with _browser(hub) as b2ws:
                b2 = Peer(b2ws)
                await b2.recv_until(
                    lambda m: m.get("type") == "draw"
                    and "hubink1" in (m.get("diff", {}).get("added") or {}))
    _run(go())


def test_merge_offset_translates_a_source(hub):
    # The 📍 origin drag: merge_offset shifts the source's cached panels for
    # everyone (live updates + shifted replay + roster offset).
    async def go():
        async with _source(hub, "c14") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "p", "name": "p14",
                            "component": "React", "props": {}, "x": 10, "y": 20})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "p14")
            roster = await br.recv_until(
                lambda m: m.get("type") == "merge_sources"
                and any(s.get("label") == "c14" for s in m.get("sources", [])))
            sid = next(s["sid"] for s in roster["sources"]
                       if s["label"] == "c14")
            await br.send({"type": "merge_offset", "sid": sid,
                           "x": 600, "y": 0})
            await br.recv_until(
                lambda m: m.get("type") == "update" and m.get("id") == reg["id"]
                and (m.get("payload") or {}).get("x") == 610)   # 10 + 600
            # a late browser lands at the new origin
            async with _browser(hub) as b2ws:
                b2 = Peer(b2ws)
                await b2.recv_until(
                    lambda m: m.get("type") == "register"
                    and m.get("name") == "p14" and m.get("x") == 610)
    _run(go())


def test_replayed_register_is_fresh_not_stale_plus_patches(hub):
    # A hub browser-refresh must be equivalent to a direct source reconnect:
    # the replayed REGISTER itself carries current geometry and value (the
    # owner's own replay bakes them; transient channels like {post} and
    # racing update frames don't survive a fresh mount).
    async def go():
        async with _source(hub, "c15") as sws:
            src = Peer(sws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "p", "name": "p15",
                            "component": "React",
                            "props": {"data": json.dumps({"value": 90, "min": 0})},
                            "x": 10, "y": 20})
            await src.send({"type": "update", "id": "p", "payload": {"post": 140}})
            await src.send({"type": "update", "id": "p",
                            "payload": {"x": 520, "y": 300}})
            await asyncio.sleep(0.3)
            async with _browser(hub) as bws:
                br = Peer(bws)
                reg = await br.recv_until(
                    lambda m: m.get("type") == "register"
                    and m.get("name") == "p15")
                assert (reg["x"], reg["y"]) == (520, 300)      # fresh geometry
                blob = json.loads(reg["props"]["data"])
                assert blob["value"] == 140                    # fresh value
    _run(go())


def test_replayed_register_folds_text_content_too(hub):
    # Same freshness rule for text-content panels (a Label's key is "text").
    async def go():
        async with _source(hub, "c16") as sws:
            src = Peer(sws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "l", "name": "l16",
                            "component": "React",
                            "props": {"data": json.dumps({"text": "idle"})}})
            await src.send({"type": "update", "id": "l",
                            "payload": {"post": "done"}})
            await asyncio.sleep(0.3)
            async with _browser(hub) as bws:
                br = Peer(bws)
                reg = await br.recv_until(
                    lambda m: m.get("type") == "register"
                    and m.get("name") == "l16")
                assert json.loads(reg["props"]["data"])["text"] == "done"
    _run(go())
