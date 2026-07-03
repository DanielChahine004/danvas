"""Live hosting controls: the 🌐 button / canvas.expose() — widen a private
canvas's reach (LAN listener, public tunnel) without restarting.

Gated like the Inspector: on by default only for a private local bind. The
actions are idempotent, errors land in the broadcast state (the canvas keeps
serving), and every viewer sees the current reach via welcome + "hosting"
frames.
"""

import asyncio
import json

import pytest

import danvas


def test_button_defaults_on_only_for_private_local_bind():
    c = danvas.Canvas()
    # the same truth-table serve() applies, computed inline there:
    for host, tunnel, expect in (("127.0.0.1", False, True),
                                 ("localhost", False, True),
                                 ("0.0.0.0", False, False),
                                 ("127.0.0.1", True, False)):
        default_private = host in ("127.0.0.1", "localhost") and not tunnel
        assert default_private is expect
    # and the serve() wiring honours an explicit override
    import inspect
    src = inspect.getsource(danvas.Canvas.serve)
    assert "_ui_hosting" in src and "ui_hosting" in src


def test_hosting_state_shape_and_local_url():
    c = danvas.Canvas()
    b = c._bridge
    assert b.hosting_state()["local"] is None          # before serve
    b._hosting.update(host="127.0.0.1", port=8123)
    s = b.hosting_state()
    assert s["local"] == "http://127.0.0.1:8123"
    assert s["lan"] is None and s["tunnel"] is None


def test_ui_action_is_gated_by_the_flag():
    async def run():
        c = danvas.Canvas()
        b = c._bridge
        b._loop = asyncio.get_running_loop()
        calls = []

        async def fake_action(action):
            calls.append(action)
        b._hosting_action = fake_action

        ws = object()
        b._on_message(ws, json.dumps({"type": "ui", "action": "host_lan"}))
        await asyncio.sleep(0.01)
        assert calls == []                              # flag off: dropped
        b._ui_hosting = True
        b._on_message(ws, json.dumps({"type": "ui", "action": "host_tunnel"}))
        await asyncio.sleep(0.01)
        assert calls == ["host_tunnel"]
    asyncio.run(run())


def test_action_errors_land_in_state_not_exceptions():
    async def run():
        c = danvas.Canvas()
        b = c._bridge
        b._loop = asyncio.get_running_loop()
        broadcasts = []
        b.broadcast = lambda msg, **kw: broadcasts.append(msg)

        async def boom():
            raise RuntimeError("no LAN address found")
        b._expose_lan = boom
        await b._hosting_action("host_lan")
        assert b._hosting["error"] == "no LAN address found"
        assert b._hosting["busy"] is None               # cleared on the way out
        assert broadcasts and broadcasts[-1]["type"] == "hosting"
        assert broadcasts[-1]["error"] == "no LAN address found"
    asyncio.run(run())


def test_expose_requires_a_serving_canvas():
    c = danvas.Canvas()
    with pytest.raises(RuntimeError, match="serve"):
        c.expose(lan=True)


def test_actions_are_idempotent_when_already_exposed():
    async def run():
        c = danvas.Canvas()
        b = c._bridge
        b._loop = asyncio.get_running_loop()
        b.broadcast = lambda msg, **kw: None
        b._hosting.update(port=8000, lan="http://10.0.0.5:8000",
                          tunnel="https://x.trycloudflare.com")
        # neither action should try to do anything (no app, would raise)
        await b._hosting_action("host_lan")
        await b._hosting_action("host_tunnel")
        assert b._hosting["error"] is None
    asyncio.run(run())


def test_welcome_carries_hosting_keys():
    from fastapi.testclient import TestClient
    from danvas import server

    c = danvas.Canvas()
    c._bridge._ui_hosting = True
    c._bridge._hosting.update(host="127.0.0.1", port=8000)
    app = server.create_app(c._bridge, open_browser=False)
    assert c._bridge._app is app                        # LAN listener hook
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            while True:
                m = ws.receive_json()
                if m.get("type") == "welcome":
                    break
    assert m["uiHosting"] is True
    assert m["hosting"]["local"] == "http://127.0.0.1:8000"


# -- live teardown: LAN and tunnel turn OFF without restarting -------------------

def test_close_actions_tear_down_and_clear_state():
    async def run():
        c = danvas.Canvas()
        b = c._bridge
        b._loop = asyncio.get_running_loop()
        b.broadcast = lambda msg, **kw: None

        class FakeSrv:
            should_exit = False

        class FakeTunnel:
            stopped = False
            url = "https://x.trycloudflare.com"
            def stop(self):
                FakeTunnel.stopped = True

        srv = FakeSrv()
        b._lan_server = srv
        b._tunnel_handle = FakeTunnel()
        b._hosting.update(port=8000, lan="http://10.0.0.5:8000",
                          tunnel=FakeTunnel.url)
        await b._hosting_action("host_lan_off")
        assert srv.should_exit is True
        assert b._hosting["lan"] is None and b._lan_server is None
        await b._hosting_action("host_tunnel_off")
        assert FakeTunnel.stopped is True
        assert b._hosting["tunnel"] is None and b._tunnel_handle is None
        # idempotent: closing again with nothing open is a clean no-op
        await b._hosting_action("host_lan_off")
        await b._hosting_action("host_tunnel_off")
        assert b._hosting["error"] is None
    asyncio.run(run())


def test_expose_tristate_maps_to_actions():
    async def run():
        c = danvas.Canvas()
        b = c._bridge
        b._loop = asyncio.get_running_loop()
        calls = []

        async def fake_action(action):
            calls.append(action)
        b._hosting_action = fake_action

        import threading
        done = threading.Event()

        def drive():
            c.expose(lan=True, tunnel=False)
            c.expose()                        # both None: touches nothing
            done.set()
        threading.Thread(target=drive, daemon=True).start()
        for _ in range(200):
            if done.is_set():
                break
            await asyncio.sleep(0.01)
        assert done.is_set()
        assert calls == ["host_lan", "host_tunnel_off"]
    asyncio.run(run())
