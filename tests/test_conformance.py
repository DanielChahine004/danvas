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
        # A {password} placeholder formats to empty here — both hubs treat an
        # empty --password as an open bind, so one template serves both the
        # open and the secure fixture.
        cmd = [part.format(port=port, password="")
               for part in cmd_tpl.split("|")]
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
        self.blobs = []   # binary frames, raw

    async def send(self, msg):
        await self.ws.send(json.dumps(msg))

    async def send_binary(self, data):
        await self.ws.send(data)

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
                self.blobs.append(bytes(raw))
                continue
            m = json.loads(raw)
            self.frames.append(m)
            if pred(m):
                return m

    async def recv_blob(self, pred, timeout=5.0):
        """The binary twin: first blob matching pred (buffered included)."""
        for b in self.blobs:
            if pred(b):
                return b
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no matching blob; have {len(self.blobs)}")
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            if isinstance(raw, (bytes, bytearray)):
                b = bytes(raw)
                self.blobs.append(b)
                if pred(b):
                    return b
            else:
                self.frames.append(json.loads(raw))


def _bin_frame(code, cid, payload):
    """The protocol's binary envelope: [type][idLen][id bytes][payload]."""
    cid_b = cid.encode()
    return bytes([code, len(cid_b)]) + cid_b + payload


def _bin_parse(data):
    code, idlen = data[0], data[1]
    return code, data[2:2 + idlen].decode(), data[2 + idlen:]


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


# -- auth: the /__auth__ cookie flow gates HTTP and WS ---------------------------

PASSWORD = "hunter2"


@pytest.fixture(scope="module")
def secure_hub():
    """A password-protected hub. DANVAS_HUB_CMD may carry a {password}
    placeholder; the default Python hub uses --password."""
    port = _free_port()
    cmd_tpl = os.environ.get("DANVAS_HUB_CMD")
    if cmd_tpl and "{password}" in cmd_tpl:
        cmd = [p.format(port=port, password=PASSWORD) for p in cmd_tpl.split("|")]
    elif cmd_tpl:
        pytest.skip("DANVAS_HUB_CMD given without a {password} placeholder")
    else:
        cmd = [sys.executable, "-m", "danvas.merge", "--port", str(port),
               "--no-open", "--password", PASSWORD]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
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


def _login(port, password):
    """POST /__auth__; return the pc_session token or None."""
    import re
    import urllib.request
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/__auth__",
        data=f"password={password}".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(req, timeout=5)
    except urllib.error.HTTPError as e:
        resp = e
    cookie = resp.headers.get("Set-Cookie") or ""
    m = re.search(r"pc_session=([^;]+)", cookie)
    return m.group(1) if m else None


def test_auth_gates_http_and_websocket(secure_hub):
    import urllib.error
    import urllib.request
    # HTTP without a session: the login page, 401.
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{secure_hub}/", timeout=5)
        status = 200
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 401
    # WS without a session: no welcome frame obtainable.
    async def unauthed():
        try:
            async with _browser(secure_hub) as ws:
                p = Peer(ws)
                await p.recv_until(lambda m: m.get("type") == "welcome",
                                   timeout=2.0)
                return True
        except Exception:
            return False
    got = [None]

    async def go():
        got[0] = await unauthed()
    _run(go())
    assert got[0] is False


def test_auth_wrong_password_yields_no_session(secure_hub):
    assert _login(secure_hub, "wrong") is None


def test_auth_cookie_unlocks_http_and_ws_fully(secure_hub):
    import urllib.request
    token = _login(secure_hub, PASSWORD)
    assert token
    req = urllib.request.Request(
        f"http://127.0.0.1:{secure_hub}/",
        headers={"Cookie": f"pc_session={token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200

    async def go():
        headers = {"Cookie": f"pc_session={token}"}
        async with ws_connect(
                f"ws://127.0.0.1:{secure_hub}/ws?source=1&label=sec",
                max_size=None, additional_headers=headers) as sws, \
            ws_connect(f"ws://127.0.0.1:{secure_hub}/ws",
                       max_size=None, additional_headers=headers) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "p", "name": "sec1",
                            "component": "React", "props": {}})
            await br.recv_until(
                lambda m: m.get("type") == "register"
                and m.get("name") == "sec1")
    _run(go())


# -- heartbeat reaping: silence is death, heartbeats are life --------------------

