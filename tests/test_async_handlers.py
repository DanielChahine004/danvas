"""``async def`` handler support.

Any ``on_*`` handler (and ``@canvas.background`` worker) may be a coroutine
function: it runs on the shared AsyncKernel loop (its own daemon thread), so an
awaiting handler never blocks the shared dispatch thread. ``threaded=True``
awaits per-call on its own thread; ``dedicated=True`` awaits serially on the
handler's own thread. ``on_request`` coroutines reply when they complete.
"""

import asyncio
import json
import threading
import time

import danvas
from danvas.bridge import Bridge
from danvas.kernel import AsyncKernel


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# -- the three dispatch modes ---------------------------------------------------

def test_async_on_change_frees_the_dispatch_thread():
    canvas = danvas.Canvas()
    sld = canvas.slider("v")
    done = threading.Event()

    @sld.on_change
    async def _(val):
        await asyncio.sleep(0.15)
        done.set()

    t0 = time.time()
    sld._handle_input({"value": 3})          # the bridge calls this synchronously
    assert time.time() - t0 < 0.1            # returned before the await finished
    assert done.wait(2.0)                    # but the handler did run, on the loop


def test_async_handler_receives_viewer():
    canvas = danvas.Canvas()
    sld = canvas.slider("v")
    seen = []

    @sld.on_change
    async def _(val, viewer):
        seen.append((val, viewer.get("name")))

    sld._handle_input({"value": 2}, {"name": "Fox"})
    assert _wait_for(lambda: seen == [(2, "Fox")])


def test_async_threaded_handler_runs():
    canvas = danvas.Canvas()
    btn = canvas.button("go")
    done = threading.Event()

    @btn.on_click(threaded=True)
    async def _():
        await asyncio.sleep(0.01)
        done.set()

    btn._handle_input({})
    assert done.wait(2.0)


def test_async_dedicated_handler_stays_serialised():
    canvas = danvas.Canvas()
    sld = canvas.slider("v")
    order = []

    @sld.on_change(dedicated=True)
    async def _(v):
        if v == 1:
            await asyncio.sleep(0.1)          # the slow first call...
        order.append(v)

    sld._handle_input({"value": 1})
    sld._handle_input({"value": 2})
    assert _wait_for(lambda: len(order) == 2)
    assert order == [1, 2]                    # ...still finishes before the second


def test_async_handler_exception_is_logged_not_raised(capsys):
    canvas = danvas.Canvas()
    btn = canvas.button("boom")

    @btn.on_click
    async def _():
        raise ValueError("kapow")

    btn._handle_input({})                     # must not raise into the dispatcher
    assert _wait_for(lambda: "kapow" in capsys.readouterr().err)


# -- tracing (armed whenever a canvas serves) is the production path ------------

def test_async_handler_runs_and_completes_under_trace_recording():
    canvas = danvas.Canvas()
    sld = canvas.slider("v")
    canvas._bridge._trace_recording = True    # what serve() arms
    done = threading.Event()

    @sld.on_change
    async def _(val):
        await asyncio.sleep(0.02)
        done.set()

    sld._handle_input({"value": 1})
    assert done.wait(2.0)
    # The trace's done event fires when the coroutine finishes, with a real
    # duration — not at coroutine creation.
    def _recorded_done():
        for rec in canvas._bridge._trace_history_snapshot():
            for f in rec["frames"]:
                if f["status"] == "done" and (f["dur_ms"] or 0) >= 10:
                    return True
        return False
    assert _wait_for(_recorded_done)


# -- on_request coroutines -------------------------------------------------------

def _request_bridge():
    b = Bridge()
    replies = []
    b._reply = lambda req_id, result=None, error=None, ws=None: \
        replies.append({"reqId": req_id, "result": result, "error": error,
                        "ws": ws})
    return b, replies


def test_async_on_request_replies_with_result():
    b, replies = _request_bridge()
    p = danvas.React("function Component(){return null}", name="p")
    p._bind("r1", b)

    @p.on_request()
    async def _(req):
        await asyncio.sleep(0.01)
        return {"doubled": req["n"] * 2}

    b._dispatch_request(p, "q1", {"n": 21}, ws="the-socket")
    assert _wait_for(lambda: replies)
    assert replies[0] == {"reqId": "q1", "result": {"doubled": 42},
                          "error": None, "ws": "the-socket"}


def test_async_on_request_error_rejects_the_promise():
    b, replies = _request_bridge()
    p = danvas.React("function Component(){return null}", name="p")
    p._bind("r1", b)

    @p.on_request()
    async def _(req):
        raise RuntimeError("nope")

    b._dispatch_request(p, "q2", {}, ws="s")
    assert _wait_for(lambda: replies)
    assert "RuntimeError" in replies[0]["error"] and replies[0]["ws"] == "s"


# -- background workers ----------------------------------------------------------

def test_async_background_worker_runs_on_the_shared_loop():
    canvas = danvas.Canvas()
    done = threading.Event()

    @canvas.background
    async def worker():
        await asyncio.sleep(0.01)
        done.set()

    canvas._start_background()
    assert done.wait(2.0)


# -- the AsyncKernel primitive ----------------------------------------------------

def test_async_kernel_is_a_shared_singleton():
    assert AsyncKernel.get() is AsyncKernel.get()


def test_async_kernel_spawn_logs_exceptions(capsys):
    async def boom():
        raise ZeroDivisionError("async boom")

    fut = AsyncKernel.get().spawn(boom())
    assert _wait_for(lambda: fut.done())
    assert _wait_for(lambda: "ZeroDivisionError" in capsys.readouterr().err)


def test_async_kernel_submit_returns_result():
    async def add(a, b):
        return a + b

    assert AsyncKernel.get().submit(add(2, 3)).result(timeout=2.0) == 5
