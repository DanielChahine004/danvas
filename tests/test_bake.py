import os
import sys
import types

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
    # A plain canvas pulls in no heavy optional deps: numpy is not force-collected.
    assert "numpy" not in args


def test_build_args_collects_numpy_only_on_request():
    base = bake._build_args("/p/app.py", "App", "/p/dist", "/p")
    assert "numpy" not in base  # off by default
    withnp = bake._build_args("/p/app.py", "App", "/p/dist", "/p", collect_numpy=True)
    i = withnp.index("numpy")
    assert withnp[i - 1] == "--collect-submodules"


def test_packages_for_components():
    # Only the components with heavy runtime deps contribute packages.
    assert bake._packages_for_components({"Slider", "Label"}) == set()
    assert bake._packages_for_components({"AudioFeed"}) == {"numpy"}
    assert bake._packages_for_components({"VideoFeed"}) == {"cv2"}
    assert bake._packages_for_components({"Image"}) == {"PIL"}
    assert bake._packages_for_components({"AudioFeed", "VideoFeed"}) == {"numpy", "cv2"}
    assert bake._packages_for_components(None) == set()


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


def _capture_build_args(monkeypatch, tmp_path, **kwargs):
    """Run build_app with PyInstaller/frontend stubbed, returning the arg list."""
    entry = tmp_path / "app.py"
    entry.write_text("import pycanvas\n")
    monkeypatch.setattr(bake, "_frontend_dist", lambda: str(tmp_path))
    monkeypatch.setattr(bake, "_conda_mkl_binaries", lambda: [])
    captured = {}
    fake = types.SimpleNamespace(run=lambda args: captured.setdefault("args", args))
    monkeypatch.setitem(sys.modules, "PyInstaller", types.SimpleNamespace(__main__=fake))
    monkeypatch.setitem(sys.modules, "PyInstaller.__main__", fake)
    bake.build_app(str(entry), name="App", **kwargs)
    return captured["args"]


def test_baked_app_excludes_tunnel_and_ipython_by_default(monkeypatch, tmp_path):
    # pycloudflared + IPython drag the whole scientific/notebook stack into the
    # build; a standalone local app needs neither, so they're excluded by default.
    args = _capture_build_args(monkeypatch, tmp_path)
    excluded = {args[i + 1] for i, a in enumerate(args) if a == "--exclude-module"}
    assert {"pycloudflared", "IPython"} <= excluded
    # A slider-only app collects no numpy and no media deps.
    collected = {args[i + 1] for i, a in enumerate(args)
                 if a in ("--collect-all", "--collect-submodules")}
    assert not ({"numpy", "cv2", "PIL"} & collected)


def test_components_drive_optional_deps(monkeypatch, tmp_path):
    # An AudioFeed pulls numpy back in (with conda-MKL collection enabled);
    # a VideoFeed collects OpenCV.
    args = _capture_build_args(monkeypatch, tmp_path, components={"AudioFeed"})
    assert args[args.index("numpy") - 1] == "--collect-submodules"
    args = _capture_build_args(monkeypatch, tmp_path, components={"VideoFeed"})
    assert args[args.index("cv2") - 1] == "--collect-all"
    # An Image collects Pillow, and Pillow is then NOT in the exclude list.
    args = _capture_build_args(monkeypatch, tmp_path, components={"Image"})
    assert args[args.index("PIL") - 1] == "--collect-all"
    excluded = {args[i + 1] for i, a in enumerate(args) if a == "--exclude-module"}
    assert "PIL" not in excluded


def test_unused_media_deps_are_excluded(monkeypatch, tmp_path):
    # With no Image/VideoFeed on the canvas, Pillow and OpenCV are excluded so a
    # transitive/typing import (e.g. pygments.formatters.img -> PIL -> numpy)
    # can't drag them — and numpy — into the build.
    args = _capture_build_args(monkeypatch, tmp_path)
    excluded = {args[i + 1] for i, a in enumerate(args) if a == "--exclude-module"}
    assert {"PIL", "cv2"} <= excluded


def test_include_overrides_default_exclude(monkeypatch, tmp_path):
    # Forcing a default-excluded package back via include wins over the exclude.
    args = _capture_build_args(monkeypatch, tmp_path, include=["IPython"])
    excluded = {args[i + 1] for i, a in enumerate(args) if a == "--exclude-module"}
    assert "IPython" not in excluded
    assert args[args.index("IPython") - 1] == "--collect-all"


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
