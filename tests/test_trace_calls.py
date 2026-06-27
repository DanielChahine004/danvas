"""Deep (nested) dispatch tracing: canvas.trace_calls follows a handler into the
user's own functions, emitting depth-tagged start/done events so a trace view can
indent the call tree. Only project code is recorded — danvas/stdlib/3rd-party
frames are skipped and don't inflate the depth.
"""

import os

import danvas
from danvas import _trace

_HERE = os.path.normcase(os.path.abspath(__file__))


class _RecBridge:
    """Minimal bridge: just collects the emitted trace events."""

    def __init__(self):
        self.events = []

    def _emit_dispatch(self, event):
        self.events.append(event)


def _phases(events):
    return [(e["phase"], e["depth"]) for e in events]


# -- the core nesting logic (run_calls + the setprofile probe) ---------------

def test_nested_user_calls_get_increasing_depth():
    def leaf():
        pass

    def mid():
        leaf()

    def handler():
        mid()

    bridge = _RecBridge()
    traceable = lambda code: os.path.normcase(os.path.abspath(code.co_filename)) == _HERE
    _trace.run_calls(bridge, handler, (), {"trace": 1, "comp": "c"}, traceable)

    # handler(0) -> mid(1) -> leaf(2), then unwind in reverse.
    assert _phases(bridge.events) == [
        ("start", 0), ("start", 1), ("start", 2),
        ("done", 2), ("done", 1), ("done", 0),
    ]
    # Each frame's label rides along, so the tree is readable.
    assert "mid (" in bridge.events[1]["handler"]


def test_intermediate_non_user_frames_do_not_inflate_depth():
    # `lib` stands in for a danvas/library frame between two user frames: it's
    # skipped, so `leaf` is still depth 1 (a direct child of the handler), not 2.
    def leaf():
        pass

    def lib():
        leaf()

    def handler():
        lib()

    bridge = _RecBridge()

    def traceable(code):
        return (os.path.normcase(os.path.abspath(code.co_filename)) == _HERE
                and code.co_name != "lib")

    _trace.run_calls(bridge, handler, (), {"trace": 1}, traceable)

    assert _phases(bridge.events) == [
        ("start", 0), ("start", 1), ("done", 1), ("done", 0),
    ]


def test_sibling_calls_share_depth():
    def a():
        pass

    def b():
        pass

    def handler():
        a()
        b()

    bridge = _RecBridge()
    traceable = lambda code: os.path.normcase(os.path.abspath(code.co_filename)) == _HERE
    _trace.run_calls(bridge, handler, (), {"trace": 1}, traceable)

    # a and b are both direct children -> both depth 1, not nested in each other.
    assert _phases(bridge.events) == [
        ("start", 0),
        ("start", 1), ("done", 1),      # a
        ("start", 1), ("done", 1),      # b
        ("done", 0),
    ]


def test_profiler_is_uninstalled_afterwards():
    import sys

    def handler():
        pass

    bridge = _RecBridge()
    before = sys.getprofile()
    _trace.run_calls(bridge, handler, (), {"trace": 1}, lambda code: False)
    assert sys.getprofile() is before     # restored, nothing left installed


# -- is_user_code scoping ----------------------------------------------------

def test_is_user_code_excludes_danvas_and_stdlib(monkeypatch):
    # Pretend this tests/ dir is the project root.
    monkeypatch.setattr(_trace, "_PROJECT_ROOT",
                        os.path.normcase(os.path.dirname(_HERE)))

    def local():
        pass

    assert _trace.is_user_code(local.__code__) is True          # in project
    assert _trace.is_user_code(danvas.Canvas.serve.__code__) is False  # danvas
    assert _trace.is_user_code(os.path.join.__code__) is False  # stdlib


# -- end to end through a real canvas + dispatch -----------------------------

def _helper_step():           # module-level so it's "project code" under tests/
    return 1


def test_deep_trace_through_canvas_dispatch(monkeypatch):
    monkeypatch.setattr(_trace, "_PROJECT_ROOT",
                        os.path.normcase(os.path.dirname(_HERE)))
    canvas = danvas.Canvas()
    btn = canvas.button("go")
    canvas.insert(btn)
    canvas.trace_calls()                    # turn deep tracing on
    events = []
    canvas.on_dispatch(events.append)

    @btn.on_click
    def handler():
        _helper_step()                      # a nested user call -> depth 1

    btn._handle_input({})

    depths = {(e["phase"], e["depth"]) for e in events}
    assert ("queued", 0) in depths
    assert ("start", 0) in depths           # the handler
    assert ("start", 1) in depths           # _helper_step, nested
    assert ("done", 1) in depths
    assert any("_helper_step" in e["handler"] for e in events
               if e.get("depth") == 1)
