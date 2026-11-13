"""Static danvas: a danvas app run in the browser (Pyodide), with no server.

The page (web/ in the danvas repo) fetches a repository's
`danvas.toml`, the app's code and its files into Pyodide's filesystem, then:

    app = danvas.static.App(manifest_dict, repo_root="/repo")
    app.start()           # runs the entry; its canvas.serve() hands the canvas here
    app.connect(page)     # one in-page socket per danvas frontend connection
    app.reload()          # after new files arrive: the app's hook, else a rebuild

What changes compared with a served canvas:

- Pyodide has no threads, so danvas's kernels (and `threading.Thread` in app
  code) run on the browser's event loop instead. A handler that loops forever
  would freeze the page; `async def` loops are fine.
- `Canvas.serve()` does not serve. It records the view and UI flags and makes
  the canvas the one the page shows, then returns.
- Each viewer runs their own copy: what they draw is theirs (`drawings_json`).
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import runpy
import sys
import threading
import time
import traceback

if sys.platform != "emscripten":
    # importing this swaps danvas's threads for the event loop: right in a
    # browser, ruinous in a normal process
    raise ImportError("danvas.static runs a canvas inside a browser (Pyodide); "
                      "to serve a canvas from Python, use Canvas.serve()")

_loop = asyncio.get_event_loop()


def _guard(fn):
    try:
        r = fn()
        if inspect.isawaitable(r):
            asyncio.ensure_future(r)
    except Exception:
        traceback.print_exc()


# --- no threads ---------------------------------------------------------------------

class _LoopThread:
    """threading.Thread stand-in: the target runs on the event loop, soon."""

    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None, daemon=None):
        self._target, self._args, self._kwargs = target, args, kwargs or {}
        self.name, self.daemon = name or "loop-thread", daemon

    def start(self):
        if self._target is not None:
            _loop.call_soon(_guard, lambda: self._target(*self._args, **self._kwargs))

    def run(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


class _Timer(_LoopThread):
    def __init__(self, interval, function, args=None, kwargs=None):
        super().__init__(target=function, args=args or (), kwargs=kwargs)
        self.interval, self._handle = interval, None

    def start(self):
        self._handle = _loop.call_later(self.interval, _guard, lambda: self._target(*self._args, **self._kwargs))

    def cancel(self):
        if self._handle:
            self._handle.cancel()


def _patch_threads():
    threading.Thread, threading.Timer = _LoopThread, _Timer
    # code written for a thread of its own sleeps to pace itself; here a sleep
    # would freeze the page, so it returns at once (use asyncio.sleep instead)
    time.sleep = lambda s: None

    from danvas import kernel

    def spawn(fn, *args, name=None, **kwargs):
        _loop.call_soon(_guard, lambda: fn(*args, **kwargs))

    class Kernel:
        def submit(self, fn):
            _loop.call_soon(_guard, fn)

        def is_current_thread(self):
            return False

    class DedicatedKernel(Kernel):
        def __init__(self, *a, **k):
            pass

    class AsyncKernel:
        _instance = None

        @classmethod
        def get(cls):
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

        def submit(self, coro):
            return asyncio.ensure_future(coro)

        spawn = submit

        def is_current_thread(self):
            return False

    swaps = {"spawn": spawn, "Kernel": Kernel, "AsyncKernel": AsyncKernel, "DedicatedKernel": DedicatedKernel}
    for name, new in swaps.items():
        setattr(kernel, name, new)
    # modules that did `from .kernel import X` hold the old names
    for mod in list(sys.modules.values()):
        for name, new in swaps.items():
            old = getattr(mod, name, None)
            if old is not None and getattr(old, "__module__", "") == "danvas.kernel":
                setattr(mod, name, new)


_patch_threads()

import danvas  # noqa: E402  (after the kernels are swapped)

# --- serve() hands the canvas to the page -------------------------------------------

_served: list = []


def _serve(self, *args, view=None, ui_inspector=None, ui_graveyard=None, ui_hosting=None,
           cursors=None, **kwargs):
    b = self._bridge
    v = danvas.Canvas._normalize_view(view)
    if v is not None:
        b._view = {**(b._view or {}), **v}
    # a static page is public by nature: the Inspector (a state browser) stays off
    # unless the app asks for it; hosting controls mean nothing without a server
    b._ui_inspector = bool(ui_inspector)
    b._ui_graveyard = bool(ui_graveyard) if ui_graveyard is not None else b._ui_graveyard
    b._ui_hosting = False
    b.set_loop(_loop)
    for fn, a, k in getattr(self, "_background", []):
        if inspect.iscoroutinefunction(fn):
            asyncio.ensure_future(fn(*a, **k))
        else:
            print(f"[danvas-static] background {fn.__name__} is not async; it would block the page, so it is skipped",
                  file=sys.stderr)
    _served.append(self)
    return self


danvas.Canvas.serve = _serve


# --- the app ------------------------------------------------------------------------

class App:
    def __init__(self, manifest: dict, repo_root: str = "/repo"):
        self.m = manifest.get("app", manifest)
        self.root = repo_root
        self.canvas = None
        self.reloader = None
        self.sockets: list[_Socket] = []

    # where the code is: repo folders named by `paths`, and fetched packages,
    # are put on sys.path by the page before start()
    def start(self) -> str:
        os.chdir(os.path.join(self.root, self.m.get("cwd", "")))
        if self.root not in sys.path:
            sys.path.insert(0, self.root)
        _served.clear()
        entry = self.m.get("entry", "app.py")
        result = None
        if entry.endswith(".py"):
            # a script: run as __main__, as `python app.py` would; its serve() returns at once
            runpy.run_path(os.path.join(self.root, self.m.get("cwd", ""), entry), run_name="__main__")
        else:
            mod, _, fn = entry.partition(":")
            result = getattr(importlib.import_module(mod), fn or "app")()
        if isinstance(result, danvas.Canvas) and result not in _served:
            _serve(result)
        if not _served:
            raise RuntimeError(f"{entry} built no canvas: it should call canvas.serve() or return the canvas")
        self.canvas = _served[-1]
        # an entry may return a reload hook (or an object with .reload): called
        # when new files arrive, instead of rebuilding the canvas from scratch
        self.reloader = result.reload if hasattr(result, "reload") else result if callable(result) else None
        return f"{entry}: {len(self.canvas.components)} panels"

    def reload(self) -> str:
        """New files are in place. Let the app update in place, else rebuild:
        the frontend reconnects to the new canvas, keeping this viewer's drawings."""
        if self.reloader is not None:
            return str(self.reloader())
        drawings = self.canvas._bridge._drawings if self.canvas else {}
        for mod in [m for m, v in sys.modules.items() if _from_repo(v, self.root)]:
            del sys.modules[mod]
        summary = self.start()
        self.canvas._bridge._drawings.update(drawings)
        for s in list(self.sockets):
            s.close_from_server()
        return "rebuilt: " + summary

    # --- this viewer's drawings ----------------------------------------------------

    def drawings_json(self) -> str:
        return json.dumps(self.canvas._bridge._drawings) if self.canvas else "{}"

    def load_drawings(self, text: str) -> None:
        try:
            self.canvas._bridge._drawings.update(json.loads(text or "{}"))
        except ValueError:
            pass

    # --- the socket ------------------------------------------------------------------

    def connect(self, js, query=None, ua=None):
        """`js` is the page's end of the socket: deliver(text), deliver_bytes(u8), closed(code).
        A guest of a live room brings its viewer identity (`query`: vid, vname,
        vcolor) and browser (`ua`), as a browser connecting to a served canvas would."""
        ws = _Socket(js, self)
        if query:
            ws.query_params = {str(k): str(v) for k, v in (query.to_py() if hasattr(query, "to_py") else query).items()}
        if ua:
            ws.headers = {"user-agent": str(ua)}
        self.sockets.append(ws)
        asyncio.ensure_future(self.canvas._bridge.handle_connection(ws))
        return ws


