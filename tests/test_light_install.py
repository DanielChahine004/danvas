"""The light-install invariant: serving is the danvasd binary and both serve()
and danvas.connect() dial in over the websockets *client*, so the base package
must import — and a Canvas must build + broker-serve — with NO server stack
(fastapi / uvicorn / starlette) installed. The FastAPI hub is an optional extra
(danvas[hub]) used only to run `python -m danvas.merge`.

Run in a subprocess with an import blocker so a stray top-level server-stack
import anywhere in the base chain fails loudly here rather than silently
inflating everyone's install.
"""

import subprocess
import sys
import textwrap


_BLOCKER = textwrap.dedent("""
    import sys, importlib.abc
    BLOCK = {"fastapi", "uvicorn", "starlette"}
    class _Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name.split(".")[0] in BLOCK:
                raise ImportError("server stack blocked in light-install test: " + name)
            return None
    sys.meta_path.insert(0, _Blocker())
""")


def _run(body):
    code = _BLOCKER + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True)


def test_base_import_and_client_chain_need_no_server_stack():
    r = _run("""
        import danvas
        from danvas import SourceClient, connect, RemoteCanvas
        import danvas.source, danvas.remote, danvas.bridge, danvas.canvas, danvas._dialin, danvas._net
        c = danvas.Canvas()
        c.slider("s", 0, 10, 5); c.label("l", "hi")
        loaded = [m for m in sys.modules if m.split('.')[0] in {"fastapi","uvicorn","starlette"}]
        assert not loaded, f"server stack leaked into the base chain: {loaded}"
        print("OK")
    """)
    assert r.returncode == 0, f"light import failed:\n{r.stdout}\n{r.stderr}"
    assert "OK" in r.stdout


def test_broker_serve_needs_no_server_stack():
    # The headline: a real broker serve() end to end with the server stack
    # blocked. Skipped where danvasd isn't built (a pure-Python compat runner).
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exe = "danvasd.exe" if os.name == "nt" else "danvasd"
    have = any(os.path.exists(os.path.join(root, "broker", "target", p, exe))
               for p in ("release", "debug"))
    if not have:
        import pytest
        pytest.skip("danvasd binary not built")

    r = _run("""
        import socket, json, asyncio
        import danvas
        from websockets.asyncio.client import connect as ws_connect
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        c = danvas.Canvas(); c.slider("servo", 0, 180, 90)
        c.serve(broker=True, port=port, open_browser=False, block=False)
        async def go():
            async with ws_connect(f"ws://127.0.0.1:{port}/ws", max_size=None, max_queue=None) as ws:
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if m.get("type") == "register" and m.get("name") == "servo":
                        return
        try:
            asyncio.run(asyncio.wait_for(go(), timeout=20))
        finally:
            c._broker.stop()
        loaded = [m for m in sys.modules if m.split('.')[0] in {"fastapi","uvicorn","starlette"}]
        assert not loaded, f"server stack leaked during broker serve: {loaded}"
        print("OK")
    """)
    assert r.returncode == 0, f"broker serve without server stack failed:\n{r.stdout}\n{r.stderr}"
    assert "OK" in r.stdout
