"""The serve_config frame: an owner's UI gating reaches a pre-spawned broker.

Under hot reload the monitor spawns danvasd BEFORE the script runs, so the
broker can't be given serve()'s kwargs on its command line — the host source
delivers its resolved UI-affordance gating (Inspector button, graveyard,
cursors, hosting) as a serve_config frame on dial-in instead. A browser
connecting afterwards must see the flags in its welcome.
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
    """A bare danvasd, exactly as the hot-reload monitor spawns it (no UI flags)."""
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


async def _welcome(port):
    async with ws_connect(f"ws://127.0.0.1:{port}/ws", max_size=None) as browser:
        while True:
            raw = await asyncio.wait_for(browser.recv(), timeout=8)
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("type") == "welcome":
                return msg


def test_serve_config_reaches_an_already_connected_browser(hub):
    """A hot-reload browser outlives the worker, so its welcome predates the
    flags — the hub must relay serve_config to it live (no F5)."""
    async def go():
        async with ws_connect(f"ws://127.0.0.1:{hub}/ws", max_size=None) as browser:
            # Drain until the welcome so the session is fully established.
            while True:
                raw = await asyncio.wait_for(browser.recv(), timeout=8)
                if not isinstance(raw, bytes) and \
                        json.loads(raw).get("type") == "welcome":
                    break
            async with ws_connect(
                    f"ws://127.0.0.1:{hub}/ws?source=1&label=host",
                    max_size=None) as src:
                await src.send(json.dumps(
                    {"type": "serve_config", "uiInspector": True}))
                deadline = asyncio.get_event_loop().time() + 8
                while True:
                    raw = await asyncio.wait_for(browser.recv(), timeout=8)
                    if isinstance(raw, bytes):
                        continue
                    msg = json.loads(raw)
                    if msg.get("type") == "serve_config":
                        assert msg.get("uiInspector") is True
                        return
                    assert asyncio.get_event_loop().time() < deadline

    asyncio.run(go())


def test_serve_config_updates_the_welcome_gating(hub):
    async def go():
        # A bare broker defaults every affordance off.
        before = await _welcome(hub)
        assert before.get("uiInspector") is not True

        async with ws_connect(f"ws://127.0.0.1:{hub}/ws?source=1&label=host",
                              max_size=None) as src:
            await src.send(json.dumps({
                "type": "serve_config", "uiInspector": True,
                "uiGraveyard": True, "cursors": True}))
            await asyncio.sleep(0.3)
            after = await _welcome(hub)
            assert after.get("uiInspector") is True
            assert after.get("uiGraveyard") is True
            assert after.get("cursors") is True

    asyncio.run(go())