def _from_repo(mod, root) -> bool:
    f = getattr(mod, "__file__", None) or ""
    return f.startswith(root + "/")


class _Socket:
    """The slice of Starlette's WebSocket that danvas's Bridge uses."""

    def __init__(self, js, app: App):
        self.js, self.app = js, app
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.query_params: dict = {}
        self.headers: dict = {"user-agent": "browser"}

    def push(self, text: str) -> None:
        self.inbox.put_nowait({"type": "websocket.receive", "text": text})

    def push_bytes(self, data) -> None:
        self.inbox.put_nowait({"type": "websocket.receive", "bytes": bytes(data.to_py() if hasattr(data, "to_py") else data)})

    def drop(self) -> None:
        self.inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})
        if self in self.app.sockets:
            self.app.sockets.remove(self)

    def close_from_server(self) -> None:
        self.drop()
        self.js.closed(1012)

    async def accept(self):
        pass

    async def receive(self):
        return await self.inbox.get()

    async def send_text(self, text):
        self.js.deliver(text)

    async def send_bytes(self, data):
        from pyodide.ffi import to_js
        self.js.deliver_bytes(to_js(bytes(data)))

    async def close(self, code=1000, reason=None):
        self.js.closed(code)


# --- helpers for the page -------------------------------------------------------------

def read_manifest(text: str) -> dict:
    import tomllib
    return tomllib.loads(text)


def extra_files(manifest: dict, repo_root: str = "/repo") -> list[str]:
    """Paths the entry's package asks for beyond the manifest's `files`: a module
    named by `entry` may define files() -> paths or folder/ prefixes relative to
    the app's cwd, reading what it needs (e.g. its own config) from the files
    already fetched. Returned relative to the repo root."""
    import posixpath
    m = manifest.get("app", manifest)
    entry = m.get("entry", "")
    if entry.endswith(".py") or not entry:
        return []
    cwd = m.get("cwd", "")
    os.chdir(posixpath.join(repo_root, cwd))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    mod = importlib.import_module(entry.partition(":")[0])
    hook = getattr(mod, "files", None)
    if not callable(hook):
        return []
    out = []
    for p in hook():
        out.append(posixpath.normpath(posixpath.join(cwd, p)) + ("/" if p.endswith("/") else ""))
    return out


def requirements(pyproject_text: str) -> list[str]:
    """A fetched package's own dependencies, from its pyproject.toml (danvas is
    already here, so it is dropped)."""
    import re
    import tomllib
    deps = tomllib.loads(pyproject_text).get("project", {}).get("dependencies", [])
    return [d for d in deps if re.split(r"[<>=!~ ;\[]", d, maxsplit=1)[0].strip().lower() != "danvas"]
