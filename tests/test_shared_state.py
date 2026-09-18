"""Shared panel state (Custom / React): canvas.setState in the page,
panel.state in Python, converging through the set_props plane.

Native controls sync because their value is Python state. `state` gives
embedded panels the same slot: the page writes it (a set_props frame), the
owner applies + broadcasts, every viewer converges, late joiners get it in
the register frame, and Python sees it (panel.state / @on_state).
"""

import asyncio
import json
import os
import socket
import subprocess
import time

import pytest

import danvas

# -- Python side (no network) ---------------------------------------------------

def test_state_starts_empty_and_python_writes_broadcast():
    canvas = danvas.Canvas()
    p = canvas.custom(html="<p>x</p>")
    r = canvas.react("function Component(){return null}", name="r")
    assert p.state == {} and r.state == {}
    sent = []
    canvas._bridge.broadcast = lambda msg, **kw: sent.append(msg)
    p.set_state(a=1)
    p.state = {"b": 2}
    assert p.state == {"b": 2}
    assert [m["payload"] for m in sent if m.get("type") == "update"] == [
        {"state": {"a": 1}}, {"state": {"b": 2}}]
    assert p.register_props()["state"] == {"b": 2}          # late joiners
    r.set_state(k=1)
    assert r._compose_props({})["state"] == {"k": 1}
    with pytest.raises(TypeError):
        p.state = "nope"


def test_viewer_write_applies_through_set_props_and_fires_on_state():
    canvas = danvas.Canvas()
    p = canvas.custom(html="<p>x</p>")
    seen = []
    p.on_state(lambda s, viewer: seen.append((dict(s), viewer.get("id"))))
    p.on_state(lambda s: seen.append(("one-arg", dict(s))))
    canvas._bridge._apply_props(p, {"state": {"view": [1, 2]}}, {"id": "v9"})
    assert p.state == {"view": [1, 2]}
    assert seen == [({"view": [1, 2]}, "v9"), ("one-arg", {"view": [1, 2]})]
    # Python's own writes do NOT fire on_state (only viewers' do)
    seen.clear()
    p.set_state(view=[3])
    assert seen == []


def test_model3d_shared_camera_rides_state():
    canvas = danvas.Canvas()
    v = canvas.model3d("m", shared_camera=True)
    assert v.state == {"shared_camera": True}
    assert v.register_props()["state"] == {"shared_camera": True}
    assert canvas.model3d("n").state == {}


def test_export_html_carries_state():
    canvas = danvas.Canvas()
    p = canvas.custom(html="<p>x</p>")
    p.set_state(a=2)
    html = p.export_html()
    assert 'state:{"a": 2}' in html and "onState:function" in html


# -- through the broker: two fake browsers + Python ----------------------------

ws_connect = pytest.importorskip("websockets.asyncio.client").connect

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


def _wait(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


async def _until(ws, pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
        if isinstance(raw, bytes):
            continue
        m = json.loads(raw)
        if pred(m):
            return m
    raise AssertionError("frame never arrived")


def test_state_converges_across_browsers_and_python(hub, monkeypatch):
    monkeypatch.setenv("_danvas_BROKER_PORT", str(hub))
    canvas = danvas.Canvas()
    panel = canvas.custom(html="<p>x</p>", name="shared")
    panel.set_state(seed=1)
    written = []
    panel.on_state(lambda s, viewer: written.append((dict(s), viewer.get("id"))))
    joined = []
    canvas.on_connect(joined.append)
    canvas.serve(port=hub, open_browser=False, block=False)

    async def go():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as a, \
                ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as b:
            reg_a = await _until(a, lambda m: m.get("type") == "register" and m.get("name") == "shared")
            assert reg_a["props"]["state"] == {"seed": 1}        # replay carries it
            await _until(b, lambda m: m.get("type") == "register" and m.get("name") == "shared")
            await asyncio.get_event_loop().run_in_executor(None, _wait, lambda: len(joined) >= 2)
            # A writes the state (the page's canvas.setState -> set_props)
            await a.send(json.dumps({"type": "set_props", "id": reg_a["id"],
                                     "props": {"state": {"seed": 1, "view": [1, 2, 3]}}}))
            # B gets the owner's broadcast of the full state
            upd = await _until(b, lambda m: m.get("type") == "update"
                               and (m.get("payload") or {}).get("state") is not None)
            assert upd["payload"]["state"] == {"seed": 1, "view": [1, 2, 3]}
            # Python saw it, attributed to A
            await asyncio.get_event_loop().run_in_executor(None, _wait, lambda: written)
            # a late joiner opens at the written state
            async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as c:
                reg_c = await _until(c, lambda m: m.get("type") == "register" and m.get("name") == "shared")
                assert reg_c["props"]["state"] == {"seed": 1, "view": [1, 2, 3]}
            return [dict(v) for v in canvas.viewers]

    roster = asyncio.run(asyncio.wait_for(go(), timeout=30))
    assert panel.state == {"seed": 1, "view": [1, 2, 3]}
    assert written and written[0][0] == {"seed": 1, "view": [1, 2, 3]}
    assert written[0][1] == joined[0].get("id"), (written, joined, roster)
