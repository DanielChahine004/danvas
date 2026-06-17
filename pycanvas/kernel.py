"""Serial code execution off the event loop, with output capture.

A REPL cell's run request arrives on the asyncio loop thread (see
``Bridge._on_message``). Executing user code there would block the event loop
and freeze the whole canvas, so it is handed to a :class:`Kernel`: a single
daemon thread that runs submitted callables one at a time. One shared kernel per
canvas gives every REPL cell Jupyter-like semantics -- one statement runs at a
time, against a shared namespace.
"""

import ast
import contextlib
import io
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

    def _run(self):
        while True:
            fn = self._q.get()
            try:
                fn()
            except Exception:
                traceback.print_exc()


def run_code(code, ns):
    """Exec ``code`` against namespace ``ns``, Jupyter-style.

    stdout, stderr and any traceback are captured into the returned text. The
    last top-level expression (if the cell ends in one) is evaluated and its
    ``repr`` returned separately, like Jupyter's ``Out[]``.

    Returns ``(output_text, result_repr_or_None)``. ``redirect_stdout`` is
    process-global, so this is only safe because the :class:`Kernel` runs one
    job at a time -- never call it from two threads at once.
    """
    buf = io.StringIO()
    result = None
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            tree = ast.parse(code)
            last_expr = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last_expr = tree.body.pop()
            if tree.body:
                exec(compile(tree, "<repl>", "exec"), ns)
            if last_expr is not None:
                value = eval(
                    compile(ast.Expression(last_expr.value), "<repl>", "eval"), ns
                )
                if value is not None:
                    result = repr(value)
        except Exception:
            traceback.print_exc()
    return buf.getvalue(), result
