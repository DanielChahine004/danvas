"""on_connect / on_disconnect under broker serving.

Serving is broker-only: a viewer joining reaches the source process as a
`presence` roster frame, not a websocket accept — so the connect/disconnect
taps must fire off the roster diff (regression: they never fired at all
through danvasd; only the retired embedded-server path called them).

Also guards get_image relay namespacing: a targeted canvas.screenshot()
sends the source's own panel ids, which the broker must rewrite to the
browser's tag-namespaced form (regression: relayed verbatim, so the browser
matched nothing and every targeted screenshot failed with "nothing to
capture").
"""

import asyncio
import base64
import json
import os
import socket
import subprocess
import time

import pytest
from websockets.asyncio.client import connect as ws_connect

import danvas

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


@pytest.fixture()
def hub():
    binary = _danvasd()
    if binary is None:
        pytest.skip("danvasd not built")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([binary, "--port", str(port)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    try:
        yield port
    finally:
        proc.kill()


def _wait(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_connect_and_disconnect_taps_fire_via_broker(hub, monkeypatch):
    # Dial into the fixture's broker instead of spawning one (the same path a
    # hot-reload worker takes), so the test owns the danvasd lifecycle.
    monkeypatch.setenv("_danvas_BROKER_PORT", str(hub))
    canvas = danvas.Canvas()
    canvas.label("hello")
    joined, left = [], []
    canvas.on_connect(joined.append)
    canvas.on_disconnect(left.append)
    canvas.serve(port=hub, open_browser=False, block=False)

    async def browse():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as ws:
            # Hold the socket open until the source has seen the join.
            await asyncio.get_event_loop().run_in_executor(
                None, _wait, lambda: joined)

    asyncio.run(asyncio.wait_for(browse(), timeout=15))
    assert joined, "on_connect never fired through the broker"
    assert joined[0].get("id"), joined
    assert _wait(lambda: left), "on_disconnect never fired through the broker"
    assert left[0].get("id") == joined[0].get("id"), (joined, left)


def test_get_image_shape_ids_are_namespaced_for_the_browser(hub, monkeypatch):
    # canvas.screenshot(target=panel) sends the source's own panel id
    # ("shape:<uuid>"); the browser's store holds the tag-namespaced form
    # ("shape:<tag>:<uuid>"), so the broker must rewrite the ids on relay —
    # exactly as it does for register/graveyard frames.
    monkeypatch.setenv("_danvas_BROKER_PORT", str(hub))
    canvas = danvas.Canvas()
    panel = canvas.toggle("t", options=["a", "b"])
    canvas.serve(port=hub, open_browser=False, block=False)

    fake_png = b"\x89PNG-not-really"
    seen = {}

    async def browse():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as ws:
            loop = asyncio.get_event_loop()
            # screenshot() refuses with no known viewer; the roster arrives
            # via presence, so wait for it before asking.
            await loop.run_in_executor(None, _wait, lambda: canvas.viewers)
            shot = loop.run_in_executor(
                None, lambda: canvas.screenshot(target=panel, timeout=10))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "get_image":
                    seen.update(msg)
                    await ws.send(json.dumps({
                        "type": "image", "reqId": msg["reqId"],
                        "data": base64.b64encode(fake_png).decode()}))
                    break
            assert await shot == fake_png

    asyncio.run(asyncio.wait_for(browse(), timeout=25))
    (sid,) = seen["shapeIds"]
    assert sid.startswith("shape:") and sid.endswith(f":{panel.id}"), sid
    assert sid != f"shape:{panel.id}", (
        "broker relayed the panel id un-namespaced — the browser store "
        "can't match it")
