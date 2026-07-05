"""danvasd replay semantics for the streaming-figure channel.

The hub caches update payloads merged by key; `plot`/`plot_extend` need more
than that (PROTOCOL.md § update-payload vocabulary): a full `plot` supersedes
any pending delta, and deltas fold INTO the cached figure — otherwise a
late-joining browser replays a stale figure plus one dangling delta, and a
reconnecting one double-applies the last point.
"""

import asyncio
import json
import os
import socket
import subprocess
import time

import pytest
from websockets.asyncio.client import connect as ws_connect

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


def test_plot_extend_folds_into_replayed_figure(hub):
    async def go():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws?source=1&label=lp",
                              max_size=None) as src:
            await src.send(json.dumps({
                "type": "register", "id": "live", "name": "live",
                "component": "React", "props": {"source": "x"}}))
            await src.send(json.dumps({
                "type": "update", "id": "live", "payload": {"plot": {
                    "data": [{"type": "scatter", "x": [1], "y": [10]}],
                    "layout": {}}}}))
            for i in (2, 3):
                await src.send(json.dumps({
                    "type": "update", "id": "live", "payload": {
                        "plot_extend": {"indices": [0], "x": [[i]],
                                        "y": [[i * 10]], "max": 300}}}))
            await asyncio.sleep(0.5)
            # A late joiner must replay ONE complete figure, no dangling delta.
            async with ws_connect(f"ws://127.0.0.1:{hub}/ws",
                                  max_size=None) as browser:
                plot, saw_extend = None, False
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        raw = await asyncio.wait_for(
                            browser.recv(),
                            timeout=deadline - time.monotonic())
                    except asyncio.TimeoutError:
                        break
                    if isinstance(raw, bytes):
                        continue
                    m = json.loads(raw)
                    if m.get("type") != "update":
                        continue
                    payload = m.get("payload") or {}
                    if "plot" in payload:
                        plot = payload["plot"]
                    if "plot_extend" in payload:
                        saw_extend = True
                    if plot is not None:
                        break
                assert plot is not None, "no figure replayed"
                assert not saw_extend, (
                    "replay carried a dangling plot_extend delta")
                trace = plot["data"][0]
                assert trace["x"] == [1, 2, 3], trace
                assert trace["y"] == [10, 20, 30], trace

    asyncio.run(asyncio.wait_for(go(), timeout=30))


def test_full_plot_supersedes_pending_delta(hub):
    async def go():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws?source=1&label=lp",
                              max_size=None) as src:
            await src.send(json.dumps({
                "type": "register", "id": "live", "name": "live",
                "component": "React", "props": {"source": "x"}}))
            # A delta with no prior figure is kept (partial beats empty)…
            await src.send(json.dumps({
                "type": "update", "id": "live", "payload": {
                    "plot_extend": {"indices": [0], "x": [[9]], "y": [[9]],
                                    "max": 300}}}))
            # …then a full figure supersedes it.
            await src.send(json.dumps({
                "type": "update", "id": "live", "payload": {"plot": {
                    "data": [{"type": "scatter", "x": [5], "y": [50]}],
                    "layout": {}}}}))
            await asyncio.sleep(0.5)
            async with ws_connect(f"ws://127.0.0.1:{hub}/ws",
                                  max_size=None) as browser:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        raw = await asyncio.wait_for(
                            browser.recv(),
                            timeout=deadline - time.monotonic())
                    except asyncio.TimeoutError:
                        break
                    if isinstance(raw, bytes):
                        continue
                    m = json.loads(raw)
                    if m.get("type") != "update":
                        continue
                    payload = m.get("payload") or {}
                    assert "plot_extend" not in payload, (
                        "a full plot must supersede the pending delta")
                    if "plot" in payload:
                        assert payload["plot"]["data"][0]["x"] == [5]
                        return
                raise AssertionError("no figure replayed")

    asyncio.run(asyncio.wait_for(go(), timeout=30))
