"""canvas.on_edit: one-function hot reload as a handler trigger.

A real module file on disk, really edited: the watcher must fire only when
the WATCHED function's source changes, rebind the module global to the fresh
definition, keep the old code through a syntax error, and — in its bare
form — re-run the function on save (the default policy).
"""

import importlib.util
import os
import textwrap
import time

import danvas

_V1 = """
def answer():
    return 1

def bystander():
    return "unchanged"
"""

_V2 = """
def answer():
    return 2

def bystander():
    return "unchanged"
"""

_BROKEN = """
def answer(:
    return 3
"""


def _load(path, name):
    # Registered in sys.modules like any real import — the name form of
    # on_edit resolves a file's module globals through sys.modules.
    import sys
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(path, src):
    # A newer mtime is the watcher's trigger; some filesystems are coarse.
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(src))
    late = time.time() + 2
    os.utime(path, (late, late))


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_on_edit_rebinds_and_fires_only_for_the_watched_function(tmp_path):
    path = tmp_path / "livemod.py"
    _write(path, _V1)
    mod = _load(path, "livemod_a")
    canvas = danvas.Canvas()
    fired = []
    canvas.on_edit("answer", file=str(path))(lambda fresh: fired.append(fresh()))

    # An edit elsewhere in the file must NOT fire the watch.
    _write(path, _V1.replace('"unchanged"', '"tweaked"'))
    time.sleep(1.2)
    assert fired == []

    # Editing the watched function fires the handler AND rebinds the global.
    _write(path, _V2)
    assert _wait(lambda: fired == [2]), fired
    assert mod.answer() == 2, "module global was not rebound"


def test_syntax_error_keeps_the_old_definition(tmp_path):
    path = tmp_path / "livemod.py"
    _write(path, _V1)
    mod = _load(path, "livemod_b")
    canvas = danvas.Canvas()
    fired = []
    canvas.on_edit("answer", file=str(path))(lambda fresh: fired.append(fresh()))

    _write(path, _BROKEN)
    time.sleep(1.2)
    assert fired == [] and mod.answer() == 1

    # The watch stays armed: a good save afterwards still lands.
    _write(path, _V2)
    assert _wait(lambda: fired == [2]), fired


def test_bare_on_edit_reruns_on_save(tmp_path):
    path = tmp_path / "livemod.py"
    runs = []
    _write(path, """
import builtins

def tick():
    builtins._on_edit_test_runs.append("ran")
""")
    import builtins
    builtins._on_edit_test_runs = runs
    try:
        mod = _load(path, "livemod_c")
        canvas = danvas.Canvas()
        canvas.on_edit(mod.tick)

        _write(path, """
import builtins

def tick():
    builtins._on_edit_test_runs.append("ran-v2")
""")
        assert _wait(lambda: runs == ["ran-v2"]), runs
    finally:
        del builtins._on_edit_test_runs


def test_decorated_def_runs_once_per_save(tmp_path):
    # Regression: the recompiled def must not re-exec its decorators. When the
    # watched function is decorated with @canvas.on_edit (the usual bare-form
    # layout), re-applying the decorator on save appended a duplicate re-run
    # handler each time — save N ran the function N+1 times.
    path = tmp_path / "livemod.py"
    runs = []
    import builtins
    builtins._on_edit_test_runs = runs
    builtins._on_edit_test_canvas = danvas.Canvas()
    src = """
import builtins

@builtins._on_edit_test_canvas.on_edit
def tick():
    builtins._on_edit_test_runs.append({version!r})
"""
    _write(path, src.format(version="v1"))
    try:
        _load(path, "livemod_d")

        _write(path, src.format(version="v2"))
        assert _wait(lambda: len(runs) >= 1)
        time.sleep(1.2)  # would catch the duplicate handler firing late
        assert runs == ["v2"], runs

        _write(path, src.format(version="v3"))
        assert _wait(lambda: len(runs) >= 2)
        time.sleep(1.2)
        assert runs == ["v2", "v3"], runs
    finally:
        del builtins._on_edit_test_runs
        del builtins._on_edit_test_canvas
