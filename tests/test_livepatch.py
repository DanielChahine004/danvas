"""The smart middle tier of hot reload: safe_live_diff + apply_live_patch.

safe_live_diff classifies a script edit as "only top-level function bodies
changed" (live-patchable) or "needs a full restart". apply_live_patch swaps the
changed functions' code objects in place — preserving the process, its heap, and
its threads — and declines (so the monitor restarts) for anything it can't apply
safely.
"""

import types

from danvas._livepatch import apply_live_patch, safe_live_diff


# -- safe_live_diff: the classifier -----------------------------------------

def test_body_only_change_is_patchable():
    old = "def handle(v):\n    return v + 1\n"
    new = "def handle(v):\n    return v + 999\n"
    assert safe_live_diff(old, new) == [{"name": "handle", "occ": 0}]


def test_identical_apart_from_comments_is_noop():
    old = "def f():\n    return 1\n"
    new = "def f():\n    # a new comment\n    return 1\n"
    assert safe_live_diff(old, new) == []


def test_signature_change_forces_restart():
    old = "def handle(v):\n    return v\n"
    new = "def handle(v, w):\n    return v\n"
    assert safe_live_diff(old, new) is None


def test_decorator_change_forces_restart():
    old = "@a\ndef handle(v):\n    return v\n"
    new = "@b\ndef handle(v):\n    return v\n"
    assert safe_live_diff(old, new) is None


def test_added_import_forces_restart():
    old = "def f():\n    return 1\n"
    new = "import math\n\ndef f():\n    return 1\n"
    assert safe_live_diff(old, new) is None


def test_changed_global_forces_restart():
    old = "X = 1\n\ndef f():\n    return X\n"
    new = "X = 2\n\ndef f():\n    return X\n"
    assert safe_live_diff(old, new) is None


def test_new_function_forces_restart():
    old = "def f():\n    return 1\n"
    new = "def f():\n    return 1\n\ndef g():\n    return 2\n"
    assert safe_live_diff(old, new) is None


def test_syntax_error_forces_restart():
    old = "def f():\n    return 1\n"
    new = "def f(:\n    return 1\n"
    assert safe_live_diff(old, new) is None


def test_nested_body_change_targets_enclosing_function():
    # A change inside a function defined within a top-level function is reported
    # as a change to that top-level function (its whole code object is swapped).
    old = "def build():\n    def inner():\n        return 1\n    return inner\n"
    new = "def build():\n    def inner():\n        return 7\n    return inner\n"
    assert safe_live_diff(old, new) == [{"name": "build", "occ": 0}]


def test_occurrence_index_for_duplicate_names():
    # The `def _` handler idiom: only the second one's body changes.
    old = "def _(v):\n    return v + 1\n\ndef _(v):\n    return v + 2\n"
    new = "def _(v):\n    return v + 1\n\ndef _(v):\n    return v + 22\n"
    assert safe_live_diff(old, new) == [{"name": "_", "occ": 1}]


def test_shadowing_nested_def_forces_restart():
    # A module-level nested def shadowing a top-level name is ambiguous -> restart.
    old = ("if True:\n    def f():\n        return 0\n\n"
           "def f():\n    return 1\n")
    new = ("if True:\n    def f():\n        return 0\n\n"
           "def f():\n    return 2\n")
    assert safe_live_diff(old, new) is None


# -- apply_live_patch: the in-process swap ----------------------------------

def _make_module(src, path):
    """Compile + exec ``src`` as a stand-in ``__main__`` whose functions report
    ``path`` as their file (matching how the real worker runs the script)."""
    path.write_text(src, encoding="utf-8")
    mod = types.ModuleType("__main__")
    mod.__file__ = str(path)
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


class _FakeComp:
    """Stands in for a live component holding handlers in a callback list."""

    def __init__(self, fns):
        self._callbacks = list(fns)


def test_swaps_top_level_helper(tmp_path):
    old = "def add(a, b):\n    return a + b\n"
    new = "def add(a, b):\n    return a * b\n"
    mod = _make_module(old, tmp_path / "s.py")
    specs = safe_live_diff(old, new)

    ok, swapped = apply_live_patch(mod, [], old, new, specs)

    assert ok and swapped == ["add"]
    assert mod.__dict__["add"](3, 4) == 12  # new behaviour, same function object


def test_swaps_handler_held_by_component(tmp_path):
    old = "def _(v):\n    return v + 1\n"
    new = "def _(v):\n    return v + 100\n"
    mod = _make_module(old, tmp_path / "s.py")
    handler = mod.__dict__["_"]            # the same object a component would hold
    comp = _FakeComp([handler])
    specs = safe_live_diff(old, new)

    ok, _swapped = apply_live_patch(mod, [comp], old, new, specs)

    assert ok
    assert comp._callbacks[0](5) == 105    # live handler now runs the new body


