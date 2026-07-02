"""Deep (nested) call tracing for the dispatch tracer — opt-in.

The shallow trace (see :meth:`components.base.BaseComponent._dispatch_callbacks`)
reports each *handler* as it runs: queued → start → done/error. With deep tracing
on, a :func:`sys.setprofile` probe — installed only for the duration of one
handler, and only while a trace is being observed — also records the calls that
handler makes *into your own project*, with their nesting depth, so a trace panel
can show which function called which, indented.

Only your project's functions are recorded: calls into danvas, the standard
library, or third-party packages are skipped (so the tree is your code, not
library internals), and C-level calls never enter the picture. "Your project" is
the directory tree of the running script (``__main__``); the probe is scoped to a
single handler and torn down in a ``finally``, so nothing leaks between handlers
or onto other threads.

Deep tracing reuses the same ``start``/``done``/``error`` phases as the shallow
trace, just with a ``depth`` (0 = the handler itself, 1 = a function it calls,
…), so a consumer renders both the same way — flat or indented.
"""

import os
import sys
import time

# This package's directory — used to keep danvas's own internals out of the
# trace even though a callback into user code may pass back through danvas frames.
_DANVAS_PKG_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))

_UNSET = object()
_PROJECT_ROOT = _UNSET


def _project_root():
    """The directory tree counted as "your project": where ``__main__`` lives.

    Resolved once and cached. ``None`` when there's no script file (an interactive
    session), which disables deep tracing — there's no project tree to scope to."""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is _UNSET:
        main = sys.modules.get("__main__")
        path = getattr(main, "__file__", None)
        _PROJECT_ROOT = (os.path.normcase(os.path.dirname(os.path.abspath(path)))
                         if path else None)
    return _PROJECT_ROOT


def is_user_code(code):
    """True for a code object defined in the user's project but not inside danvas.

    Keeps the nested trace to the user's own functions: under the project root,
    and not part of the danvas package (a relative panel's project may sit beside
    a vendored danvas, so the package exclusion is explicit)."""
    root = _project_root()
    if root is None:
        return False
    path = os.path.normcase(os.path.abspath(code.co_filename))
    return (path.startswith(root + os.sep)
            and not path.startswith(_DANVAS_PKG_DIR + os.sep))


def _label(code):
    name = getattr(code, "co_qualname", None) or code.co_name
    return f"{name} ({os.path.basename(code.co_filename)}:{code.co_firstlineno})"


class _CallTracer:
    """A ``sys.setprofile`` callback that emits depth-tagged start/done events for
    user-code calls. Counts only user frames, so intermediate danvas/library
    frames don't inflate the depth — the indentation reflects *your* call nesting."""

    def __init__(self, bridge, meta, traceable):
        self._bridge = bridge
        self._meta = meta
        self._traceable = traceable      # (code) -> bool
        self._open = 0                   # user frames currently on the stack
        self._stack = []                 # (fid, perf_counter) per open user frame
        self._fid_base = f"{meta.get('trace')}:{meta.get('seq')}"
        self._n = 0

    def __call__(self, frame, event, arg):
        if event == "call":
            code = frame.f_code
            if not self._traceable(code):
                return
            self._n += 1
            fid = f"{self._fid_base}:{self._n}"      # unique per nested call
            self._bridge._emit_dispatch({
                **self._meta, "phase": "start", "depth": self._open, "fid": fid,
                "handler": _label(code), "t": time.perf_counter()})
            self._stack.append((fid, time.perf_counter()))
            self._open += 1
        elif event == "return":
            code = frame.f_code
            if not self._traceable(code):
                return
            self._open = max(0, self._open - 1)
            fid, t0 = self._stack.pop() if self._stack else (None, time.perf_counter())
            self._bridge._emit_dispatch({
                **self._meta, "phase": "done", "depth": self._open, "fid": fid,
                "handler": _label(code), "t": time.perf_counter(),
                "dur_ms": (time.perf_counter() - t0) * 1000.0})


