"""serve(persist=...): auto-save the formation + drawings, reload on startup."""

import json
import os

import pycanvas


def _build(canvas):
    """Two placed panels, so a round-trip has geometry to compare."""
    s = canvas.slider("vol", min=0, max=10)
    canvas.insert(s, x=10, y=20, w=200, h=80)
    b = canvas.button("go")
    canvas.insert(b, x=300, y=400)
    return s, b


def test_flush_writes_layout_and_drawings(tmp_path):
    path = str(tmp_path / "board.canvas.json")
    canvas = pycanvas.Canvas()
    s, _ = _build(canvas)
    canvas._persist_setup(path)            # no file yet -> arms autosave only
    assert canvas._bridge._on_mutation is not None

    # A user drag (layout frame) and a free-form drawing (draw frame).
    canvas._bridge._dispatch_layout(
        s, {"x": 111, "y": 222, "w": 250, "h": 90, "rotation": 0})
    canvas._bridge._apply_draw(
        {"added": {"shape:doodle": {"id": "shape:doodle"}},
         "updated": {}, "removed": {}})
    canvas._persist_flush()

    data = json.loads(open(path).read())
    by_name = {c["name"]: c for c in data["layout"]["components"]}
    assert (by_name["vol"]["x"], by_name["vol"]["y"]) == (111, 222)
    assert (by_name["vol"]["w"], by_name["vol"]["h"]) == (250, 90)
    assert "shape:doodle" in data["drawings"]


def test_reload_restores_onto_code_created_panels(tmp_path):
    path = str(tmp_path / "board.canvas.json")
    # Run 1: edit and flush.
    c1 = pycanvas.Canvas()
    s1, _ = _build(c1)
    c1._persist_setup(path)
    c1._bridge._dispatch_layout(s1, {"x": 111, "y": 222})
    c1._bridge._apply_draw(
        {"added": {"shape:d": {"id": "shape:d"}}, "updated": {}, "removed": {}})
    c1._persist_flush()

    # Run 2: a fresh canvas recreates the panels in code, then loads.
    c2 = pycanvas.Canvas()
    s2, _ = _build(c2)             # code places vol back at (10, 20)
    c2._persist_setup(path)        # file exists -> overlays the saved formation
    assert (s2.x, s2.y) == (111, 222)
    assert "shape:d" in c2._bridge._drawings


def test_load_does_not_immediately_resave(tmp_path):
    """Arming happens after the load, so restoring state isn't itself a change."""
    path = str(tmp_path / "board.canvas.json")
    c1 = pycanvas.Canvas()
    _build(c1)
    c1._persist_setup(path)
    c1._persist_flush()
    mtime = os.path.getmtime(path)

    c2 = pycanvas.Canvas()
    _build(c2)
    c2._persist_setup(path)        # loads; must not schedule a write
    assert c2._persist_timer is None
    assert os.path.getmtime(path) == mtime


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = str(tmp_path / "board.canvas.json")
    canvas = pycanvas.Canvas()
    _build(canvas)
    canvas._persist_setup(path)
    canvas._persist_flush()
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_file_starts_fresh(tmp_path):
    path = tmp_path / "board.canvas.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    canvas = pycanvas.Canvas()
    _build(canvas)
    import pytest
    with pytest.warns(UserWarning, match="could not load"):
        canvas._persist_setup(str(path))      # must not raise
    assert canvas._bridge._on_mutation is not None   # autosave still armed


def test_persist_off_is_inert():
    canvas = pycanvas.Canvas()
    assert canvas._persist_path is None
    assert canvas._bridge._on_mutation is None
    canvas._persist_flush()                   # safe no-op when off


def test_default_path_named_after_script(monkeypatch, tmp_path):
    # Deterministic regardless of how the suite is launched (python -m pytest vs
    # the pytest console script give __main__ different __file__s): drive it off
    # a controlled __main__.
    import sys
    import types
    fake_main = types.ModuleType("__main__")
    fake_main.__file__ = str(tmp_path / "myapp.py")
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    p = pycanvas.Canvas._default_persist_path()
    assert os.path.isabs(p)
    assert os.path.basename(p) == "myapp.canvas.json"


def test_opacity_round_trips_through_persist(tmp_path):
    path = str(tmp_path / "board.canvas.json")
    # Run 1: set opacity and flush.
    c1 = pycanvas.Canvas()
    s1, _ = _build(c1)
    s1.opacity = 0.3
    c1._persist_setup(path)
    c1._persist_flush()

    # Run 2: recreate panels, load — opacity should be restored.
    c2 = pycanvas.Canvas()
    s2, _ = _build(c2)
    c2._persist_setup(path)
    assert abs(s2.opacity - 0.3) < 1e-9


def test_default_path_falls_back_without_a_script(monkeypatch):
    # No .py __file__ (REPL / notebook) -> a fixed name that still ends in
    # .canvas.json, so the *.canvas.json gitignore still catches it.
    import sys
    import types
    monkeypatch.setitem(sys.modules, "__main__", types.ModuleType("__main__"))
    p = pycanvas.Canvas._default_persist_path()
    assert os.path.basename(p) == "pycanvas.canvas.json"
