"""The language-neutral component templates + serve(broker=True).

components.json is what lets a non-Python SDK author NATIVE panels (the
register frame's React source + data defaults, extracted from the real
components). serve(broker=True) is the transplant: danvasd owns the port,
the Python process dials in as the host source.
"""

import asyncio
import json
import os
import socket
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import danvas
from danvas.source import SourceClient, _templates


def test_committed_asset_is_fresh():
    import gen_component_templates as gen
    committed = json.load(open(gen.OUT_PATH, encoding="utf-8"))
    assert committed == gen.build(), (
        "danvas/templates/components.json is stale — run "
        "python scripts/gen_component_templates.py")


def test_templates_match_the_real_components():
    tpl = _templates()["slider"]
    real = danvas.Slider().register_props_for(None, None)
    assert tpl["component"] == "React"
    assert tpl["props"]["source"] == real["source"]      # same JSX mounts
    assert tpl["data"]["max"] == 100


def test_register_template_builds_a_native_register():
    c = SourceClient(":8000", label="rig")
    sent = []
    c._send = lambda m: sent.append(m)
    c.register_template("temp", "slider", min=0, max=60, value=20, x=40, y=50)
    reg = sent[-1]
    assert reg["type"] == "register"
    assert reg["component"] == "React"                   # renders natively
    assert reg["name"] == "temp" and (reg["x"], reg["y"]) == (40, 50)
    blob = json.loads(reg["props"]["data"])
    assert (blob["min"], blob["max"], blob["value"]) == (0, 60, 20)
    assert "source" in reg["props"]                      # the mounting JSX


# -- serve(broker=True): the binary owns the port, Python dials in ---------------

def _danvasd():
    from danvas.remote import _find_danvasd
    return _find_danvasd()


@pytest.mark.skipif(_danvasd() is None, reason="danvasd binary not built")
def test_serve_broker_end_to_end():
    from websockets.asyncio.client import connect as ws_connect

    port = None
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    canvas = danvas.Canvas()
    servo = canvas.slider("servo", min=0, max=180, default=90)
    status = canvas.label("status", "idle")
    got = []
    servo.on_change(lambda v: (got.append(v), status.update(f"at {v}")))

    canvas.serve(broker=True, port=port, open_browser=False, block=False)
    try:
        # the broker serves the frontend...
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            assert r.status == 200

        async def go():
            async with ws_connect(f"ws://127.0.0.1:{port}/ws",
                                  max_size=None) as ws:
                reg = None
                while reg is None:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if m.get("type") == "register" and m.get("name") == "servo":
                        reg = m
                assert reg["owner"] == "host"
                # a browser input reaches the Python handler through the broker
                await ws.send(json.dumps({"type": "input", "id": reg["id"],
                                          "payload": {"value": 42}}))
                deadline = time.monotonic() + 5
                while not got and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                assert got == [42]
                # ...and the handler's update comes back out to the browser
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if m.get("type") == "update" and "at 42" in json.dumps(m):
                        break
        asyncio.run(asyncio.wait_for(go(), timeout=30))
    finally:
        canvas._broker.stop()


# -- the all-Rust stack: danvasd serves, a Rust program authors the canvas -------

def _rust_canvas_exe():
    root = os.path.join(os.path.dirname(__file__), "..")
    exe = "rust_canvas.exe" if os.name == "nt" else "rust_canvas"
    for profile in ("debug", "release"):
        p = os.path.abspath(os.path.join(root, "broker", "target", profile,
                                         "examples", exe))
        if os.path.exists(p):
            return p
    return None


@pytest.mark.skipif(_danvasd() is None or _rust_canvas_exe() is None,
                    reason="rust binaries not built")
