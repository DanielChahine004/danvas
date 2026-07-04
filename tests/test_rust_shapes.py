"""Phase 2 of the Rust SDK: managed shapes + arrows.

Spawns danvasd + the danvas-source `shapes` example (two boxes, an arrow, a live
edit) and verifies the shape/arrow/shape_update frames cross the hub to a browser
with ids namespaced to the source — a Python-owned diagram authored from Rust.
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


def _shapes_exe():
    exe = "shapes.exe" if os.name == "nt" else "shapes"
    for prof in ("release", "debug"):
        p = os.path.join(_ROOT, "danvas-source", "target", prof, "examples", exe)
        if os.path.exists(p):
            return p
    return None


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close()
    return p


@pytest.mark.skipif(_danvasd() is None or _shapes_exe() is None,
                    reason="rust binaries not built (danvasd + shapes example)")
def test_rust_authors_shapes_and_arrow():
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

        async def go():
            u = f"ws://127.0.0.1:{port}/ws"
            obs = await ws_connect(u, max_size=None, max_queue=None)
            frames = []
            stop = asyncio.Event()

            async def drain():
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(obs.recv(), 0.5)
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
                await wait(lambda m: m.get("type") == "welcome")
                nonlocal rust
                rust = subprocess.Popen([_shapes_exe(), str(port)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # (1) both boxes arrive as shape frames, ids namespaced to source.
                a = await wait(lambda m: m.get("type") == "shape"
                               and (m.get("props") or {}).get("text") == "A")
                b = await wait(lambda m: m.get("type") == "shape"
                               and (m.get("props") or {}).get("text") == "B")
                assert a["shapeType"] == "geo" and a["props"]["color"] == "blue"
                assert a["id"] != "box_a"                 # namespaced
                a_id, b_id = a["id"], b["id"]

                # (2) the arrow binds the two composed ids.
                arr = await wait(lambda m: m.get("type") == "arrow")
                assert arr["start"] == a_id and arr["end"] == b_id
                assert arr["props"]["text"] == "A->B"

                # (3) the live edit relays as a shape_update on box A.
                upd = await wait(lambda m: m.get("type") == "shape_update"
                                 and m.get("id") == a_id
                                 and (m.get("props") or {}).get("color") == "orange")
                assert upd.get("x") == 80.0
            finally:
                stop.set()
                task.cancel()
                await obs.close()

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
