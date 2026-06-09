import os

import pytest

import pycanvas
from pycanvas import bake


def test_build_args_core_flags():
    args = bake._build_args(
        "/proj/app.py", "MyApp", "/proj/pycanvas/frontend/dist", "/proj",
        onefile=True, windowed=True,
    )
    # entry is first; name/output/data flags present.
    assert args[0] == "/proj/app.py"
    assert args[args.index("--name") + 1] == "MyApp"
    assert "--onefile" in args and "--windowed" in args
    assert "--onedir" not in args
    # The frontend is embedded at the path the frozen server resolves.
    data = args[args.index("--add-data") + 1]
    src, dest = data.split(os.pathsep)
    assert src == "/proj/pycanvas/frontend/dist"
    # Embedded under a non-pycanvas name so it can't shadow the real package.
    assert dest == "pcframe/dist"
    # Lazy-imported backends are collected wholesale.
    assert ["--collect-submodules", "pycanvas"] == [
        args[args.index("--collect-submodules") - 0],
        args[args.index("--collect-submodules") + 1],
    ]
    assert "uvicorn" in args and "websockets" in args


def test_build_args_onedir_and_console_and_icon():
    args = bake._build_args(
        "/proj/app.py", "MyApp", "/proj/dist", "/proj",
        onefile=False, windowed=False, icon="/proj/app.ico",
        hidden_imports=["foo.bar"], add_data=["a;b"],
    )
    assert "--onedir" in args and "--onefile" not in args
    assert "--windowed" not in args  # console kept
    assert args[args.index("--icon") + 1] == "/proj/app.ico"
    assert args[args.index("--hidden-import") + 1] == "foo.bar"
    assert "a;b" in args


def test_build_app_missing_entry():
    with pytest.raises(FileNotFoundError):
        bake.build_app("/no/such/script.py")


def test_bake_runs_app_when_frozen(monkeypatch):
    # Inside the built executable, bake() must NOT rebuild — it delegates to
    # serve() in desktop mode so the .exe just launches the canvas.
    monkeypatch.setattr(pycanvas.canvas.sys, "frozen", True, raising=False)
    canvas = pycanvas.Canvas()
    captured = {}

    def fake_serve(**kwargs):
        captured.update(kwargs)
        return canvas

    monkeypatch.setattr(canvas, "serve", fake_serve)
    canvas.bake(name="RobotConsole", window_size=(900, 600), port=8123)

    assert captured["desktop"] is True
    assert captured["window_title"] == "RobotConsole"
    assert captured["window_size"] == (900, 600)
    assert captured["port"] == 8123