# The on-canvas trace viewer — a danvas React panel authored here in Python, so
# it needs no change to the built frontend. It subscribes to the pushed dispatch
# stream (canvas.trace wires on_dispatch -> panel.push), groups events by their
# action's ``trace`` id, pairs start↔done by ``fid``, and renders each action's
# (nested) call tree indented by ``depth`` — amber while running, green when done,
# red on error. Newest action on top; history is capped so it stays light.
PANEL_JSX = r"""
function Component({ canvas, props }) {
  // Seed from the history recorded before this panel opened (props replay on
  // mount); map the Python status names onto the panel's short ones.
  const seed = () => ((props && props.history) || []).map((a) => ({
    id: a.trace, comp: a.comp, event: a.event,
    frames: (a.frames || []).map((f) => ({
      fid: f.fid, depth: f.depth, handler: f.handler, mode: f.mode,
      status: f.status === 'error' ? 'err' : (f.status === 'done' ? 'ok' : 'run'),
      dur: f.dur_ms,
    })),
  }))
  const [traces, setTraces] = React.useState(seed)
  const [threads, setThreads] = React.useState([])
  React.useEffect(() => canvas.onFrame((e) => {
    if (!e) return
    if (e.threads) { setThreads(e.threads); return }   // a live-threads snapshot
    if (!e.phase) return
    setTraces((prev) => {
      const next = prev.slice()
      let t = next.find((x) => x.id === e.trace)
      if (!t) {
        t = { id: e.trace, comp: e.comp, event: e.event, frames: [] }
        next.push(t)
        while (next.length > 60) next.shift()
      }
      if (e.phase === 'start') {
        t.frames = t.frames.concat([{ fid: e.fid, depth: e.depth,
          handler: e.handler, mode: e.mode, status: 'run', dur: null }])
      } else if (e.phase === 'done' || e.phase === 'error') {
        t.frames = t.frames.map((f) => f.fid === e.fid
          ? { ...f, status: e.phase === 'error' ? 'err' : 'ok', dur: e.dur_ms } : f)
      }
      return next
    })
  }), [])
  const COLOR = { run: '#d9a441', ok: '#3fa45b', err: '#d4483b' }
  const MARK = { run: '▶', ok: '✓', err: '✗' }
  const TXT = { run: '..', ok: 'OK', err: 'XX' }
  const copyAction = (t) => {
    const lines = ['#' + t.id + ' ' + t.comp + ' · ' + t.event]
    t.frames.forEach((f) => lines.push(
      '  '.repeat(f.depth + 1) + TXT[f.status] + ' ' + f.handler +
      (f.dur != null ? ' ' + Math.round(f.dur) + 'ms' : '')))
    if (navigator.clipboard) navigator.clipboard.writeText(lines.join('\n'))
  }
  return (
    <div style={{ font: '12px ui-monospace, monospace', padding: '6px 8px',
      height: '100%', overflow: 'auto', boxSizing: 'border-box', userSelect: 'text' }}>
      {threads.length > 0 &&
        <div style={{ marginBottom: 8, paddingBottom: 6,
          borderBottom: '1px solid rgba(128,128,128,0.3)' }}>
          <div style={{ opacity: 0.55, fontSize: 11, marginBottom: 2 }}>
            live threads ({threads.length})
          </div>
          {threads.map((th, i) => (
            <div key={i} style={{ color: '#5aa9d6', whiteSpace: 'nowrap' }}>
              ● {th.name}{th.daemon ? '' : ' · non-daemon'}
            </div>
          ))}
        </div>}
      {traces.length === 0 &&
        <div style={{ opacity: 0.5 }}>interact with the canvas to see handlers run…</div>}
      {traces.slice().reverse().map((t) => (
        <div key={t.id} style={{ marginBottom: 8, paddingLeft: 6,
          borderLeft: '2px solid rgba(128,128,128,0.35)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
            alignItems: 'center', marginBottom: 2 }}>
            <span style={{ opacity: 0.55, fontSize: 11 }}>
              #{t.id} {t.comp} · {t.event}
            </span>
            <button onClick={() => copyAction(t)} title="copy this trace"
              style={{ fontSize: 10, cursor: 'pointer', opacity: 0.65,
                background: 'none', color: 'inherit', padding: '0 4px',
                border: '1px solid rgba(128,128,128,0.4)', borderRadius: 3 }}>copy</button>
          </div>
          {t.frames.map((f, i) => (
            <div key={i} style={{ paddingLeft: f.depth * 14, color: COLOR[f.status],
              whiteSpace: 'nowrap' }}>
              {MARK[f.status]} {f.handler}
              {f.dur != null &&
                <span style={{ opacity: 0.5 }}> {Math.round(f.dur)}ms</span>}
              {f.depth === 0 &&
                <span style={{ opacity: 0.4 }}> ({f.mode})</span>}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
"""


def start_thread_sampler(canvas, panel, interval=1.5):
    """Stream a snapshot of the living background threads to the trace ``panel``.

    Every ``interval`` seconds (until the panel is closed) push the threads that
    are *ongoing* — ``@canvas.background`` producer loops, ``threaded=True``
    handlers still running, any non-daemon thread the app started — so the trace
    view shows long-lived work, not just the handlers that fired and finished.
    Daemon infrastructure (uvicorn/asyncio) is filtered out to keep it to the
    threads you care about. The sampler is itself a daemon thread that exits once
    the panel is gone."""
    import threading
    import time as _time

    def loop():
        me = threading.current_thread()
        while panel.id in canvas._bridge._components:
            rows = []
            for t in threading.enumerate():
                if t is me or t.name == "MainThread":
                    continue
                # danvas-spawned workers, plus any non-daemon app thread.
                if not (t.name.startswith("danvas-") or not t.daemon):
                    continue
                rows.append({"name": t.name, "daemon": t.daemon})
            try:
                panel.push({"threads": rows})
            except Exception:
                pass
            _time.sleep(interval)

    threading.Thread(target=loop, name="danvas-trace-sampler", daemon=True).start()


def run_calls(bridge, cb, args, meta, traceable=is_user_code):
    """Run ``cb(*args)`` with a nested-call probe installed on this thread.

    Emits ``start``/``done`` (with ``depth``) for every user-code call the handler
    makes, the handler itself being depth 0. Re-raises whatever ``cb`` raises —
    the caller (``_traced``) emits the handler-level ``error`` event and does the
    console logging, exactly as on the shallow path. The previously-installed
    profiler (if any) is always restored. Returns ``cb``'s result — an ``async
    def`` handler returns its coroutine here, which the caller schedules (the
    probe is per-thread, so it can't follow into the async loop; such a handler's
    awaited part is traced shallowly)."""
    tracer = _CallTracer(bridge, meta, traceable)
    prev = sys.getprofile()
    sys.setprofile(tracer)
    try:
        return cb(*args)
    finally:
        sys.setprofile(prev)
