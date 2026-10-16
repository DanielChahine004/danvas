"""read_only / observer views, define() replay, describe sizes, settled and
the screenshot URL — through the real broker, with a fake browser socket.

read_only used to block only freehand ink: a read-only viewer could still
move, resize, delete and restore any panel it could see (and per-role /
per-client set_view collapsed to a global view under the broker). observer
additionally makes every panel inert. Both are enforced by the broker, not
just hidden in the browser, so a forged frame is what these tests send.
"""

import asyncio
import base64
import json
import time
import urllib.error
import urllib.request

import pytest
from websockets.asyncio.client import connect as ws_connect

import danvas
from test_broker_taps import hub, _wait  # noqa: F401  (fixture re-export)


def _register_for(frames, name):
    for f in frames:
        if f.get("type") == "register" and f.get("name") == name:
            return f
    return None


async def _drain(ws, frames, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, end - time.monotonic()))
        except asyncio.TimeoutError:
            break
        if isinstance(raw, str):
            frames.append(json.loads(raw))


def _serve(canvas, port, monkeypatch, **kw):
    monkeypatch.setenv("_danvas_BROKER_PORT", str(port))
    canvas.serve(port=port, open_browser=False, block=False, **kw)


def test_read_only_blocks_moves_deletes_but_keeps_fit_reports(hub, monkeypatch):
    canvas = danvas.Canvas()
    fixed = canvas.react(name="fixed", jsx="<div/>", x=10, y=20, w=200, h=150)
    auto = canvas.react(name="auto", jsx="<div/>", x=300, y=20, w=200)
    _serve(canvas, hub, monkeypatch, view={"read_only": True})

    async def browse():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as ws:
            frames = []
            await _drain(ws, frames, 1.5)
            welcome = frames[0]
            assert welcome["view"]["read_only"] is True
            fid = _register_for(frames, "fixed")["id"]
            aid = _register_for(frames, "auto")["id"]
            # forged user move + resize of a fixed panel, and a delete
            await ws.send(json.dumps({"type": "layout", "id": fid, "x": 999, "y": 999, "w": 50}))
            await ws.send(json.dumps({"type": "graveyard", "id": fid}))
            # a "fit" claim on a fixed axis is dropped too
            await ws.send(json.dumps({"type": "layout", "id": fid, "h": 77, "fit": True}))
            # a real content-fit report on an auto panel lands
            await ws.send(json.dumps({"type": "layout", "id": aid, "h": 432, "fit": True}))
            await _drain(ws, frames, 1.0)

    asyncio.run(asyncio.wait_for(browse(), timeout=20))
    assert _wait(lambda: auto.h == 432), auto.h
    assert (fixed.x, fixed.y, fixed.w, fixed.h) == (10, 20, 200, 150)
    assert fixed in canvas.components, "a read-only viewer deleted a panel"
    row = {r["name"]: r for r in canvas.describe()}["auto"]
    assert row["measured_by"] and row["measured_at"], row


def test_observer_inert_and_role_scoped_view(hub, monkeypatch):
    canvas = danvas.Canvas()
    clicks = []
    btn = canvas.button("go", x=0, y=0)
    btn.on_click(lambda *a: clicks.append(1))
    _serve(canvas, hub, monkeypatch)

    async def browse():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as a, \
                ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as b:
            fa, fb = [], []
            await asyncio.gather(_drain(a, fa, 1.5), _drain(b, fb, 1.5))
            bid = _register_for(fa, "go")["id"]
            vid_a = fa[0]["you"]["id"]
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _wait, lambda: len(canvas.viewers) >= 2)
            # client-scoped observer: only viewer A
            canvas.set_view(observer=True, client_id=vid_a)
            await asyncio.gather(_drain(a, fa, 0.8), _drain(b, fb, 0.8))
            assert any(f.get("type") == "view" and f["view"].get("observer") for f in fa)
            assert not any(f.get("type") == "view" and f["view"].get("observer") for f in fb), \
                "a client-scoped view reached another viewer"
            payload = {"type": "input", "id": bid, "payload": {"click": True}}
            await a.send(json.dumps(payload))        # observer: dropped
            await asyncio.sleep(0.5)
            assert clicks == [], "an observer's click reached the handler"
            await b.send(json.dumps(payload))        # normal viewer: lands
            await loop.run_in_executor(None, _wait, lambda: clicks)

    asyncio.run(asyncio.wait_for(browse(), timeout=25))
    assert clicks == [1]


def test_define_before_serve_reaches_browsers(hub, monkeypatch):
    canvas = danvas.Canvas()
    canvas.define("useSel", "function useSel(){ return 1 }")
    canvas.style(".x{color:red}")
    canvas.react(name="r", jsx="<div>{useSel()}</div>")
    _serve(canvas, hub, monkeypatch)

    async def browse():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as ws:
            frames = []
            await _drain(ws, frames, 1.5)
            return frames

    frames = asyncio.run(asyncio.wait_for(browse(), timeout=15))
    kinds = [f.get("type") for f in frames]
    assert "shared" in kinds, kinds
    shared = frames[kinds.index("shared")]
    assert "useSel" in shared["components"] and ".x" in shared["styles"]
    assert shared.get("source"), "hub should tag shared frames with their source"
    reg = next(i for i, f in enumerate(frames) if f.get("type") == "register")
    assert kinds.index("shared") < reg, "shared must arrive before the panels using it"