@pytest.fixture(scope="module")
def reaping_hub():
    """A hub with a 2s heartbeat deadline (DANVAS_HEARTBEAT_TIMEOUT env —
    honoured by both hubs so the reap is testable in seconds)."""
    port = _free_port()
    cmd_tpl = os.environ.get("DANVAS_HUB_CMD")
    if cmd_tpl:
        cmd = [p.format(port=port, password="") for p in cmd_tpl.split("|")]
    else:
        cmd = [sys.executable, "-m", "danvas.merge", "--port", str(port),
               "--no-open"]
    env = dict(os.environ)
    env["DANVAS_HEARTBEAT_TIMEOUT"] = "2"
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
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


def test_silent_source_is_reaped_heartbeating_source_survives(reaping_hub):
    async def go():
        async with _browser(reaping_hub) as bws:
            br = Peer(bws)
            # two sources: c17 goes silent, c18 heartbeats
            quiet = await _source(reaping_hub, "c17")
            beat = await _source(reaping_hub, "c18")
            q, b = Peer(quiet), Peer(beat)
            await q.recv_until(lambda m: m["type"] == "welcome")
            await b.recv_until(lambda m: m["type"] == "welcome")
            await q.send({"type": "register", "id": "p", "name": "p17",
                          "component": "React", "props": {}})
            await b.send({"type": "register", "id": "p", "name": "p18",
                          "component": "React", "props": {}})
            regq = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "p17")
            regb = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "p18")

            # keep c18 alive with heartbeats while c17 stays mute; the browser
            # heartbeats too (it must not be reaped either)
            async def keepalive(peer, seconds):
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    await peer.send({"type": "heartbeat"})
                    await asyncio.sleep(0.7)

            frozen = asyncio.create_task(br.recv_until(
                lambda m: m.get("type") == "update"
                and m.get("id") == regq["id"]
                and (m.get("payload") or {}).get("operable") is False,
                timeout=10.0))
            await asyncio.gather(keepalive(b, 7), keepalive(br, 7), frozen)
            # c17 was reaped and retention froze it…
            assert frozen.result()["payload"]["opacity"] is not None
            # …while heartbeating c18 was never frozen
            assert not [m for m in br.frames
                        if m.get("type") == "update"
                        and m.get("id") == regb["id"]
                        and (m.get("payload") or {}).get("operable") is False]
    asyncio.run(asyncio.wait_for(go(), timeout=40))


# -- dialed-out sources: merge_add composes a SERVED canvas by URL ---------------

