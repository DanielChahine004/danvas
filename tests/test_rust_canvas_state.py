"""Phase 4 of the Rust SDK: canvas state.

Spawns danvasd + the danvas-rust `canvas_state` example and verifies, from a
browser: shared React assets (define/style), the camera baked into welcome
(set_view), z-order, free-form ink reaching on_draw, and a get_snapshot round
trip — all driven from Rust.
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


def _state_exe():
    exe = "canvas_state.exe" if os.name == "nt" else "canvas_state"
    for prof in ("release", "debug"):
        p = os.path.join(_ROOT, "danvas-rust", "target", prof, "examples", exe)
        if os.path.exists(p):
            return p
    return None


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close()
    return p


@pytest.mark.skipif(_danvasd() is None or _state_exe() is None,
                    reason="rust binaries not built (danvasd + canvas_state example)")
def test_rust_drives_canvas_state():
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
        rust = subprocess.Popen([_state_exe(), str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)          # let it set view/shared before the browser joins

        async def go():
            u = f"ws://127.0.0.1:{port}/ws"
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
                raise AssertionError(f"not seen; saw {[m.get('type') for m in frames][-25:]}")

            try:
                # (1) welcome bakes in the Rust-set camera (set_view).
                w = await wait(lambda m: m.get("type") == "welcome")
                assert (w.get("view") or {}).get("zoom") == 1.5
                assert (w.get("view") or {}).get("locked") is True

                # (2) shared React assets (define/style).
                sh = await wait(lambda m: m.get("type") == "shared"
                                and "Pill" in (m.get("components") or {}))
                assert ".pill" in sh["styles"]

                # (3) z-order for the header label.
                await wait(lambda m: m.get("type") == "order" and m.get("op") == "front")

                # (4) free-form ink reaches the Rust on_draw -> it chats.
                await ws.send(json.dumps({"type": "draw", "diff": {
                    "added": {"k1": {"id": "k1", "x": 1, "props": {}}},
                    "updated": {}, "removed": {}}}))
                await wait(lambda m: m.get("type") == "chat"
                           and (m.get("text") or "") == "someone drew")

                # (5) snapshot round-trip: tap "snap" -> Rust asks us for the doc
                #     -> we reply -> Rust chats the record count.
                snap = await wait(lambda m: m.get("type") == "register"
                                  and m.get("name") == "snap")
                await ws.send(json.dumps({"type": "input", "id": snap["id"],
                                          "payload": {"clicks": 1}}))
                req = await wait(lambda m: m.get("type") == "get_snapshot")
                await ws.send(json.dumps({"type": "snapshot", "reqId": req["reqId"],
                                          "data": {"records": [1, 2, 3]}}))
                await wait(lambda m: m.get("type") == "chat"
                           and (m.get("text") or "") == "snapshot has 3 records")
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