def test_occurrence_indexed_swap_hits_the_right_duplicate(tmp_path):
    # Two top-level `def _`; only the first changes. Both must stay reachable, so
    # the script stashes them in a list the way a decorator would.
    old = ("REG = []\n"
           "def _(v):\n    return v + 1\n"
           "REG.append(_)\n"
           "def _(v):\n    return v + 2\n"
           "REG.append(_)\n")
    new = ("REG = []\n"
           "def _(v):\n    return v + 1000\n"
           "REG.append(_)\n"
           "def _(v):\n    return v + 2\n"
           "REG.append(_)\n")
    mod = _make_module(old, tmp_path / "s.py")
    reg = mod.__dict__["REG"]
    comp = _FakeComp(reg)
    specs = safe_live_diff(old, new)
    assert specs == [{"name": "_", "occ": 0}]

    ok, _swapped = apply_live_patch(mod, [comp], old, new, specs)

    assert ok
    assert reg[0](5) == 1005   # first handler patched
    assert reg[1](5) == 7      # second handler untouched


def test_declines_background_worker(tmp_path):
    # A registered @canvas.background worker is parked in its loop; swapping its
    # code would silently do nothing, so apply must decline (-> restart).
    old = "def worker():\n    return 1\n"
    new = "def worker():\n    return 2\n"
    mod = _make_module(old, tmp_path / "s.py")
    worker = mod.__dict__["worker"]
    specs = safe_live_diff(old, new)

    ok, reason = apply_live_patch(mod, [], old, new, specs,
                                  background_funcs=[worker])

    assert not ok
    assert "background" in reason
    assert mod.__dict__["worker"]() == 1   # not swapped


def test_all_or_nothing_when_one_target_is_unresolvable(tmp_path):
    # Two functions change, but one isn't reachable live -> the whole patch is
    # declined and the resolvable one is left untouched.
    old = "def a(v):\n    return v + 1\n\ndef b(v):\n    return v + 1\n"
    new = "def a(v):\n    return v + 9\n\ndef b(v):\n    return v + 9\n"
    mod = _make_module(old, tmp_path / "s.py")
    # Delete b from the live namespace so it can't be located.
    del mod.__dict__["b"]
    specs = safe_live_diff(old, new)

    ok, _reason = apply_live_patch(mod, [], old, new, specs)

    assert not ok
    assert mod.__dict__["a"](0) == 1   # a was NOT swapped despite being resolvable


def test_empty_specs_is_a_clean_noop(tmp_path):
    src = "def f():\n    return 1\n"
    mod = _make_module(src, tmp_path / "s.py")
    ok, swapped = apply_live_patch(mod, [], src, src, [])
    assert ok and swapped == []


# -- /__hot_patch__ endpoint -------------------------------------------------

def _client(canvas, host="127.0.0.1"):
    import danvas.server as server
    from fastapi.testclient import TestClient

    app = server.create_app(canvas._bridge, open_browser=False)
    return TestClient(app, client=(host, 9999))


def test_endpoint_rejects_non_loopback():
    import danvas

    client = _client(danvas.Canvas(), host="10.0.0.5")
    resp = client.post("/__hot_patch__", json={"old": "", "new": ""})
    assert resp.status_code == 403


def test_endpoint_reports_restart_for_structural_change():
    import danvas

    client = _client(danvas.Canvas())
    resp = client.post("/__hot_patch__", json={
        "old": "def f():\n    return 1\n",
        "new": "import os\n\ndef f():\n    return 1\n",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "not a body-only change"}


def test_endpoint_swaps_a_live_handler(monkeypatch, tmp_path):
    # Drive the full endpoint path: a real Button whose on_click handler is a
    # top-level function in a stand-in __main__, body-edited and patched live.
    import sys
    import danvas

    src_old = (
        "import danvas\n"
        "canvas = danvas.Canvas()\n"
        "btn = canvas.button('go')\n"
        "@btn.on_click\n"
        "def _():\n"
        "    btn._patched = 1\n"
    )
    main = _make_module(src_old, tmp_path / "app.py")
    monkeypatch.setitem(sys.modules, "__main__", main)
    canvas = main.__dict__["canvas"]
    btn = main.__dict__["btn"]

    src_new = src_old.replace("btn._patched = 1", "btn._patched = 42")
    client = _client(canvas)
    resp = client.post("/__hot_patch__", json={"old": src_old, "new": src_new})

    assert resp.json() == {"ok": True, "swapped": ["_"]}
    btn._handle_input({}, {})              # fire the (now patched) handler
    assert btn._patched == 42
