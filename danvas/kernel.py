"""Threading utilities: daemon thread spawning and serial execution queues."""

import asyncio
import queue
import threading
import traceback


def spawn(fn, *args, name=None, **kwargs):
    """Run ``fn(*args, **kwargs)`` on a fresh daemon thread; log any exception.

    The single "give this its own thread" primitive behind both
    :meth:`Canvas.background` (a producer loop started at serve) and the
    ``threaded=True`` input handlers (run off the shared dispatch thread so a
    slow handler doesn't hold up the others). The thread lives exactly as long
    as ``fn`` runs: a ``while True`` loop keeps it alive for the app's lifetime,
    a handler that returns lets it collapse on its own. Daemon, so it never
    blocks interpreter shutdown or a hot-reload teardown. Returns the thread.
    """
    def run():
        try:
            fn(*args, **kwargs)
        except Exception:
            traceback.print_exc()
    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread


class Kernel:
    """One daemon thread running submitted callables in FIFO order."""

    def __init__(self):
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False
        self._lock = threading.Lock()

    def submit(self, fn):
        """Queue ``fn`` to run on the kernel thread; start it on first use."""
        with self._lock:
            if not self._started:
                self._started = True
                self._thread.start()
        self._q.put(fn)

    def is_current_thread(self):
        """True when called from this kernel's own worker thread.

        Lets a blocking call (e.g. a browser round-trip that waits on a reply)
        detect that it is running *on* the shared dispatch thread, where waiting
        would stall every other queued handler until it returns.
        """
        return threading.current_thread() is self._thread

    def _run(self):
        while True:
            fn = self._q.get()
            try:
                fn()
            except Exception:
                traceback.print_exc()


class AsyncKernel:
    """One asyncio event loop on a daemon thread — where ``async def`` handlers
    and background workers run.

    A single loop is shared process-wide (lazily started on first use), so every
    async handler interleaves on it: concurrency comes from ``await``, not from a
    thread per handler. This is deliberately *not* the server's own event loop —
    a user handler that blocks between awaits can then never stall rendering or
    the WebSocket, only other async handlers (the same isolation the sync
    dispatch thread provides).

    :meth:`submit` schedules a coroutine and returns its
    ``concurrent.futures.Future`` (the caller owns error handling — an
    unretrieved failure would vanish silently); :meth:`spawn` is the
    fire-and-forget form that logs any exception, mirroring :func:`spawn` for
    threads.
    """

    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls):
        """The process-wide kernel, created (and its thread started) on first use."""
        inst = cls._instance
        if inst is None:
            with cls._instance_lock:
                inst = cls._instance
                if inst is None:
                    inst = cls._instance = cls()
        return inst

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="danvas-async",
                                        daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro):
        """Schedule ``coro`` on the loop; return its ``concurrent.futures.Future``.

        Thread-safe (any thread may call it). The caller must consume the
        future — ``result()`` to wait/re-raise, or a done-callback — or use
        :meth:`spawn` instead so a failure isn't dropped on the floor.
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def spawn(self, coro):
        """Fire-and-forget :meth:`submit`: run ``coro``, log any exception.

        The async twin of :func:`spawn` — same contract (never raises into the
        caller, failures reach the console), for coroutines instead of threads.
        Returns the future so a caller may still observe completion.
        """
        fut = self.submit(coro)

        def _log(f):
            if f.cancelled():
                return
            exc = f.exception()
            if exc is not None:
                traceback.print_exception(type(exc), exc, exc.__traceback__)

        fut.add_done_callback(_log)
        return fut

    def is_current_thread(self):
        """True when called from the async kernel's own loop thread (so a
        blocking wait there would deadlock the loop — mirror of
        :meth:`Kernel.is_current_thread`)."""
        return threading.current_thread() is self._thread


class DedicatedKernel:
    """A persistent single daemon thread bound to one handler, with configurable queuing.

    Unlike :class:`Kernel` (which runs any callable), this is created once per
    ``dedicated=True`` handler and lives for the app's lifetime. Two modes:

    ``"fifo"`` — every call is queued and run in order (an unbounded
    :class:`queue.Queue`). No calls are dropped; right when every event must
    be processed (file uploads, confirmations, ordered state machines).

    ``"latest"`` — only the most recent *pending* call is kept. The thread
    always runs the current call to completion, then picks up only the latest
    pending one, dropping everything that queued up in between. Right for
    high-rate inputs where you only care about the current value: a slider
    connected to a 200 ms compute — you want to process where it settled, not
    replay every intermediate drag position.

    In both modes the thread is started on the first :meth:`submit` call.
    """

    def __init__(self, mode="fifo"):
        if mode not in ("fifo", "latest"):
            raise ValueError(f"DedicatedKernel mode must be 'fifo' or 'latest', got {mode!r}")
        self._mode = mode
        if mode == "latest":
            self._slot_lock = threading.Lock()
            self._event = threading.Event()
            self._pending = None
        else:
            self._q = queue.Queue()
        self._start_lock = threading.Lock()
        self._started = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def submit(self, fn):
        """Queue (or replace) ``fn``; start the thread on first call."""
        with self._start_lock:
            if not self._started:
                self._started = True
                self._thread.start()
        if self._mode == "latest":
            with self._slot_lock:
                self._pending = fn
            self._event.set()
        else:
            self._q.put(fn)

    def _run(self):
        if self._mode == "latest":
            while True:
                self._event.wait()
                with self._slot_lock:
                    fn = self._pending
                    self._pending = None
                    self._event.clear()
                if fn is not None:
                    try:
                        fn()
                    except Exception:
                        traceback.print_exc()
        else:
            while True:
                fn = self._q.get()
                try:
                    fn()
                except Exception:
                    traceback.print_exc()
