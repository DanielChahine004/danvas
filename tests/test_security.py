"""Access control: the serve password gate and the live Repl insert gate."""

import pytest
from fastapi.testclient import TestClient

import danvas
from danvas import server
from danvas.components import Repl


def _app(password):
    canvas = danvas.Canvas()
    canvas.insert(danvas.Slider("servo", min=0, max=180, default=90))
    return server.create_app(canvas._bridge, open_browser=False, password=password)


def test_no_password_serves_openly():
    app = _app(None)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200


def test_password_blocks_unauthenticated_http():
    app = _app("hunter2")
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 401
        assert "password protected" in r.text.lower()


def test_wrong_password_rejected():
    app = _app("hunter2")
    with TestClient(app) as client:
        r = client.post("/__auth__", data={"password": "nope"},
                        follow_redirects=False)
        assert r.status_code == 401


def test_correct_password_grants_access():
    app = _app("hunter2")
    with TestClient(app) as client:
        r = client.post("/__auth__", data={"password": "hunter2"},
                        follow_redirects=False)
        assert r.status_code == 303
        assert server._AUTH_COOKIE in r.cookies
        # The client now holds the session cookie, so the canvas loads.
        assert client.get("/").status_code == 200


def test_password_blocks_unauthenticated_websocket():
    app = _app("hunter2")
    with TestClient(app) as client:
        with pytest.raises(Exception):
            # No session cookie -> the server closes the socket with 1008.
            with client.websocket_connect("/ws"):
                pass


def test_authenticated_websocket_connects():
    app = _app("hunter2")
    with TestClient(app) as client:
        client.post("/__auth__", data={"password": "hunter2"},
                    follow_redirects=False)
        with client.websocket_connect("/ws") as ws:
            # A welcome frame proves the connection was accepted.
            assert any(ws.receive_json().get("type") == "welcome"
                       for _ in range(12))


def test_live_repl_insert_refused_on_public_bind():
    canvas = danvas.Canvas()
    canvas.enable_repl({})
    # Simulate a running server bound publicly without the remote-exec opt-in.
    canvas._serving = True
    canvas._public_bind = True
    canvas._allow_remote_exec = False
    with pytest.raises(RuntimeError, match="arbitrary Python"):
        canvas.insert(Repl(name="r"))


def test_live_repl_insert_allowed_with_opt_in():
    canvas = danvas.Canvas()
    canvas.enable_repl({})
    canvas._serving = True
    canvas._public_bind = True
    canvas._allow_remote_exec = True
    # Opted in: the insert is permitted.
    canvas.insert(Repl(name="r"))


def test_live_repl_insert_allowed_on_local_bind():
    canvas = danvas.Canvas()
    canvas.enable_repl({})
    canvas._serving = True
    canvas._public_bind = False  # 127.0.0.1
    canvas._allow_remote_exec = False
    canvas.insert(Repl(name="r"))
