"""threaded=True input handlers + the shared spawn() primitive.

A handler marked threaded runs on its own daemon thread (via the same spawn()
that backs canvas.background), so a slow handler doesn't hold up the others.
"""

import threading
import time

import pycanvas
from pycanvas.kernel import spawn


def test_spawn_thread_collapses_when_fn_returns():
    done = threading.Event()
    t = spawn(done.set)
    assert done.wait(1.0)
    t.join(1.0)
    assert not t.is_alive()        # the thread lives only as long as fn runs


def test_spawn_logs_exception_without_propagating(capsys):
    t = spawn(lambda: 1 / 0)        # must not raise into the caller
    t.join(1.0)
    assert "ZeroDivisionError" in capsys.readouterr().err


def test_on_click_supports_both_decorator_forms():
    canvas = pycanvas.Canvas()
    btn = canvas.button("go")
    canvas.insert(btn)

    @btn.on_click                   # bare form
    def _plain():
        pass

    @btn.on_click(threaded=True)    # keyword form
    def _slow():
        pass

    assert not getattr(btn._callbacks[0], "_pc_threaded", False)
    assert getattr(btn._callbacks[1], "_pc_threaded", False)


def test_threaded_handler_does_not_block_the_dispatcher():
    canvas = pycanvas.Canvas()
    sld = canvas.slider("v", min=0, max=10)
    canvas.insert(sld)
    ran = threading.Event()

    @sld.on_change(threaded=True)
    def _(val):
        time.sleep(0.3)
        ran.set()

    t0 = time.time()
    sld._handle_input({"value": 5})   # the bridge calls this synchronously
    assert time.time() - t0 < 0.1     # returned at once, didn't wait on sleep
    assert ran.wait(1.0)              # but the handler did run, in the background


def test_non_threaded_handler_runs_inline():
    canvas = pycanvas.Canvas()
    btn = canvas.button("b")
    canvas.insert(btn)

    @btn.on_click
    def _():
        time.sleep(0.2)

    t0 = time.time()
    btn._handle_input({})
    assert time.time() - t0 >= 0.2    # inline = blocks the caller, as before


def test_threaded_handler_still_receives_viewer():
    canvas = pycanvas.Canvas()
    sld = canvas.slider("v", min=0, max=10)
    canvas.insert(sld)
    seen = []
    got = threading.Event()

    @sld.on_change(threaded=True)
    def _(val, viewer):
        seen.append((val, viewer.get("name")))
        got.set()

    sld._handle_input({"value": 7}, {"name": "alice"})
    assert got.wait(1.0)
    assert seen == [(7, "alice")]


def test_routing_on_event_threaded():
    panel = pycanvas.React(source="function P(){ return null }",
                           name="p", event_key="action")
    fired = threading.Event()

    @panel.on("tick", threaded=True)
    def _(msg):
        fired.set()

    panel._handle_input({"action": "tick"})
    assert fired.wait(1.0)
    # the stored handler (not the returned original) carries the mark
    assert getattr(panel._routes["tick"][0], "_pc_threaded", False)
