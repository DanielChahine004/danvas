"""serve(persist=...): auto-save the formation + drawings, reload on startup."""

import json
import os

import danvas


def _build(canvas):
    """Two placed panels, so a round-trip has geometry to compare."""
    s = canvas.slider("vol", min=0, max=10)
    canvas.insert(s, x=10, y=20, w=200, h=80)
    b = canvas.button("go")
    canvas.insert(b, x=300, y=400)
    return s, b


def test_flush_writes_layout_and_drawings(tmp_path):
    path = str(tmp_path / "board.canvas.json")
    canvas = danvas.Canvas()
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
    c1 = danvas.Canvas()
    s1, _ = _build(c1)
    c1._persist_setup(path)
    c1._bridge._dispatch_layout(s1, {"x": 111, "y": 222})
    c1._bridge._apply_draw(
        {"added": {"shape:d": {"id": "shape:d"}}, "updated": {}, "removed": {}})
    c1._persist_flush()

    # Run 2: a fresh canvas recreates the panels in code, then loads.
    c2 = danvas.Canvas()
    s2, _ = _build(c2)             # code places vol back at (10, 20)
    c2._persist_setup(path)        # file exists -> overlays the saved formation
    assert (s2.x, s2.y) == (111, 222)
    assert "shape:d" in c2._bridge._drawings


def test_load_does_not_immediately_resave(tmp_path):
    """Arming happens after the load, so restoring state isn't itself a change."""
    path = str(tmp_path / "board.canvas.json")
    c1 = danvas.Canvas()
    _build(c1)
    c1._persist_setup(path)
    c1._persist_flush()
    mtime = os.path.getmtime(path)

    c2 = danvas.Canvas()
    _build(c2)
    c2._persist_setup(path)        # loads; must not schedule a write
    assert c2._persist_timer is None
    assert os.path.getmtime(path) == mtime


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = str(tmp_path / "board.canvas.json")
    canvas = danvas.Canvas()
    _build(canvas)
    canvas._persist_setup(path)
    canvas._persist_flush()
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_file_starts_fresh(tmp_path):
    path = tmp_path / "board.canvas.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    canvas = danvas.Canvas()
    _build(canvas)
    import pytest
    with pytest.warns(UserWarning, match="could not load"):
        canvas._persist_setup(str(path))      # must not raise
    assert canvas._bridge._on_mutation is not None   # autosave still armed


def test_persist_off_is_inert():
    canvas = danvas.Canvas()
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
    p = danvas.Canvas._default_persist_path()
    assert os.path.isabs(p)
    assert os.path.basename(p) == "myapp.canvas.json"


def test_opacity_round_trips_through_persist(tmp_path):
    path = str(tmp_path / "board.canvas.json")
    # Run 1: set opacity and flush.
    c1 = danvas.Canvas()
    s1, _ = _build(c1)
    s1.opacity = 0.3
    c1._persist_setup(path)
    c1._persist_flush()

    # Run 2: recreate panels, load — opacity should be restored.
    c2 = danvas.Canvas()
    s2, _ = _build(c2)
    c2._persist_setup(path)
    assert abs(s2.opacity - 0.3) < 1e-9


def test_default_path_falls_back_without_a_script(monkeypatch):
    # No .py __file__ (REPL / notebook) -> a fixed name that still ends in
    # .canvas.json, so the *.canvas.json gitignore still catches it.
    import sys
    import types
    monkeypatch.setitem(sys.modules, "__main__", types.ModuleType("__main__"))
    p = danvas.Canvas._default_persist_path()
    assert os.path.basename(p) == "danvas.canvas.json"


# -- user-set input values survive save/load and serve(persist=) --------------

def _controls(canvas):
    """A slider, a toggle, and a text field — the value-bearing input controls."""
    return (canvas.slider("vol", min=0, max=10),
            canvas.toggle(["a", "b", "c"], name="mode"),
            canvas.text_field("comment"))


