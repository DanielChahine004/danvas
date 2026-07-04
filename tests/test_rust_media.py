"""Phase 1 of the Rust SDK: binary media over the wire, both directions.

Spawns danvasd + the danvas-source `media` example (a Rust source that streams a
native video panel and echoes any binary a browser sends). Verifies the binary
envelope crosses the hub from Rust to the browser AND back — the protocol's
media layer, driven from Rust.
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


def _media_exe():
    exe = "media.exe" if os.name == "nt" else "media"
    for prof in ("release", "debug"):
        p = os.path.join(_ROOT, "danvas-source", "target", prof, "examples", exe)
        if os.path.exists(p):
            return p
    return None


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close()
    return p


def _envelope(code, cid, payload):
    idb = cid.encode()
    return bytes([code, len(idb)]) + idb + payload


def _parse(data):
    code, idlen = data[0], data[1]
    return code, data[2:2 + idlen].decode(), data[2 + idlen:]


@pytest.mark.skipif(_danvasd() is None or _media_exe() is None,
                    reason="rust binaries not built (danvasd + media example)")
def test_rust_streams_and_receives_binary_media():
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
            observer = await ws_connect(u, max_size=None, max_queue=None)
            frames, blobs = [], []
            stop = asyncio.Event()

            async def drain():
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(observer.recv(), 0.5)
                    except (asyncio.TimeoutError, Exception):
                        if stop.is_set():
                            return
                        continue
                    (blobs if isinstance(raw, bytes) else frames).append(raw)

            task = asyncio.create_task(drain())
            try:
                # welcome first
                async def wait_json(pred, t=15):
                    end = asyncio.get_event_loop().time() + t
                    while asyncio.get_event_loop().time() < end:
                        for f in frames:
                            m = json.loads(f)
                            if pred(m):
                                return m
                        await asyncio.sleep(0.05)
                    raise AssertionError(f"json not seen; saw {[json.loads(f).get('type') for f in frames][-15:]}")

                async def wait_blob(pred, t=15):
                    end = asyncio.get_event_loop().time() + t
                    while asyncio.get_event_loop().time() < end:
                        for b in blobs:
                            if pred(b):
                                return b
                        await asyncio.sleep(0.05)
                    raise AssertionError(f"blob not seen; have {len(blobs)} blobs")

                nonlocal rust
                rust = subprocess.Popen([_media_exe(), str(port)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # (1) Rust registered a NATIVE video panel (React component).
                reg = await wait_json(lambda m: m.get("type") == "register"
                                      and m.get("name") == "cam")
                assert reg["component"] == "React"
                assert reg["owner"] == "media-rust"
                cam_id = reg["id"]                       # composed id (s0:cam)

                # (2) Rust STREAMED a JPEG frame — the browser gets binary code 1
                #     under the composed id.
                got = await wait_blob(lambda b: _parse(b)[0] == 1
                                      and _parse(b)[1] == cam_id)
                code, cid, payload = _parse(got)
                assert code == 1 and cid == cam_id
                assert payload[:3] == b"\xff\xd8\xff"    # JPEG SOI marker

                # (3) Browser sendBinary()s to the panel (code INPUT=5) -> the
                #     Rust on_binary handler echoes it back as a video frame.
                marker = b"\xff\xd8\xff\x42echo-me"
                await observer.send(_envelope(5, cam_id, marker))
                await wait_blob(lambda b: _parse(b)[0] == 1
                                and _parse(b)[2] == marker, t=15)
            finally:
                stop.set()
                task.cancel()
                await observer.close()

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
