"""canvas.emit / canvas.on_event: the backend's universal trigger.

Handlers registered by name must fire (in order) with the emitted data,
from any thread, with the full component-handler feature set: the optional
trailing viewer arg, dedicated threads with queue="latest" coalescing, and
async def handlers. An emit with no listeners is a silent no-op.
"""

import threading
import time

import danvas


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_emit_fires_handlers_in_order_with_data():
    canvas = danvas.Canvas()
    got = []
    canvas.on_event("tick")(lambda d: got.append(("a", d)))
    canvas.on_event("tick")(lambda d: got.append(("b", d)))
    canvas.emit("tick", 42)
    assert _wait(lambda: got == [("a", 42), ("b", 42)]), got


def test_emit_without_listeners_is_a_noop():
    canvas = danvas.Canvas()
    canvas.emit("nobody-home", {"x": 1})     # must not raise


def test_emit_from_another_thread():
    canvas = danvas.Canvas()
    got = []
    canvas.on_event("sensor")(got.append)
    threading.Thread(target=lambda: canvas.emit("sensor", 3.5),
                     daemon=True).start()
    assert _wait(lambda: got == [3.5]), got


def test_handler_can_take_the_viewer_arg():
    canvas = danvas.Canvas()
    got = []

    @canvas.on_event("evt")
    def _(data, viewer):
        got.append((data, viewer))

    canvas.emit("evt", "d")
    # Backend emits have no browser viewer; the slot is filled with {}.
    assert _wait(lambda: got == [("d", {})]), got


def test_dedicated_latest_coalesces_a_burst():
    canvas = danvas.Canvas()
    ran = []
    gate = threading.Event()
    started = threading.Event()

    @canvas.on_event("burst", dedicated=True, queue="latest")
    def _(v):
        started.set()
        gate.wait(5)
        ran.append(v)

    canvas.emit("burst", 1)                  # occupies the dedicated thread
    assert started.wait(5)
    for v in (2, 3, 4):
        canvas.emit("burst", v)              # pile up while 1 is blocked
    time.sleep(0.3)                          # let the queue coalesce
    gate.set()
    # Only the newest pending emit survives the "latest" queue.
    assert _wait(lambda: ran == [1, 4]), ran


def test_async_handler_runs():
    canvas = danvas.Canvas()
    got = []

    @canvas.on_event("aio")
    async def _(v):
        got.append(v * 2)

    canvas.emit("aio", 21)
    assert _wait(lambda: got == [42]), got