def test_canvas_authored_in_rust_no_python_serving():
    import subprocess
    import urllib.request
    from websockets.asyncio.client import connect as ws_connect

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    broker = subprocess.Popen([_danvasd(), "--port", str(port)])
    rust = None
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.1)
        rust = subprocess.Popen([_rust_canvas_exe(), "--port", str(port)])

        async def go():
            async with ws_connect(f"ws://127.0.0.1:{port}/ws",
                                  max_size=None) as ws:
                reg = None
                end = time.monotonic() + 10
                while reg is None and time.monotonic() < end:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if m.get("type") == "register" and m.get("name") == "servo":
                        reg = m
                assert reg is not None
                assert reg["owner"] == "rust-canvas"      # authored in Rust
                assert reg["component"] == "React"        # renders natively
                blob = json.loads(reg["props"]["data"])
                assert blob["max"] == 180                 # template + overrides
                # browser drags the Rust slider -> Rust computes -> label updates
                await ws.send(json.dumps({"type": "input", "id": reg["id"],
                                          "payload": {"value": 77}}))
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if (m.get("type") == "update"
                            and "computed in rust" in json.dumps(m)
                            and "77" in json.dumps(m)):
                        break
        asyncio.run(asyncio.wait_for(go(), timeout=40))
        # the frontend itself is served by the Rust broker
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            assert r.status == 200
    finally:
        if rust is not None:
            rust.kill()
        broker.kill()


# -- the default flip: serve() prefers the broker ---------------------------------

def test_serve_auto_resolves_to_broker(monkeypatch):
    import danvas.remote as remote_mod
    calls = {}

    def fake_serve_via_broker(canvas, **kw):
        calls.update(kw)
        return canvas
    monkeypatch.setattr(remote_mod, "serve_via_broker", fake_serve_via_broker)
    # Force the broker to "resolve" regardless of whether a real binary is
    # present in this environment (the broker CI has one, a pure compat run
    # doesn't) — we're testing serve()'s routing, not binary discovery.
    monkeypatch.setattr(remote_mod, "_find_danvasd", lambda: "/fake/danvasd")

    c = danvas.Canvas()
    out = c.serve(port=1234, open_browser=False, block=False)   # plain serve()
    assert out is c and calls["port"] == 1234                   # broker path

    # embedded-only features fall back to the embedded server
    calls.clear()
    called_embedded = {}
    monkeypatch.setattr(
        danvas.Canvas, "_maybe_handoff_reload",
        lambda self, *a, **k: (_ for _ in ()).throw(SystemExit))
    c2 = danvas.Canvas()
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        c2.serve(persist=True, open_browser=False, block=False)
    assert not calls                                            # broker skipped

    # DANVAS_EMBEDDED force-disables
    monkeypatch.setenv("DANVAS_EMBEDDED", "1")
    c3 = danvas.Canvas()
    with _pytest.raises(SystemExit):
        c3.serve(open_browser=False, block=False)
    assert not calls


def test_serve_auto_falls_back_when_broker_wont_launch(monkeypatch):
    # A found-but-unlaunchable binary (wrong arch, corrupt) must NOT break
    # serve() in auto mode — it falls back to the embedded server.
    import danvas.remote as remote_mod
    from danvas.remote import _BrokerUnavailable

    def boom(canvas, **kw):
        raise _BrokerUnavailable("danvasd exited on startup (code 1)")
    monkeypatch.setattr(remote_mod, "serve_via_broker", boom)
    monkeypatch.setattr(remote_mod, "_find_danvasd", lambda: "/fake/danvasd")

    import pytest as _pytest
    # Sentinel for "fell through to the embedded server": stub its block=False
    # entry point (the handoff now runs BEFORE the broker branch, so it can't
    # be the sentinel any more).
    monkeypatch.setattr(
        danvas.Canvas, "_serve_background",
        lambda self, *a, **k: (_ for _ in ()).throw(SystemExit("embedded")))
    c = danvas.Canvas()
    with _pytest.warns(UserWarning, match="broker unavailable"):
        with _pytest.raises(SystemExit, match="embedded"):   # reached embedded
            c.serve(open_browser=False, block=False)

    # broker=True (explicit) instead surfaces the failure
    c2 = danvas.Canvas()
    with _pytest.raises(_BrokerUnavailable):
        c2.serve(broker=True, open_browser=False, block=False)
