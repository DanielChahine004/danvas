"""Phase 3 of the Rust SDK: interaction + multiuser.

Spawns danvasd + the danvas-source `interact` example and verifies, from a
browser: canvas.request() is answered by a Rust handler (request->response), and
the roster reaching the Rust peer makes it chat the headcount (presence + chat).
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
    for prof in ("release", "debug"):
        p = os.path.join(_ROOT, "broker", "target", prof,
                         "danvasd.exe" if os.name == "nt" else "danvasd")
        if os.path.exists(p):
            return p
    return None


def _interact_exe():
    exe = "interact.exe" if os.name == "nt" else "interact"
    for prof in ("release", "debug"):
        p = os.path.join(_ROOT, "danvas-source", "target", prof, "examples", exe)
        if os.path.exists(p):
            return p
    return None


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close()
    return p


@pytest.mark.skipif(_danvasd() is None or _interact_exe() is None,
                    reason="rust binaries not built (danvasd + interact example)")
def test_rust_answers_requests_and_tracks_roster():
    port = _free_port()
    broker = subprocess.Popen([_danvasd(), "--port", str(port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rust = None
    try:
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close(); break
            except OSError:
                time.sleep(0.1)
        # start the Rust peer first so it's the host source when the browser joins
        rust = subprocess.Popen([_interact_exe(), str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)

        async def go():
            u = f"ws://127.0.0.1:{port}/ws?vname=tester"
            ws = await ws_connect(u, max_size=None, max_queue=None)
            frames = []
            stop = asyncio.Event()

            async def drain():
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 0.5)
                    except (asyncio.TimeoutError, Exception):
                        if stop.is_set():
                            return
                        continue
                    if not isinstance(raw, bytes):
                        frames.append(json.loads(raw))

            task = asyncio.create_task(drain())

            async def wait(pred, t=15):
                end = asyncio.get_event_loop().time() + t
                while asyncio.get_event_loop().time() < end:
                    for m in frames:
                        if pred(m):
                            return m
                    await asyncio.sleep(0.05)
                raise AssertionError(f"not seen; saw {[m.get('type') for m in frames][-20:]}")

            try:
                reg = await wait(lambda m: m.get("type") == "register"
                                 and m.get("name") == "compute")
                # (1) request -> Rust handler -> response routed back to us
                await ws.send(json.dumps({"type": "request", "id": reg["id"],
                                          "reqId": "r1", "data": {"n": 21}}))
                resp = await wait(lambda m: m.get("type") == "response"
                                  and m.get("reqId") == "r1")
                assert resp["result"] == {"doubled": 42}

                # (2) our join reached the Rust peer's presence handler, which
                #     posted the headcount to chat — proving presence + chat.
                chat = await wait(lambda m: m.get("type") == "chat"
                                  and "roster now" in (m.get("text") or ""))
                assert chat["text"].startswith("roster now")
            finally:
                stop.set()
                task.cancel()
                await ws.close()

        asyncio.run(asyncio.wait_for(go(), timeout=45))
    finally:
        if rust is not None:
            rust.terminate()
        broker.terminate()
        for p in (rust, broker):
            if p is None:
                continue
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
