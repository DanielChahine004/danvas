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


def test_input_handler_gets_the_documented_viewer_dict(hub, monkeypatch):
    # README: a handler's optional 2nd arg is {"id","name","color","device",
    # "role"}. Under the broker every input used to arrive with NO viewer
    # (the relay dropped the sender), so `viewer["role"]` KeyError'd in the
    # leaderboard example. The broker now stamps the sender's roster id on
    # the relayed frame and the source resolves it against the mirrored
    # roster, so the dict is populated exactly as under the embedded server.
    monkeypatch.setenv("_danvas_BROKER_PORT", str(hub))
    canvas = danvas.Canvas()
    slider = canvas.slider("gain", min=0, max=10, default=1)
    seen = []

    @slider.on_change
    def _(value, viewer):
        seen.append((value, dict(viewer)))

    joined = []
    canvas.on_connect(joined.append)
    canvas.serve(port=hub, open_browser=False, block=False)

    async def browse():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as ws:
            await asyncio.get_event_loop().run_in_executor(
                None, _wait, lambda: joined)
            # find the slider's namespaced id from the register frames
            sid = None
            deadline = time.monotonic() + 8
            while sid is None and time.monotonic() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=8)
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "register" and msg.get("name") == "gain":
                    sid = msg["id"]          # namespaced uuid; the name rides alongside
            assert sid, "slider never registered through the broker"
            await ws.send(json.dumps({"type": "input", "id": sid,
                                      "payload": {"value": 7}}))
            await asyncio.get_event_loop().run_in_executor(
                None, _wait, lambda: seen)

    asyncio.run(asyncio.wait_for(browse(), timeout=20))
    assert seen, "on_change never fired through the broker"
    value, viewer = seen[0]
    assert value == 7
    assert viewer.get("id") == joined[0].get("id"), (viewer, joined)
    for key in ("id", "name", "color", "device", "role"):
        assert key in viewer, (key, viewer)
    assert viewer["name"], viewer


async def _frames_until(ws, pred, timeout=8.0):
    """Collect text frames from a fake browser until `pred(frames)` holds."""
    frames = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
        except asyncio.TimeoutError:
            break
        if isinstance(raw, bytes):
            continue
        frames.append(json.loads(raw))
        if pred(frames):
            break
    return frames


def test_shapes_made_before_serve_replay_through_the_broker(hub, monkeypatch):
    # canvas.geo/draw/line before serve() only live in bridge._shapes; the
    # broker replay carried panels + arrows but NOT shapes, so the README's
    # own shapes example (and examples/managed_shapes.py) showed an empty
    # canvas under danvasd. All three kinds must reach a browser.
    monkeypatch.setenv("_danvas_BROKER_PORT", str(hub))
    canvas = danvas.Canvas()
    canvas.geo(x=40, y=40, w=160, h=100, geo="rectangle", name="box")
    canvas.draw([(0, 0), (10, 5), (20, 0)], color="red", name="ink")
    canvas.line([(40, 480), (120, 430), (200, 480)], name="zig")
    canvas.label("anchor")
    canvas.serve(port=hub, open_browser=False, block=False)

    async def browse():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as ws:
            return await _frames_until(
                ws, lambda fs: len([f for f in fs if f.get("type") == "shape"]) >= 3)

    frames = asyncio.run(asyncio.wait_for(browse(), timeout=15))
    shapes = {f["shapeType"]: f for f in frames if f.get("type") == "shape"}
    assert set(shapes) >= {"geo", "draw", "line"}, [f.get("type") for f in frames]
    # ink rides as tldraw-style segments; the frontend flattens them on ingest
    assert shapes["draw"]["props"]["segments"][0]["points"], shapes["draw"]
    assert len(shapes["line"]["props"]["points"]) == 3, shapes["line"]


def test_cursor_relays_between_browsers_and_onto_the_roster(hub, monkeypatch):
    # README: live cursors. The broker never handled `cursor` frames, so a
    # peer's pointer was never rendered and canvas.viewers[i]["cursor"] stayed
    # None under danvasd. Browser B moves; browser A must get a `cursor` frame
    # stamped with B's roster identity, and Python must see B's cursor.
    monkeypatch.setenv("_danvas_BROKER_PORT", str(hub))
    canvas = danvas.Canvas()
    canvas.label("hello")
    joined = []
    canvas.on_connect(joined.append)
    canvas.serve(port=hub, open_browser=False, block=False, cursors=True)

    async def browse():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as a, \
                ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as b:
            await asyncio.get_event_loop().run_in_executor(
                None, _wait, lambda: len(joined) >= 2)
            welcome_b = await _frames_until(b, lambda fs: any(f.get("type") == "welcome" for f in fs))
            assert any(f.get("cursors") is True for f in welcome_b if f.get("type") == "welcome"), welcome_b
            await b.send(json.dumps({"type": "cursor", "x": 123.0, "y": 45.0}))
            got = await _frames_until(a, lambda fs: any(f.get("type") == "cursor" for f in fs))
            cur = next(f for f in got if f.get("type") == "cursor")
            # Python's roster mirror carries it too (while B is still here).
            on_roster = await asyncio.get_event_loop().run_in_executor(
                None, _wait, lambda: any((v.get("cursor") or {}).get("x") == 123.0
                                         for v in canvas.viewers))
            return cur, on_roster, [dict(v) for v in canvas.viewers]

    cur, on_roster, roster = asyncio.run(asyncio.wait_for(browse(), timeout=20))
    assert cur["x"] == 123.0 and cur["y"] == 45.0, cur
    assert cur.get("name") and cur.get("color") and cur.get("id"), cur
    assert on_roster, roster