def test_describe_sizes_settled_and_screenshot_url(hub, monkeypatch):
    canvas = danvas.Canvas()
    canvas.react(name="r", jsx="<div/>", x=5, y=6, w=210, h=120)
    _serve(canvas, hub, monkeypatch)
    base = f"http://127.0.0.1:{hub}"
    rows = json.load(urllib.request.urlopen(base + "/__describe__"))["components"]
    row = next(r for r in rows if r["name"] == "r")
    assert (row["w"], row["h"]) == (210, 120), row
    # no browser yet: the screenshot URL says so instead of serving HTML
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/__screenshot__.png")
    assert e.value.code == 503
    fake = b"\x89PNG fake"

    async def browse():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as ws:
            await _drain(ws, [], 1.0)
            loop = asyncio.get_event_loop()
            waiter = loop.run_in_executor(None, canvas.wait_settled, 10)
            shot = loop.run_in_executor(
                None, lambda: urllib.request.urlopen(base + "/__screenshot__.png").read())
            answered = set()
            while len(answered) < 2:
                f = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if f.get("type") == "settle_check" and "settle" not in answered:
                    await ws.send(json.dumps({"type": "settled", "pending": []}))
                    answered.add("settle")
                if f.get("type") == "get_image" and "img" not in answered:
                    await ws.send(json.dumps({"type": "image", "reqId": f["reqId"],
                                              "data": base64.b64encode(fake).decode()}))
                    answered.add("img")
            return await waiter, await shot

    report, png = asyncio.run(asyncio.wait_for(browse(), timeout=20))
    assert report is not None and report["pending"] == [], report
    assert png == fake


# -- per-viewer panel content through the broker ------------------------------
# panel.update(roles=)/update(client_id=) used to collapse to the shared frame
# under the broker: every viewer got every "private" update, and a reconnect
# replayed only the shared props.

@pytest.fixture()
def roles_broker():
    import os
    import socket
    import subprocess
    from test_broker_taps import _danvasd
    binary = _danvasd()
    if binary is None:
        pytest.skip("danvasd not built")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = dict(os.environ, DANVAS_ROLE_PASSWORDS="red=r1,blue=b1")
    proc = subprocess.Popen([binary, "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def _session(port, password):
    from test_conformance import _login
    tok = _login(port, password)
    assert tok, "role login failed"
    return {"Cookie": f"pc_session={tok}"}


def _data_updates(frames, pid):
    out = []
    for f in frames:
        if f.get("type") == "update" and f.get("id") == pid and "data" in (f.get("payload") or {}):
            out.append(json.loads(f["payload"]["data"]))
    return out


def test_scoped_updates_reach_only_their_viewers_and_replay(roles_broker, monkeypatch):
    port = roles_broker
    canvas = danvas.Canvas()
    panel = canvas.react(name="p", jsx="<div/>", props={"shared": 1})
    monkeypatch.setenv("_danvas_BROKER_PORT", str(port))
    canvas.serve(port=port, open_browser=False, block=False,
                 passwords={"red": "r1", "blue": "b1"})
    red, blue = _session(port, "r1"), _session(port, "b1")
    url = f"ws://127.0.0.1:{port}/ws"

    async def browse():
        async with ws_connect(url, additional_headers=red, max_size=None) as a, \
                ws_connect(url, additional_headers=blue, max_size=None) as b:
            fa, fb = [], []
            await asyncio.gather(_drain(a, fa, 1.5), _drain(b, fb, 1.5))
            pid = _register_for(fa, "p")["id"]
            a_id = fa[0]["you"]["id"]
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _wait, lambda: len(canvas.viewers) >= 2)
            panel.update(roles="red", team="red-only")
            panel.update(client_id=a_id, me="just-a")
            await asyncio.gather(_drain(a, fa, 1.0), _drain(b, fb, 1.0))
            got_a, got_b = _data_updates(fa, pid), _data_updates(fb, pid)
            assert any(d.get("team") == "red-only" for d in got_a), got_a
            assert any(d.get("me") == "just-a" for d in got_a), got_a
            assert not got_b, f"blue saw red/client-scoped content: {got_b}"
            # a NEW red browser replays the red overlay — not A's private one
            async with ws_connect(url, additional_headers=red, max_size=None) as c, \
                    ws_connect(url, additional_headers=blue, max_size=None) as d:
                fc, fd = [], []
                await asyncio.gather(_drain(c, fc, 1.5), _drain(d, fd, 1.5))
                got_c = _data_updates(fc, pid)
                assert any(x.get("team") == "red-only" for x in got_c), got_c
                assert not any("me" in x for x in got_c), got_c
                assert not _data_updates(fd, pid), _data_updates(fd, pid)

    asyncio.run(asyncio.wait_for(browse(), timeout=30))
