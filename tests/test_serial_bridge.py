"""The serial bridge: a wired device's NDJSON frames become canvas panels.

Unit-level: the bridge's routing logic against an in-memory transport and a
recording client. End-to-end: a fake device on a pyserial ``socket://`` link,
the real bridge, the real danvasd — a probe browser sees the device's panel
and the device receives the browser's input.
"""

import json
import os
import queue
import socket
import subprocess
import threading
import time

import pytest

from danvas.serial import SerialBridge

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeTransport:
    """readline()/write() over queues — the device side of the bridge."""

    def __init__(self):
        self.to_bridge = queue.Queue()
        self.from_bridge = []

    def readline(self):
        try:
            return self.to_bridge.get(timeout=0.2)
        except queue.Empty:
            return b""

    def write(self, data):
        self.from_bridge.append(json.loads(data.decode()))

    def device_says(self, msg):
        self.to_bridge.put((json.dumps(msg) + "\n").encode())


class RecordingClient:
    """Duck-typed SourceClient capturing what the bridge asks of it."""

    def __init__(self):
        self.calls = []
        self.taps = []

    def on_frame(self, fn):
        self.taps.append(fn)

    def register_template(self, cid, kind, **kw):
        self.calls.append(("register_template", cid, kind, kw))

    def register(self, cid, component, props=None, **place):
        self.calls.append(("register", cid, component, props, place))

    def update(self, cid, **payload):
        self.calls.append(("update", cid, payload))

    def remove(self, cid):
        self.calls.append(("remove", cid))

    def _send(self, msg):
        self.calls.append(("send", msg))

    def hub_says(self, msg):
        for fn in self.taps:
            fn(msg)


def test_bridge_routes_device_frames_and_filters_hub_frames():
    t, c = FakeTransport(), RecordingClient()
    bridge = SerialBridge(t, c)
    pump = threading.Thread(target=bridge.pump, daemon=True)
    pump.start()

    t.device_says({"type": "register_template", "id": "pot", "kind": "slider",
                   "data": {"min": 0, "max": 1023, "value": 0}, "x": 40, "y": 40})
    t.device_says({"type": "update", "id": "pot", "payload": {"post": 512}})
    t.device_says({"type": "response", "reqId": "r1", "result": {"ok": 1}})
    t.device_says("not json at all")  # boot babble must not kill the pump
    deadline = time.monotonic() + 3
    while len(c.calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)

    kinds = [x[0] for x in c.calls]
    assert kinds == ["register_template", "update", "send"], c.calls
    assert c.calls[0][2] == "slider" and c.calls[0][3]["min"] == 0
    assert c.calls[1][2] == {"post": 512}

    # Down: only interactions for the device's OWN panels pass the filter.
    c.hub_says({"type": "input", "id": "pot", "payload": {"value": 700}})
    c.hub_says({"type": "input", "id": "someone_elses", "payload": {}})
    c.hub_says({"type": "update", "id": "pot", "payload": {"post": 1}})  # chatter
    assert t.from_bridge == [
        {"type": "input", "id": "pot", "payload": {"value": 700}}]
    bridge.close()


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


def test_end_to_end_device_to_browser_and_back():
    pyserial = pytest.importorskip("serial")
    binary = _danvasd()
    if binary is None:
        pytest.skip("danvasd not built")

    # A "device": a plain TCP server speaking NDJSON — what a UART carries,
    # minus the copper. The bridge dials it via pyserial's socket:// URL.
    dev_srv = socket.socket()
    dev_srv.bind(("127.0.0.1", 0))
    dev_srv.listen(1)
    dev_port = dev_srv.getsockname()[1]

    hub_sock = socket.socket()
    hub_sock.bind(("127.0.0.1", 0))
    hub_port = hub_sock.getsockname()[1]
    hub_sock.close()
    hub = subprocess.Popen([binary, "--port", str(hub_port)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    got_input = queue.Queue()

    def device():
        # Firmware-realistic: re-assert the register until the canvas talks
        # back (idempotent — a re-register replaces). This also absorbs a
        # pyserial quirk the copper never sees: socket:// purges its input
        # buffer during open(), so a single line racing the open can be
        # eaten. Real boards DTR-reset on port open and always speak after.
        conn, _ = dev_srv.accept()
        conn.settimeout(0.5)
        reg = (json.dumps({"type": "register_template", "id": "pot",
                           "kind": "slider",
                           "data": {"min": 0, "max": 1023, "value": 7},
                           "x": 30, "y": 30}) + "\n").encode()
        buf = b""
        deadline = time.time() + 20
        while time.time() < deadline:
            conn.sendall(reg)
            try:
                buf += conn.recv(4096)   # the browser's input, eventually
            except socket.timeout:
                continue
            except OSError:
                return
            if b"\n" in buf:
                line = buf.split(b"\n", 1)[0]
                got_input.put(json.loads(line.decode()))
                return

    threading.Thread(target=device, daemon=True).start()

    from danvas.serial import main as bridge_main
    bridge_thread = threading.Thread(
        target=bridge_main,
        args=([f"socket://127.0.0.1:{dev_port}", "--url",
               f"127.0.0.1:{hub_port}", "--label", "uno"],),
        daemon=True)
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", hub_port),
                                         timeout=0.3).close()
                break
            except OSError:
                time.sleep(0.1)
        bridge_thread.start()

        # A probe browser: sees the device's slider, pokes it.
        import asyncio
        from websockets.asyncio.client import connect as ws_connect

        async def probe():
            async with ws_connect(f"ws://127.0.0.1:{hub_port}/ws",
                                  max_size=None, max_queue=None) as ws:
                pot = None
                end = time.monotonic() + 15
                while time.monotonic() < end:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=end - time.monotonic())
                    if isinstance(raw, bytes):
                        continue
                    m = json.loads(raw)
                    if (m.get("type") == "register"
                            and m.get("name") == "pot"):
                        pot = m["id"]
                        break
                assert pot, "device's slider never reached the browser"
                await ws.send(json.dumps({"type": "input", "id": pot,
                                          "payload": {"value": 900}}))

        asyncio.run(asyncio.wait_for(probe(), timeout=30))
        msg = got_input.get(timeout=10)
        assert msg["type"] == "input" and msg["payload"]["value"] == 900
    finally:
        hub.kill()
        dev_srv.close()
