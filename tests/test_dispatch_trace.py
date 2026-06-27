"""Dispatch tracing: canvas.on_dispatch observes handlers as they run.

As each input/layout handler is queued, starts, and finishes (or errors), a tap
gets a trace event — the data behind a live "yellow while running, green when
done" view. All the handlers one browser action fans out to share a `trace` id;
threaded handlers report start/finish from their own thread, so concurrent runs
are visible. Instrumentation is fully skipped when no tap is registered.
"""

import threading
import time

import danvas
from danvas.components.base import _is_user_handler


def _wait_for(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _button(name="go"):
    canvas = danvas.Canvas()
    btn = canvas.button(name)
    canvas.insert(btn)
    return canvas, btn


# -- zero cost when no tap is registered ------------------------------------

def test_no_tap_skips_instrumentation_entirely():
    canvas, btn = _button()
    ran = []

    @btn.on_click
    def _():
        ran.append(1)

    btn._handle_input({})

    assert ran == [1]                       # handler still runs, unchanged
    # No trace id was consumed (the off-path never calls _next_trace_id), so the
    # first id handed out is still 1.
    assert next(canvas._bridge._trace_ids) == 1


# -- the inline path: queued -> start -> done -------------------------------

def test_inline_handler_emits_queued_start_done():
    canvas, btn = _button("go")
    events = []
    canvas.on_dispatch(events.append)

    @btn.on_click
    def handler():
        pass

    btn._handle_input({})

    phases = [e["phase"] for e in events]
    assert phases == ["queued", "start", "done"]
    e = events[0]
    assert e["comp"] == "go"
    assert e["event"] == "click"
    assert e["mode"] == "inline"
    assert e["seq"] == 0
    assert "handler" in e["handler"]        # qualname is in the label
    assert all(ev["trace"] == e["trace"] for ev in events)   # one shared id
    assert events[-1]["dur_ms"] >= 0


def test_multiple_handlers_share_trace_and_increment_seq():
    canvas, btn = _button()
    events = []
    canvas.on_dispatch(events.append)

    @btn.on_click
    def _a():
        pass

    @btn.on_click
    def _b():
        pass

    btn._handle_input({})

    queued = [e for e in events if e["phase"] == "queued"]
    assert [e["seq"] for e in queued] == [0, 1]
    assert len({e["trace"] for e in events}) == 1   # all one action


def test_distinct_triggers_get_distinct_trace_ids():
    canvas, btn = _button()
    events = []
    canvas.on_dispatch(events.append)

    @btn.on_click
    def _():
        pass

    btn._handle_input({})
    btn._handle_input({})

    traces = {e["trace"] for e in events}
    assert len(traces) == 2


# -- errors turn into an error phase ----------------------------------------

def test_handler_exception_emits_error_phase(capsys):
    canvas, btn = _button()
    events = []
    canvas.on_dispatch(events.append)

    @btn.on_click
    def _():
        raise ValueError("boom")

    btn._handle_input({})

    phases = [e["phase"] for e in events]
    assert phases == ["queued", "start", "error"]
    assert "ValueError" in events[-1]["error"]
    assert "dur_ms" in events[-1]
    assert "ValueError" in capsys.readouterr().err   # still printed to console


# -- the threaded path: concurrent, reported from its own thread ------------

def test_threaded_handler_reports_threaded_mode_and_runs():
    canvas = danvas.Canvas()
    sld = canvas.slider("v", min=0, max=10)
    canvas.insert(sld)
    events = []
    lock = threading.Lock()
    canvas.on_dispatch(lambda e: (lock.acquire(), events.append(e), lock.release()))

    @sld.on_change(threaded=True)
    def _(val):
        time.sleep(0.05)

    sld._handle_input({"value": 5})

    # "queued" is emitted synchronously on the dispatch thread...
    assert events and events[0]["phase"] == "queued"
    assert events[0]["mode"] == "threaded"
    # ...then start/done arrive from the handler's own thread.
    assert _wait_for(lambda: [e["phase"] for e in events] == ["queued", "start", "done"])


def test_two_threaded_handlers_run_concurrently():
    canvas = danvas.Canvas()
    sld = canvas.slider("v", min=0, max=10)
    canvas.insert(sld)
    events = []
    lock = threading.Lock()

    def tap(e):
        with lock:
            events.append(e)

    canvas.on_dispatch(tap)
    both_started = threading.Barrier(2, timeout=2.0)

    @sld.on_change(threaded=True)
    def _a(val):
        both_started.wait()        # only proceeds if the other is also running

    @sld.on_change(threaded=True)
    def _b(val):
        both_started.wait()

    sld._handle_input({"value": 5})

    # If they didn't truly overlap, the barrier would time out and raise inside
    # the threads (no "done"). Both reaching "done" proves concurrent execution.
    assert _wait_for(
        lambda: len([e for e in events if e["phase"] == "done"]) == 2)


# -- labels keep the anonymous `def _` handlers distinguishable -------------

def test_handler_label_distinguishes_same_named_handlers():
    canvas, btn = _button()
    events = []
    canvas.on_dispatch(events.append)

    @btn.on_click
    def _():            # same name as the next one...
        pass

    @btn.on_click
    def _():            # ...but a different source line
        pass

    btn._handle_input({})

    labels = [e["handler"] for e in events if e["phase"] == "queued"]
    assert labels[0] != labels[1]      # file:line disambiguates


# -- only the user's handlers are traced, not danvas internals --------------

def test_danvas_internal_handlers_are_not_traced():
    # Canvas.serve is defined inside the danvas package -> excluded; a function
    # defined here (the user's code) -> included.
    assert _is_user_handler(danvas.Canvas.serve) is False

    def local_handler():
        pass

    assert _is_user_handler(local_handler) is True


def test_internal_layout_callback_does_not_appear_in_trace():
    # Canvas.insert registers an internal `_deferred` layout callback on panels
    # placed relatively; dragging fires it, but it must not show in the trace.
    canvas = danvas.Canvas()
    a = canvas.label("a", value="a")
    b = canvas.label("b", value="b", below=a)   # relative -> internal _deferred
    events = []
    canvas.on_dispatch(events.append)

    b._apply_remote_layout({"x": 10, "y": 20})   # a user drag

    assert all("_deferred" not in e["handler"] for e in events)


# -- the on-canvas trace panel ----------------------------------------------

def test_trace_panel_is_valid_and_enables_deep():
    canvas = danvas.Canvas()
    panel = canvas.trace()
    assert panel.validate() == []                 # the authored JSX is sound
    assert canvas._bridge._trace_deep is True      # deep tracing turned on
    assert len(canvas._bridge._dispatch_taps) == 1  # the panel-feeding tap


def test_trace_panel_deep_false_leaves_deep_off():
    canvas = danvas.Canvas()
    canvas.trace(deep=False)
    assert canvas._bridge._trace_deep is False


def test_trace_panel_receives_dispatch_events():
    canvas = danvas.Canvas()
    btn = canvas.button("go")
    canvas.insert(btn)

    @btn.on_click
    def _():
        pass

    panel = canvas.trace(deep=False)              # shallow: handler always traced
    got = []
    panel.push = lambda e: got.append(e)          # spy on what reaches the panel

    btn._handle_input({})

    phases = [e["phase"] for e in got]
    assert "queued" in phases and "start" in phases and "done" in phases
    assert all("trace" in e and "fid" in e for e in got)


# -- launching the trace panel from the Inspector ---------------------------

def _trace_panel_open(canvas):
    return any(getattr(c, "name", None) == "dispatch_trace"
               for c in canvas._bridge._components.values())


def test_inspector_trace_action_toggles_panel():
    canvas = danvas.Canvas()
    insp = danvas.Inspector()
    canvas.insert(insp)
    assert insp._canvas is canvas

    insp._handle_input({"action": "trace"})        # open
    assert _trace_panel_open(canvas)

    insp._handle_input({"action": "trace"})        # click again -> close
    assert not _trace_panel_open(canvas)

    insp._handle_input({"action": "trace"})        # open again
    assert _trace_panel_open(canvas)


def test_closing_trace_panel_detaches_its_tap():
    canvas = danvas.Canvas()
    insp = danvas.Inspector()
    canvas.insert(insp)

    insp._handle_input({"action": "trace"})        # open -> registers a tap
    assert len(canvas._bridge._dispatch_taps) == 1
    insp._handle_input({"action": "trace"})        # close -> removes the tap
    assert canvas._bridge._dispatch_taps == []


def test_ui_inspector_spawns_centered_in_view():
    # `at` is the viewport centre; the panel is placed so its own centre sits
    # there (top-left offset by half its default size).
    canvas = danvas.Canvas()
    insp = canvas._toggle_ui_inspector(at={"x": 500, "y": 320})
    w, h = danvas.Inspector.default_w, danvas.Inspector.default_h
    assert (insp.x, insp.y) == (500 - w / 2, 320 - h / 2)


def test_ui_inspector_falls_back_to_fixed_spot_without_view():
    canvas = danvas.Canvas()
    insp = canvas._toggle_ui_inspector(at=None)
    assert (insp.x, insp.y) == (120, 120)


def test_thread_sampler_pushes_live_thread_snapshots():
    from danvas import _trace

    class _FakePanel:
        id = "p1"

        def __init__(self):
            self.pushed = []

        def push(self, e):
            self.pushed.append(e)

    class _FakeCanvas:
        def __init__(self):
            self._bridge = type("B", (), {"_components": {"p1": object()}})()

    panel, canvas = _FakePanel(), _FakeCanvas()
    _trace.start_thread_sampler(canvas, panel, interval=0.03)
    assert _wait_for(lambda: any("threads" in e for e in panel.pushed))
    canvas._bridge._components.clear()             # panel gone -> sampler exits


# -- always-on history (canvas.trace_history) -------------------------------

def _armed_button():
    canvas = danvas.Canvas()
    canvas._bridge._trace_recording = True        # what serve() does
    btn = canvas.button("go")
    canvas.insert(btn)
    return canvas, btn


def test_history_records_actions_when_armed():
    canvas, btn = _armed_button()

    @btn.on_click
    def handler():
        pass

    btn._handle_input({})

    hist = canvas.trace_history()
    assert len(hist) == 1
    action = hist[0]
    assert action["comp"] == "go"
    assert action["frames"][0]["status"] == "done"
    assert "handler" in action["frames"][0]["handler"]
    assert action["frames"][0]["dur_ms"] is not None


def test_history_records_errors():
    canvas, btn = _armed_button()

    @btn.on_click
    def _():
        raise ValueError("nope")

    btn._handle_input({})

    frame = canvas.trace_history()[0]["frames"][0]
    assert frame["status"] == "error"
    assert "ValueError" in frame["error"]


def test_history_is_bounded_to_the_limit():
    canvas, btn = _armed_button()
    canvas._bridge._trace_history_limit = 5

    @btn.on_click
    def _():
        pass

    for _i in range(20):
        btn._handle_input({})

    assert len(canvas.trace_history()) == 5          # only the most recent kept


def test_no_recording_until_armed():
    canvas = danvas.Canvas()                          # not served -> not armed
    btn = canvas.button("go")
    canvas.insert(btn)

    @btn.on_click
    def _():
        pass

    btn._handle_input({})
    assert canvas.trace_history() == []               # nothing recorded


def test_history_snapshot_is_a_copy():
    canvas, btn = _armed_button()

    @btn.on_click
    def _():
        pass

    btn._handle_input({})
    canvas.trace_history()[0]["frames"].clear()       # mutate the returned copy
    assert canvas.trace_history()[0]["frames"]        # live buffer is untouched


def test_trace_panel_seeds_from_history():
    canvas, btn = _armed_button()

    @btn.on_click
    def _():
        pass

    btn._handle_input({})
    panel = canvas.trace(deep=False)
    # The recorded history is handed to the panel as an initial prop.
    assert panel._data["history"] == canvas.trace_history()
    assert panel.validate() == []


# -- ephemeral panels close on delete instead of going to the graveyard -----

def test_ephemeral_panels_close_instead_of_graveyarding():
    canvas = danvas.Canvas()
    panel = canvas.trace(deep=False)
    insp = canvas.inspector(name="insp")
    bridge = canvas._bridge

    bridge._graveyard(panel.id)            # a browser delete of each
    bridge._graveyard(insp.id)

    assert panel.id not in bridge._components
    assert insp.id not in bridge._components
    assert bridge._graveyarded == {}       # neither went to the graveyard


def test_normal_panel_still_graveyards():
    canvas = danvas.Canvas()
    btn = canvas.button("go")
    canvas.insert(btn)
    bridge = canvas._bridge

    bridge._graveyard(btn.id)

    assert btn.id in bridge._graveyarded
    assert btn._graveyarded is True
    assert btn._visible is False


def test_deleting_trace_panel_detaches_its_tap():
    canvas = danvas.Canvas()
    panel = canvas.trace(deep=False)
    assert len(canvas._bridge._dispatch_taps) == 1

    canvas._bridge._graveyard(panel.id)    # browser delete

    assert canvas._bridge._dispatch_taps == []


# -- accessor plumbing ------------------------------------------------------

def test_on_dispatch_is_decorator_friendly_and_off_removes():
    canvas, btn = _button()
    events = []

    @btn.on_click
    def _():
        pass

    @canvas.on_dispatch
    def tap(e):
        events.append(e)

    assert tap is not None                  # returns the fn (decorator form)
    btn._handle_input({})
    assert events                           # tap fired

    canvas.off_dispatch(tap)
    events.clear()
    btn._handle_input({})
    assert events == []                     # removed -> instrumentation off again
