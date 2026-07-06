"""Two languages, one canvas — the polyglot proof.

danvasd (Rust broker) + a Python peer that owns a panel + the danvas-rust
Rust SDK example, all on one canvas. Verifies the Rust process does the three
things that make it a first-class peer, across the language line:

  1. authors a NATIVE panel (renders like a Python one)
  2. edits a PEER's panel by name (the shared property plane)
  3. subscribes to a peer's events and reacts (in Rust)

Skipped unless both Rust binaries are built (danvasd + the two_languages
example).
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


def _rust_example():
    exe = "two_languages.exe" if os.name == "nt" else "two_languages"
    for prof in ("release", "debug"):
        p = os.path.join(_ROOT, "danvas-rust", "target", prof, "examples", exe)
        if os.path.exists(p):
            return p
    return None


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close()
    return p


@pytest.mark.skipif(_danvasd() is None or _rust_example() is None,
                    reason="rust binaries not built (danvasd + two_languages)")
def test_rust_peer_authors_edits_and_reacts():
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
            owner = await ws_connect(f"{u}?source=1&label=pyowner", max_size=None)
            observer = await ws_connect(u, max_size=None)
            owner_frames, obs_frames = [], []
            stop = asyncio.Event()

            async def drain(ws, out):
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 0.5)
                    except (asyncio.TimeoutError, Exception):
                        if stop.is_set():
                            return
                        continue
                    if not isinstance(raw, bytes):
                        out.append(json.loads(raw))

            async def wait_for(out, pred, timeout=15):
                end = asyncio.get_event_loop().time() + timeout
                while asyncio.get_event_loop().time() < end:
                    for m in out:
                        if pred(m):
                            return m
                    await asyncio.sleep(0.05)
                raise AssertionError(f"not seen; saw {[m.get('type') for m in out][-20:]}")

            dtasks = [asyncio.create_task(drain(owner, owner_frames)),
                      asyncio.create_task(drain(observer, obs_frames))]
            try:
                await wait_for(owner_frames, lambda m: m.get("type") == "welcome")
                await owner.send(json.dumps({"type": "register", "id": "peer",
                                             "name": "peer", "component": "React",
                                             "props": {"data": '{"min":0,"max":100,"value":5}'}}))
                await asyncio.sleep(0.4)
                nonlocal rust
                rust = subprocess.Popen([_rust_example(), str(port)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # (1) Rust authored a NATIVE panel
                servo = await wait_for(obs_frames, lambda m: m.get("type") == "register"
                                       and m.get("name") == "rust_servo")
                assert servo["component"] == "React"
                assert servo["owner"] == "rust-peer"

                # (2) Rust EDITED the Python peer's panel by name (shared plane)
                sp = await wait_for(owner_frames, lambda m: m.get("type") == "set_props"
                                    and m.get("id") == "peer")
                assert sp["props"]["max"] == 42

                # (3) Rust SUBSCRIBED + reacts
                peer_reg = await wait_for(obs_frames, lambda m: m.get("type") == "register"
                                          and m.get("name") == "peer")
                sreg = await wait_for(obs_frames, lambda m: m.get("type") == "register"
                                      and m.get("name") == "rust_status")
                # (subscribe may still be in flight — resend input until the
                # Rust reaction lands)
                reacted = None
                for _ in range(20):
                    await observer.send(json.dumps(
                        {"type": "input", "id": peer_reg["id"], "payload": {"value": 9}}))
                    await asyncio.sleep(0.3)
                    reacted = next((m for m in obs_frames
                                    if m.get("type") == "update"
                                    and m.get("id") == sreg["id"]
                                    and "reacted to peer in rust" in json.dumps(m)), None)
                    if reacted:
                        break
                assert reacted, "Rust subscription never reacted to the peer's input"
            finally:
                stop.set()
                for t in dtasks:
                    t.cancel()
                await owner.close()
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