def test_layout_captures_input_values_but_not_content_panels():
    c = danvas.Canvas()
    s, t, tf = _controls(c)
    c.label("status", "idle")          # content panel — no user value
    s.update(7); t.update("b"); tf.update("hello")
    by_name = {item["name"]: item for item in c._layout()["components"]}
    assert by_name["vol"]["state"] == {"value": 7}
    assert by_name["mode"]["state"] == {"value": "b"}
    assert by_name["comment"]["state"] == {"value": "hello"}
    assert "state" not in by_name["status"]   # Label persists no value


def test_save_load_round_trip_restores_values(tmp_path):
    path = str(tmp_path / "board.json")
    c1 = danvas.Canvas()
    s, t, tf = _controls(c1)
    s.update(7); t.update("b"); tf.update("hello")
    c1.save(path)                       # no browser -> layout (+values) only

    c2 = danvas.Canvas()                # same code, fresh defaults
    s2, t2, tf2 = _controls(c2)
    assert (s2.value, t2.value, tf2.value) != (7, "b", "hello")
    c2.load(path)
    assert s2.value == 7
    assert t2.value == "b"
    assert tf2.value == "hello"


def test_restore_does_not_fire_on_change(tmp_path):
    path = str(tmp_path / "board.json")
    c1 = danvas.Canvas()
    s, _, _ = _controls(c1)
    s.update(5)
    c1.save(path)

    c2 = danvas.Canvas()
    s2, _, _ = _controls(c2)
    fired = []
    s2.on_change(lambda v: fired.append(v))
    c2.load(path)
    assert s2.value == 5
    assert fired == []                  # restore is silent — code, not user input


def test_input_change_arms_the_persist_autosave():
    # A committed value change must arm the debounced autosave the same way a
    # drag/draw does — otherwise a value set mid-session is lost on a crash.
    c = danvas.Canvas()
    s = c.slider("vol", min=0, max=10)
    fired = []
    c._bridge._on_mutation = lambda: fired.append(1)
    c._bridge._dispatch_input(s, {"value": 4}, ws=None)
    assert s.value == 4
    assert fired                       # autosave was armed


def test_persist_restores_values_on_startup(tmp_path):
    path = str(tmp_path / "b.canvas.json")
    c1 = danvas.Canvas()
    s, _, _ = _controls(c1)
    s.update(8)
    c1._persist_setup(path)             # arms autosave (no file yet)
    c1._persist_flush()                 # writes layout + values now

    c2 = danvas.Canvas()
    s2, _, _ = _controls(c2)
    c2._persist_setup(path)             # file exists -> loads it, restoring 8
    assert s2.value == 8


# -- panel.persist(): custom panels opt into the same persistence -----------

def test_custom_panel_persist_round_trips_via_save_load(tmp_path):
    # A hand-built react() panel persists arbitrary state across a save/load,
    # exactly like a built-in input control — proving the public persist() hook.
    path = str(tmp_path / "board.json")

    def _make(canvas):
        state = {"value": 0}
        panel = canvas.react("function Component(){return null}",
                             name="myslider", props={"value": 0})
        panel.persist(lambda: dict(state),
                      lambda s: (state.update(s), panel.update(**s)))
        return panel, state

    c1 = danvas.Canvas()
    _p1, st1 = _make(c1)
    st1["value"] = 73                      # user moved it
    c1.save(path)

    c2 = danvas.Canvas()                   # same code, fresh defaults
    _p2, st2 = _make(c2)
    assert st2["value"] == 0
    c2.load(path)
    assert st2["value"] == 73              # restored into Python state


def test_persist_is_chainable_and_validates():
    import pytest
    c = danvas.Canvas()
    p = c.react("function Component(){return null}", name="p")
    assert p.persist(lambda: {}, lambda s: None) is p     # returns the panel
    with pytest.raises(TypeError):
        p.persist(1, 2)                                    # non-callables rejected


def test_persist_snapshot_is_captured_in_layout():
    c = danvas.Canvas()
    p = c.react("function Component(){return null}", name="p")
    box = {"n": 5}
    p.persist(lambda: box, lambda s: box.update(s))
    item = next(i for i in c._layout()["components"] if i["name"] == "p")
    assert item["state"] == {"state": {"n": 5}}
