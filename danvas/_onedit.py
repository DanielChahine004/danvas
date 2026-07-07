"""Per-function hot reload as a handler trigger: ``canvas.on_edit`` / ``canvas.live``.

The middle rung of the reload ladder. Below it: nothing (restart to see an
edit). Above it: ``serve(hot_reload=True)``, which restarts the whole process
(the browser survives via the broker). This rung reloads **one named
top-level function** when its *source* changes on disk — the process, its
state (open serial ports, warmed-up kernels, loaded models), and every panel
stay put; only the function is re-compiled and rebound::

    @canvas.live                    # rebind on save AND re-run
    def update_geometry():
        ...

    @canvas.on_edit("update_geometry")   # or: decide the policy yourself
    def _(fresh_fn):
        run_sanity_checks(fresh_fn)
        fresh_fn()

Semantics and boundaries (deliberate):

- Watches **top-level ``def``s by name**, per file. The trigger is the
  function's own source segment changing — saving an unrelated edit in the
  same file does not fire.
- The fresh definition is compiled with its ORIGINAL line numbers (tracebacks
  point at the real file) and re-exec'd into the function's module globals —
  so the global name is rebound *before* the handler runs; callers that look
  the function up by name (the usual case) pick it up automatically.
- A syntax error in the saved file keeps the previous definition and prints
  where the error is; the watch stays armed for the next save.
- What does NOT reload: decorator registrations that captured the old object
  (an ``@slider.on_change`` handler), closures/state, new module-level
  imports (import inside the function, or restart). For those, use
  ``serve(hot_reload=True)`` — and note that with hot_reload on, the monitor
  restarts the process on save anyway, which supersedes this mechanism.

Handlers run on the canvas's input-dispatch thread when it is serving (an
edit is an event like any other and must not race input handlers), else
directly on the watcher thread. Rapid saves coalesce: only the latest
surviving source is compiled.
"""

import ast
import os
import threading
import time
import traceback

_POLL_S = 0.4
# A save can be a two-step write (truncate + write); after an mtime change we
# wait a beat and re-stat until it holds still before reading.
_SETTLE_S = 0.15


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


class _Watch:
    __slots__ = ("name", "handlers", "src", "globals_")

    def __init__(self, name, globals_, src):
        self.name = name
        self.globals_ = globals_
        self.src = src           # last-known source segment of the def
        self.handlers = []


class EditWatcher:
    """One per canvas: polls watched files, fires per-function edit handlers."""

    def __init__(self, canvas):
        self._canvas = canvas
        self._files = {}         # path -> {"mtime": float, "watches": {name: _Watch}}
        self._lock = threading.Lock()
        self._thread = None

    def add(self, path, name, handler, globals_):
        """Watch top-level ``def name`` in ``path``; ``handler(fresh_fn)`` on change."""
        path = os.path.abspath(path)
        src = open(path, encoding="utf-8").read()
        node = _function_node(ast.parse(src, filename=path), name)
        if node is None:
            raise NameError(f"no top-level `def {name}` in {path}")
        with self._lock:
            entry = self._files.setdefault(
                path, {"mtime": os.stat(path).st_mtime, "watches": {}})
            watch = entry["watches"].get(name)
            if watch is None:
                watch = entry["watches"][name] = _Watch(
                    name, globals_, ast.get_source_segment(src, node))
            watch.handlers.append(handler)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="danvas-on-edit", daemon=True)
                self._thread.start()

    # -- the watcher thread ----------------------------------------------------
    def _run(self):
        while True:
            time.sleep(_POLL_S)
            with self._lock:
                items = [(p, e) for p, e in self._files.items()]
            for path, entry in items:
                try:
                    mtime = os.stat(path).st_mtime
                except OSError:
                    continue              # editor swap-file window; retry next poll
                if mtime == entry["mtime"]:
                    continue
                # Let the write settle (atomic-rename editors, two-step writers).
                time.sleep(_SETTLE_S)
                try:
                    entry["mtime"] = os.stat(path).st_mtime
                    src = open(path, encoding="utf-8").read()
                except OSError:
                    continue
                self._check(path, entry, src)

    def _check(self, path, entry, src):
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            print(f"[danvas.on_edit] keeping old code — syntax error at "
                  f"{e.filename}:{e.lineno}: {e.msg}")
            return
        for name, watch in list(entry["watches"].items()):
            node = _function_node(tree, name)
            if node is None:
                print(f"[danvas.on_edit] `def {name}` disappeared from "
                      f"{path}; keeping the old definition")
                continue
            segment = ast.get_source_segment(src, node)
            if segment == watch.src:
                continue                  # an edit elsewhere in the file
            watch.src = segment
            try:
                code = compile(ast.Module(body=[node], type_ignores=[]),
                               path, "exec")
                exec(code, watch.globals_)          # rebinds the module global
                fresh = watch.globals_[name]
            except Exception:
                print(f"[danvas.on_edit] reload of {name} failed:")
                traceback.print_exc()
                continue
            for handler in list(watch.handlers):
                self._dispatch(handler, fresh)

    def _dispatch(self, handler, fresh):
        def run():
            try:
                handler(fresh)
            except Exception:
                traceback.print_exc()
        bridge = getattr(self._canvas, "_bridge", None)
        dispatch = getattr(bridge, "_dispatch", None)
        if dispatch is not None:
            dispatch.submit(run)          # serialized with input handlers
        else:
            run()