def _spawn_python_hub(port):
    """A served target canvas (always the Python hub — it's the environment
    here, not the implementation under test)."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "danvas.merge", "--port", str(port), "--no-open"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("target hub never started")


def test_merge_add_composes_and_routes_and_retains(hub):
    # (Single-browser scope: whether merge_add is per-connection or canvas-
    # wide is deliberately unpinned here.)
    tport = _free_port()
    target = _spawn_python_hub(tport)
    try:
        async def go():
            # the target canvas has one panel (contributed by a dial-in peer)
            tsrc = await _source(tport, "origin")
            ts = Peer(tsrc)
            await ts.recv_until(lambda m: m["type"] == "welcome")
            await ts.send({"type": "register", "id": "tp", "name": "tpanel",
                           "component": "React", "props": {}, "x": 5, "y": 5})
            async with _browser(hub) as bws:
                br = Peer(bws)
                await br.recv_until(lambda m: m["type"] == "welcome")
                # pull the served canvas in by URL, live
                await br.send({"type": "merge_add",
                               "uri": f"127.0.0.1:{tport}"})
                reg = await br.recv_until(
                    lambda m: m.get("type") == "register"
                    and m.get("name") == "tpanel", timeout=10.0)
                roster = await br.recv_until(
                    lambda m: m.get("type") == "merge_sources"
                    and any(str(tport) in str(s.get("label", ""))
                            or str(tport) in str(s.get("uri", ""))
                            for s in m.get("sources", [])))
                # interaction routes back through the chain to the origin
                await br.send({"type": "input", "id": reg["id"],
                               "payload": {"value": 3}})
                got = await ts.recv_until(lambda m: m.get("type") == "input",
                                          timeout=10.0)
                assert got["id"] == "tp" and got["payload"] == {"value": 3}
                # the served canvas dies -> its panels hold, frozen (retention)
                target.kill()
                frozen = await br.recv_until(
                    lambda m: m.get("type") == "update"
                    and m.get("id") == reg["id"]
                    and (m.get("payload") or {}).get("operable") is False,
                    timeout=15.0)
                assert frozen
            await tsrc.close()
        asyncio.run(asyncio.wait_for(go(), timeout=60))
    finally:
        target.kill()


def test_merge_remove_drops_a_dialed_source(hub):
    tport = _free_port()
    target = _spawn_python_hub(tport)
    try:
        async def go():
            tsrc = await _source(tport, "origin2")
            ts = Peer(tsrc)
            await ts.recv_until(lambda m: m["type"] == "welcome")
            await ts.send({"type": "register", "id": "q", "name": "qpanel",
                           "component": "React", "props": {}})
            async with _browser(hub) as bws:
                br = Peer(bws)
                await br.send({"type": "merge_add",
                               "uri": f"127.0.0.1:{tport}"})
                reg = await br.recv_until(
                    lambda m: m.get("type") == "register"
                    and m.get("name") == "qpanel", timeout=10.0)
                roster = await br.recv_until(
                    lambda m: m.get("type") == "merge_sources"
                    and any(str(tport) in str(s.get("label", ""))
                            or str(tport) in str(s.get("uri", ""))
                            for s in m.get("sources", [])))
                sid = next(s["sid"] for s in roster["sources"]
                           if str(tport) in str(s.get("label", ""))
                           or str(tport) in str(s.get("uri", "")))
                await br.send({"type": "merge_remove", "sid": sid})
                await br.recv_until(
                    lambda m: m.get("type") == "remove"
                    and m.get("id") == reg["id"], timeout=10.0)
            await tsrc.close()
        asyncio.run(asyncio.wait_for(go(), timeout=60))
    finally:
        target.kill()


# -- binary media: the envelope crosses the hub, ids rewritten -------------------

def test_binary_media_relays_to_browsers_namespaced(hub):
    async def go():
        async with _source(hub, "c19") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "cam", "name": "cam19",
                            "component": "React", "props": {}})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "cam19")
            payload = b"\xff\xd8jpegish-bytes"
            await src.send_binary(_bin_frame(1, "cam", payload))   # VIDEO
            blob = await br.recv_blob(lambda b: b and b[0] == 1)
            code, cid, got = _bin_parse(blob)
            assert cid == reg["id"]                    # namespaced in-envelope
            assert got == payload                      # payload untouched
    _run(go())


def test_binary_input_routes_back_to_owner_stripped(hub):
    async def go():
        async with _source(hub, "c20") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "mic", "name": "mic20",
                            "component": "React", "props": {}})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "mic20")
            payload = b"pcm-or-whatever"
            await br.send_binary(_bin_frame(5, reg["id"], payload))   # INPUT
            blob = await src.recv_blob(lambda b: b and b[0] == 5)
            code, cid, got = _bin_parse(blob)
            assert cid == "mic"                        # namespace stripped
            assert got == payload
    _run(go())


# -- /__describe__: headless inventory of the composed canvas --------------------

def test_describe_lists_composed_panels(hub):
    async def go():
        async with _source(hub, "c21") as sws:
            src = Peer(sws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "d", "name": "dpanel",
                            "component": "React", "props": {}, "x": 7, "y": 8})
            await asyncio.sleep(0.3)
            import urllib.request
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{hub}/__describe__", timeout=5) as r:
                inv = json.loads(r.read().decode())
            comps = inv["components"]
            entry = next(c for c in comps if c.get("name") == "dpanel")
            assert entry["owner"] == "c21"
            assert (entry["x"], entry["y"]) == (7, 8)
    _run(go())


# -- ledger: DANVAS_LEDGER records user actions to the SQLite schema -------------

@pytest.fixture(scope="module")
def ledger_hub(tmp_path_factory):
    port = _free_port()
    path = str(tmp_path_factory.mktemp("ledger") / "hub.canvas.db")
    cmd_tpl = os.environ.get("DANVAS_HUB_CMD")
    if cmd_tpl:
        cmd = [p.format(port=port, password="") for p in cmd_tpl.split("|")]
    else:
        cmd = [sys.executable, "-m", "danvas.merge", "--port", str(port),
               "--no-open"]
    env = dict(os.environ)
    env["DANVAS_LEDGER"] = path
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError(f"hub exited early: {cmd}")
                time.sleep(0.1)
        else:
            raise RuntimeError("hub never started")
        yield port, path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_ledger_records_user_actions(ledger_hub):
    port, path = ledger_hub

    async def go():
        async with _source(port, "c22") as sws, _browser(port) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "lp", "name": "lp22",
                            "component": "React", "props": {}})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "lp22")
            await br.send({"type": "input", "id": reg["id"],
                           "payload": {"value": 9}})
            await src.recv_until(lambda m: m.get("type") == "input")
            await asyncio.sleep(0.5)                # let the append land
    _run(go())

    import sqlite3
    con = sqlite3.connect(path)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "snapshots", "events"} <= tables    # the _ledger.py schema
    rows = con.execute(
        "SELECT type, comp, payload FROM events WHERE type='input'").fetchall()
    con.close()
    assert rows
    assert any("9" in (r[2] or "") for r in rows)


# -- merge_auth: composing a password-protected served canvas --------------------

def test_merge_auth_flow_for_protected_source(hub):
    tport = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "danvas.merge", "--port", str(tport),
         "--no-open", "--password", "sesame"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", tport), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.1)
        # give the protected target a panel (its own auth flow, raw)
        token = _login(tport, "sesame")

        async def go():
            tsrc = await ws_connect(
                f"ws://127.0.0.1:{tport}/ws?source=1&label=vault",
                max_size=None,
                additional_headers={"Cookie": f"pc_session={token}"})
            ts = Peer(tsrc)
            await ts.recv_until(lambda m: m["type"] == "welcome")
            await ts.send({"type": "register", "id": "v", "name": "vpanel",
                           "component": "React", "props": {}})
            async with _browser(hub) as bws:
                br = Peer(bws)
                await br.send({"type": "merge_add",
                               "uri": f"127.0.0.1:{tport}"})
                await br.recv_until(
                    lambda m: m.get("type") == "merge_auth_required",
                    timeout=10.0)
                await br.send({"type": "merge_auth",
                               "uri": f"127.0.0.1:{tport}",
                               "password": "wrong"})
                await br.recv_until(
                    lambda m: m.get("type") == "merge_auth_failed",
                    timeout=10.0)
                await br.send({"type": "merge_auth",
                               "uri": f"127.0.0.1:{tport}",
                               "password": "sesame"})
                await br.recv_until(
                    lambda m: m.get("type") == "register"
                    and m.get("name") == "vpanel", timeout=10.0)
            await tsrc.close()
        asyncio.run(asyncio.wait_for(go(), timeout=60))
    finally:
        proc.kill()


# -- managed shapes: relay, fold, replay ------------------------------------------

def test_shapes_relay_fold_and_replay(hub):
    async def go():
        async with _source(hub, "c23") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "shape", "id": "g1", "shapeType": "geo",
                            "x": 5, "y": 6, "rotation": 0, "opacity": 1,
                            "props": {"geo": "rectangle", "color": "blue"}})
            shp = await br.recv_until(
                lambda m: m.get("type") == "shape"
                and str(m.get("id", "")).endswith(":g1"))
            assert shp["props"]["geo"] == "rectangle"
            await src.send({"type": "shape_update", "id": "g1",
                            "x": 50, "props": {"color": "red"}})
            upd = await br.recv_until(
                lambda m: m.get("type") == "shape_update"
                and m.get("id") == shp["id"] and m.get("x") == 50)
            # a late browser gets the CURRENT shape (folded, not stale+patch)
            async with _browser(hub) as b2ws:
                b2 = Peer(b2ws)
                got = await b2.recv_until(
                    lambda m: m.get("type") == "shape"
                    and m.get("id") == shp["id"])
                assert got["x"] == 50
                assert got["props"]["color"] == "red"
    _run(go())


# -- request/response: the awaitable round-trip crosses the hub -------------------

def test_request_response_reaches_only_the_asker(hub):
    async def go():
        async with _source(hub, "c24") as sws, _browser(hub) as bws, \
                _browser(hub) as b2ws:
            src, br, b2 = Peer(sws), Peer(bws), Peer(b2ws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "rq", "name": "rq24",
                            "component": "React", "props": {}})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "rq24")
            await br.send({"type": "request", "id": reg["id"],
                           "reqId": "r-77", "data": {"ask": "sum"}})
            got = await src.recv_until(lambda m: m.get("type") == "request")
            assert got["id"] == "rq" and got["reqId"] == "r-77"
            await src.send({"type": "response", "reqId": "r-77", "result": 42})
            resp = await br.recv_until(
                lambda m: m.get("type") == "response"
                and m.get("reqId") == "r-77")
            assert resp["result"] == 42
            # the other browser never sees the reply (no cross-viewer leak)
            await asyncio.sleep(0.3)
            assert not [m for m in b2.frames if m.get("type") == "response"]
    _run(go())


# -- presence + chat: the hub is a place, not just a relay -----------------------

def test_presence_roster_tracks_joins_and_names(hub):
    async def go():
        async with _browser(hub) as aws:
            a = Peer(aws)
            wa = await a.recv_until(lambda m: m.get("type") == "welcome")
            my = wa["you"]["name"]
            await a.recv_until(
                lambda m: m.get("type") == "presence"
                and any(v.get("name") == my for v in m.get("viewers", [])))
            async with _browser(hub) as bws:
                b = Peer(bws)
                wb = await b.recv_until(lambda m: m.get("type") == "welcome")
                # the FIRST browser sees the second join
                await a.recv_until(
                    lambda m: m.get("type") == "presence"
                    and m.get("count", 0) >= 2)
                # renames propagate
                await b.send({"type": "set_name", "name": "renamed-peer"})
                await a.recv_until(
                    lambda m: m.get("type") == "presence"
                    and any(v.get("name") == "renamed-peer"
                            for v in m.get("viewers", [])))
    _run(go())


def test_chat_relays_and_replays_history(hub):
    async def go():
        async with _browser(hub) as aws, _browser(hub) as bws:
            a, b = Peer(aws), Peer(bws)
            wa = await a.recv_until(lambda m: m.get("type") == "welcome")
            await b.recv_until(lambda m: m.get("type") == "welcome")
            await a.send({"type": "chat", "text": "hello from A"})
            got = await b.recv_until(
                lambda m: m.get("type") == "chat"
                and m.get("text") == "hello from A")
            assert got.get("name")                      # server-stamped identity
            # a late joiner gets the conversation so far
            async with _browser(hub) as cws:
                c = Peer(cws)
                await c.recv_until(
                    lambda m: m.get("type") == "chat"
                    and m.get("text") == "hello from A")
    _run(go())


# -- view / shared assets / graveyard: the last small relays ---------------------

def test_source_view_folds_into_welcome_and_relays(hub):
    async def go():
        async with _source(hub, "c25") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "view", "view": {"zoom": 1.5, "grid": True}})
            live = await br.recv_until(
                lambda m: m.get("type") == "view"
                and (m.get("view") or {}).get("zoom") == 1.5)
            assert live["view"]["grid"] is True
            async with _browser(hub) as b2ws:      # late joiner: baked into welcome
                b2 = Peer(b2ws)
                w = await b2.recv_until(lambda m: m.get("type") == "welcome")
                assert (w.get("view") or {}).get("zoom") == 1.5
    _run(go())


def test_shared_assets_relay_and_replay(hub):
    async def go():
        async with _source(hub, "c26") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "shared",
                            "components": {"Pill": "function Pill(){return null}"},
                            "styles": ".pill{color:red}"})
            got = await br.recv_until(
                lambda m: m.get("type") == "shared"
                and "Pill" in (m.get("components") or {}))
            assert ".pill" in got.get("styles", "")
            async with _browser(hub) as b2ws:      # late joiner replays it
                b2 = Peer(b2ws)
                await b2.recv_until(
                    lambda m: m.get("type") == "shared"
                    and "Pill" in (m.get("components") or {}))
    _run(go())


def test_graveyard_roundtrip_through_the_hub(hub):
    async def go():
        async with _source(hub, "c27") as sws, _browser(hub) as bws:
            src, br = Peer(sws), Peer(bws)
            await src.recv_until(lambda m: m["type"] == "welcome")
            await src.send({"type": "register", "id": "gp", "name": "gp27",
                            "component": "React", "props": {}})
            reg = await br.recv_until(
                lambda m: m.get("type") == "register" and m.get("name") == "gp27")
            # viewer deletes the merged panel -> petition reaches the owner bare
            await br.send({"type": "graveyard", "id": reg["id"]})
            got = await src.recv_until(lambda m: m.get("type") == "graveyard")
            assert got["id"] == "gp"
            # the owner's graveyard roster relays namespaced (restore targets it)
            await src.send({"type": "graveyard_update",
                            "items": [{"id": "gp", "label": "gp27"}]})
            gy = await br.recv_until(
                lambda m: m.get("type") == "graveyard_update"
                and any(i.get("id") == reg["id"] for i in m.get("items", [])))
            await br.send({"type": "restore", "id": reg["id"]})
            back = await src.recv_until(lambda m: m.get("type") == "restore")
            assert back["id"] == "gp"
    _run(go())
