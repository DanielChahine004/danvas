"""Source-SDK conformance: protocol-v1 assertions against a REAL SDK process.

The mirror of test_conformance.py (which validates hubs): this suite validates
*source SDKs* — the processes that dial into a hub and own panels. The hub is
always the real danvasd; the SDK under test is a spawned target process
implementing the fixed behavior script documented in
tests/sdk_conformance_target.py (the Python reference, the production Canvas
path) and danvas-rust/examples/conformance_target.rs (the Rust SDK).

Selection:

    # both built-in targets (Rust skipped unless its example is built):
    pytest tests/test_sdk_conformance.py
    # a candidate SDK in any language ('|'-separated argv, {port} formatted):
    DANVAS_SDK_CMD="./my_sdk_target|{port}" pytest tests/test_sdk_conformance.py

The probe side speaks raw frames over real sockets (a simulated browser) — so
this measures the wire, not any SDK's own client library. Passing this suite
is the definition of done for a new SDK.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
from websockets.asyncio.client import connect as ws_connect

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# -- the hub (always danvasd) and the SDK under test ------------------------------


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _danvasd():
    exe = os.environ.get("DANVASD")
    if exe and os.path.isfile(exe):
        return exe
    name = "danvasd.exe" if os.name == "nt" else "danvasd"
    for rel in ("broker/target/release", "broker/target/debug"):
        p = os.path.join(_ROOT, rel, name)
        if os.path.isfile(p):
            return p
    return None


def _rust_target():
    name = ("conformance_target.exe" if os.name == "nt"
            else "conformance_target")
    for rel in ("danvas-rust/target/debug/examples",
                "danvas-rust/target/release/examples"):
        p = os.path.join(_ROOT, rel, name)
        if os.path.isfile(p):
            return p
    return None


def _node():
    """A node >= 22 (the zero-dep SDK rides Node's own WebSocket), or None."""
    import shutil
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.check_output([node, "--version"], text=True).strip()
        if int(out.lstrip("v").split(".")[0]) < 22:
            return None
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    return node


def _sdk_cmds():
    """The SDK targets to parametrize over: env override, else the built-ins."""
    env = os.environ.get("DANVAS_SDK_CMD")
    if env:
        return [pytest.param(env.split("|"), id="env")]
    params = [pytest.param(
        [sys.executable, os.path.join(_ROOT, "tests", "sdk_conformance_target.py"),
         "{port}"], id="python")]
    rust = _rust_target()
    params.append(pytest.param(
        [rust, "{port}"] if rust else None, id="rust",
        marks=[] if rust else pytest.mark.skip(
            reason="cargo build --example conformance_target first")))
    node = _node()
    node_target = os.path.join(_ROOT, "danvas-node", "conformance_target.js")
    have_node = node and os.path.isfile(node_target)
    params.append(pytest.param(
        [node, node_target, "{port}"] if have_node else None, id="node",
        marks=[] if have_node else pytest.mark.skip(
            reason="node (>=22) not found")))
    return params


def _spawn(cmd, port):
    argv = [part.format(port=port) for part in cmd]
    return subprocess.Popen(argv, cwd=_ROOT, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _wait_port(port, proc=None, what="process", timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(f"{what} exited early")
            time.sleep(0.1)
    raise RuntimeError(f"{what} never opened port {port}")


def _stop(proc):
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


class Stack:
    """One broker + one SDK target, with restart controls for both."""

    def __init__(self, sdk_cmd):
        self.port = _free_port()
        self.sdk_cmd = sdk_cmd
        self.broker = None
        self.target = None

    def start_broker(self):
        binary = _danvasd()
        self.broker = subprocess.Popen(
            [binary, "--port", str(self.port), "--host", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _wait_port(self.port, self.broker, "danvasd")

    def start_target(self):
        self.target = _spawn(self.sdk_cmd, self.port)

    def stop(self):
        for proc in (self.target, self.broker):
            if proc is not None:
                _stop(proc)


@pytest.fixture(params=_sdk_cmds())
def stack(request):
    if _danvasd() is None:
        pytest.skip("danvasd not built (cargo build --release "
                    "--manifest-path broker/Cargo.toml)")
    st = Stack(request.param)
    st.start_broker()
    st.start_target()
    try:
        yield st
    finally:
        st.stop()


# -- the probe (a simulated browser; raw frames only) ------------------------------


class Probe:
    def __init__(self, ws):
        self.ws = ws
        self.frames = []
        self.blobs = []

    async def send(self, msg):
        await self.ws.send(json.dumps(msg))

    async def send_binary(self, data):
        await self.ws.send(data)

    async def recv_until(self, pred, timeout=15.0):
        for m in self.frames:
            if pred(m):
                return m
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "no matching frame; saw "
                    f"{[m.get('type') for m in self.frames][-15:]}")
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            if isinstance(raw, (bytes, bytearray)):
                self.blobs.append(bytes(raw))
                continue
            m = json.loads(raw)
            self.frames.append(m)
            if pred(m):
                return m

    async def recv_blob(self, pred, timeout=15.0):
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

    async def panel(self, name, timeout=20.0):
        """The composed id of the target's panel `name` (from its register).

        Matched by the register's `name` field, falling back to the id suffix
        — the wire id is the SDK's to mint (Python mints per-run ids; the Rust
        SDK uses the name), so a probe must resolve by name like any client.
        Waits out the target's startup: the first probe call in each test
        rides on this, so the timeout is generous (a cold Python target
        imports danvas).
        """
        m = await self.recv_until(
            lambda m: m.get("type") == "register"
            and (m.get("name") == name
                 or str(m.get("id", "")).split(":")[-1] == name), timeout)
        return m["id"]


def _browser(port):
    return ws_connect(f"ws://127.0.0.1:{port}/ws", max_size=None,
                      max_queue=None)


def _bin_frame(code, cid, payload):
    cid_b = cid.encode()
    return bytes([code, len(cid_b)]) + cid_b + payload


def _bin_parse(data):
    code, idlen = data[0], data[1]
    return code, data[2:2 + idlen].decode(), data[2 + idlen:]


def _run(coro):
    asyncio.run(asyncio.wait_for(coro, timeout=90))


def _post_of(m):
    return (m.get("payload") or {}).get("post")


# -- core: registering, routing, replay --------------------------------------------


def test_registers_native_panels(stack):
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            lbl = await p.panel("lbl")
            reg = next(m for m in p.frames
                       if m.get("type") == "register" and m.get("id") == lbl)
            # A native panel: React-shaped props with a data blob the
            # template asset defines (PROTOCOL.md § authoring native panels).
            data = json.loads(reg["props"]["data"])
            assert data.get("text") == "hello"
            for name in ("sld", "ask", "dl", "up", "bin", "cam", "ctl"):
                await p.panel(name)
    _run(go())


def test_input_routes_to_owner_handlers(stack):
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            sld = await p.panel("sld")
            lbl = await p.panel("lbl")
            await p.send({"type": "input", "id": sld,
                          "payload": {"value": 42}})
            await p.recv_until(
                lambda m: m.get("type") == "update" and m.get("id") == lbl
                and _post_of(m) == "v=42")
    _run(go())


def test_request_gets_a_response(stack):
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            ask = await p.panel("ask")
            await p.send({"type": "request", "id": ask, "reqId": "cr1",
                          "data": {"ping": 41}})
            m = await p.recv_until(
                lambda m: m.get("type") == "response"
                and (m.get("reqId") == "cr1" or m.get("req") == "cr1"))
            assert "42" in json.dumps(m), m
    _run(go())


def test_label_reconnect_replaces_previous_life(stack):
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            await p.panel("lbl")
        _stop(stack.target)
        stack.start_target()
        # A fresh probe sees the second life's panels, and its handlers run.
        # Two per-protocol wrinkles the probe must absorb: the replay may
        # still show the FIRST life's retained (frozen) panels while the
        # second connects, and panel ids are the SDK's to mint per run — the
        # second life's `sld` may arrive under a brand-new id. So each retry
        # re-resolves the latest register named `sld` and pokes that.
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            await p.panel("sld")

            def latest_sld():
                sld = None
                for m in p.frames:
                    if (m.get("type") == "register"
                            and (m.get("name") == "sld"
                                 or str(m.get("id", "")).split(":")[-1] == "sld")):
                        sld = m["id"]
                return sld

            deadline = time.monotonic() + 30
            while True:
                await p.send({"type": "input", "id": latest_sld(),
                              "payload": {"value": 7}})
                try:
                    await p.recv_until(
                        lambda m: m.get("type") == "update"
                        and _post_of(m) == "v=7", timeout=2.0)
                    break
                except TimeoutError:
                    if time.monotonic() > deadline:
                        raise
    _run(go())


def test_broker_restart_heals_with_layout_foldback(stack):
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            sld = await p.panel("sld")
            # A browser drag: the owner must fold this into its replay.
            await p.send({"type": "layout", "id": sld,
                          "x": 333, "y": 444, "w": 240, "h": 96})
            await asyncio.sleep(1.0)   # let the frame reach the owner
        _stop(stack.broker)
        stack.start_broker()
        # The target re-dials on its own (~1 s cadence) and replays
        # everything, including the folded geometry.
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            sld = await p.panel("sld", timeout=30.0)

            def carries_x(m):
                if m.get("id") != sld:
                    return False
                return (m.get("x") == 333
                        or (m.get("payload") or {}).get("x") == 333)
            await p.recv_until(
                lambda m: m.get("type") in ("register", "update")
                and carries_x(m), timeout=10.0)
    _run(go())


# -- binary: media out, opaque input in --------------------------------------------


def test_binary_input_reaches_owner_and_echoes(stack):
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            bin_id = await p.panel("bin")
            lbl = await p.panel("lbl")
            await p.send_binary(_bin_frame(5, bin_id, b"ping"))   # INPUT
            await p.recv_until(
                lambda m: m.get("type") == "update" and m.get("id") == lbl
                and _post_of(m) == "bin=4")

            def is_echo(b):
                code, cid, payload = _bin_parse(b)
                return code == 3 and cid == bin_id and payload == b"ping"
            await p.recv_blob(is_echo)
    _run(go())


def test_owner_streams_media_envelopes(stack):
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            ctl = await p.panel("ctl")
            cam = await p.panel("cam")
            await p.send({"type": "input", "id": ctl, "payload": {}})

            def is_frame(b):
                code, cid, payload = _bin_parse(b)
                return (code == 1 and cid == cam
                        and payload.endswith(b"conformance-jpeg"))
            await p.recv_blob(is_frame)
    _run(go())


# -- file transfer through the hub --------------------------------------------------


def _http(url, data=None, timeout=20):
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def test_download_roundtrip_and_decline_fast(stack):
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            dl = await p.panel("dl")
            await p.send({"type": "request", "id": dl, "reqId": "cr2",
                          "data": {}})
            m = await p.recv_until(
                lambda m: m.get("type") == "response"
                and (m.get("reqId") == "cr2" or m.get("req") == "cr2"))
            blob = json.dumps(m)
            url = json.loads(blob)
            # the url may sit at result.url or url depending on shape
            path = (url.get("result") or {}).get("url") or url.get("url")
            assert path and path.startswith("/__download__/"), m
            status, body = _http(f"http://127.0.0.1:{stack.port}{path}")
            assert status == 200 and body == b"conformance-bytes\n"
        # Decline-fast: the target must answer file_pull for a token it
        # doesn't own; the hub then 404s at once instead of waiting out its
        # 15 s all-sources deadline.
        t0 = time.monotonic()
        try:
            _http(f"http://127.0.0.1:{stack.port}/__download__/bogus")
            raise AssertionError("bogus token should not resolve")
        except urllib.error.HTTPError as e:
            assert e.code == 404
        assert time.monotonic() - t0 < 5.0, (
            "unknown download token was not declined promptly — the SDK must "
            "answer every file_pull broadcast (ok:false for tokens it "
            "doesn't own)")
    _run(go())


def test_upload_roundtrip(stack):
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            up = await p.panel("up")
            lbl = await p.panel("lbl")
            # The endpoint may ride the register's data blob (Python) or a
            # follow-up data_patch update (the Rust SDK) — a client folds
            # both, so the probe does too.
            def fold_url():
                url = None
                for m in p.frames:
                    if m.get("id") != up:
                        continue
                    if m.get("type") == "register":
                        url = json.loads(m["props"]["data"]).get("url") or url
                    elif m.get("type") == "update":
                        patch = (m.get("payload") or {}).get("data_patch") or {}
                        url = patch.get("url") or url
                return url
            deadline = time.monotonic() + 10
            while not (fold_url() or "").startswith("/__upload__/"):
                if time.monotonic() > deadline:
                    raise AssertionError(
                        "upload panel's data.url never carried a minted "
                        f"/__upload__/ endpoint (got {fold_url()!r})")
                try:
                    await p.recv_until(lambda m: m.get("id") == up
                                       and m.get("type") == "update",
                                       timeout=deadline - time.monotonic())
                except TimeoutError:
                    pass
            url = fold_url()
            status, body = _http(
                f"http://127.0.0.1:{stack.port}{url}?name=note.txt",
                data=b"conf-body")
            assert status == 200 and json.loads(body).get("ok") is True
            await p.recv_until(
                lambda m: m.get("type") == "update" and m.get("id") == lbl
                and _post_of(m) == "up=note.txt:9")
    _run(go())


# -- the shared plane ---------------------------------------------------------------


def test_set_props_applies_at_the_owner(stack):
    # The shared property plane: the hub routes the write to the owner (id
    # stripped); the owner APPLIES it and its echoed update is canonical.
    # Python applies through the component's real setters (_apply_props);
    # thin SDKs fold into the data blob — either way the probe must see the
    # echoed data_patch converge.
    async def go():
        async with _browser(stack.port) as ws:
            p = Probe(ws)
            sld = await p.panel("sld")
            await p.send({"type": "set_props", "id": sld,
                          "props": {"max": 50}})

            def reflects_max(m):
                if m.get("type") != "update" or m.get("id") != sld:
                    return False
                return (m.get("payload") or {}).get(
                    "data_patch", {}).get("max") == 50
            await p.recv_until(reflects_max, timeout=10.0)
    _run(go())
