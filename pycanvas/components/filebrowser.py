"""FileBrowser: navigate a directory tree and pick a file, from Python.

The browser can't read the host filesystem — Python can — so the split is the
package's usual one: Python lists a directory and ``push()``es the entries; the
iframe renders them and ``canvas.send``s back the folder the user clicked or the
file they selected; Python resolves it and pushes the next listing. The current
directory lives in Python, never in the browser, and every path the browser asks
for is resolved **inside a fixed ``root``** — a viewer can't ``..`` its way out
onto the rest of the disk (important once you ``serve(host="0.0.0.0")`` or
tunnel).

Built on :class:`Custom`, so it needs no new frontend shape and no ``npm`` build.

    files = canvas.file_browser("files", root="./data")

    @files.on_select
    def run(path):              # a file was clicked
        plot.update(my_pipeline(path))
"""

import fnmatch
import os
import traceback

from .custom import Custom


# The iframe UI: a header (current path + an "up" button) over a scrolling list
# of entries. It owns no filesystem knowledge — it renders whatever listing
# Python pushes and reports clicks back by name. Folders sort first.
_FILE_BROWSER_HTML = """
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #0f172a;
         color: #e2e8f0; font-size: 13px; height: 100vh; display: flex;
         flex-direction: column; }
  #bar { display: flex; align-items: center; gap: 6px; padding: 6px 8px;
         background: #1e293b; border-bottom: 1px solid #334155; flex: none; }
  #up { cursor: pointer; border: 1px solid #475569; background: #0f172a;
        color: #e2e8f0; border-radius: 4px; padding: 2px 8px; line-height: 1.4; }
  #up[disabled] { opacity: .35; cursor: default; }
  #cwd { font-family: ui-monospace, monospace; color: #94a3b8; overflow: hidden;
         text-overflow: ellipsis; white-space: nowrap; direction: rtl;
         text-align: left; flex: 1; }
  #list { flex: 1; overflow-y: auto; }
  .row { display: flex; align-items: center; gap: 8px; padding: 4px 10px;
         cursor: pointer; user-select: none; }
  .row:hover { background: #1e293b; }
  .row.sel { background: #1d4ed8; }
  .ico { width: 16px; text-align: center; flex: none; }
  .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .sz { color: #64748b; font-variant-numeric: tabular-nums; flex: none; }
  .row.sel .sz { color: #cbd5e1; }
  #empty { padding: 12px; color: #64748b; }
</style>

<div id="bar">
  <button id="up" title="up one level">..</button>
  <span id="cwd">/</span>
</div>
<div id="list"></div>

<script>
  const upBtn = document.getElementById('up')
  const cwdEl = document.getElementById('cwd')
  const listEl = document.getElementById('list')

  upBtn.addEventListener('click', () => canvas.send({ event: 'up' }))

  function fmtSize(n) {
    if (!n) return ''
    const u = ['B', 'KB', 'MB', 'GB', 'TB']
    let i = 0
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
    return (i ? n.toFixed(1) : n) + ' ' + u[i]
  }

  function render(state) {
    cwdEl.textContent = state.cwd
    upBtn.disabled = state.atRoot
    listEl.innerHTML = ''
    if (!state.entries.length) {
      const e = document.createElement('div')
      e.id = 'empty'
      e.textContent = '(empty)'
      listEl.appendChild(e)
      return
    }
    for (const ent of state.entries) {
      const row = document.createElement('div')
      row.className = 'row'
      if (!ent.dir && ent.name === state.selected) row.className += ' sel'
      const ico = document.createElement('span')
      ico.className = 'ico'
      ico.textContent = ent.dir ? '\\u{1F4C1}' : '\\u{1F4C4}'
      const nm = document.createElement('span')
      nm.className = 'nm'
      nm.textContent = ent.name
      const sz = document.createElement('span')
      sz.className = 'sz'
      sz.textContent = ent.dir ? '' : fmtSize(ent.size)
      row.append(ico, nm, sz)
      // Python is authoritative: it decides whether the name is a folder to
      // enter or a file to select, so the row just reports what was clicked.
      row.addEventListener('click', () => canvas.send({ event: 'open', name: ent.name }))
      listEl.appendChild(row)
    }
  }

  canvas.onPush(render)
  // Ask for the first listing once the receive side is wired up. Re-fires after
  // a reconnect, since the iframe reloads and re-runs this script.
  canvas.send({ event: 'ready' })
</script>
"""


class FileBrowser(Custom):
    """A sandboxed directory browser whose file selections fire Python callbacks.

    Point it at a ``root`` directory; the user navigates folders inside it and
    clicks a file to select it. ``@browser.on_select`` handlers fire with the
    selected file's absolute path; ``@browser.on_navigate`` (optional) fires with
    the new directory whenever it changes. ``value`` reads the last selected path.

    All navigation is confined to ``root`` — requested paths are resolved with
    :func:`os.path.realpath` and rejected if they escape it (symlinks included).
    """

    component = "Custom"  # rendered as a Custom panel; no bespoke frontend shape
    default_w = 320
    default_h = 420

    def __init__(self, root=".", name="files", label=None, width=320, height=420,
                 pattern=None, show_hidden=False):
        super().__init__(html=_FILE_BROWSER_HTML, name=name, label=label,
                         width=width, height=height)
        # Resolve the sandbox root once; every later path is checked against it.
        self._root = os.path.realpath(root)
        self._cwd = self._root
        # Optional fnmatch filter applied to *files* (folders always show so the
        # tree stays navigable), e.g. ``pattern="*.csv"``.
        self._pattern = pattern
        self._show_hidden = show_hidden
        self._select_cbs = []
        self._nav_cbs = []
        self._value = None
        # Wire the iframe protocol onto Custom's event router.
        self.on("ready")(self._on_ready)
        self.on("up")(self._on_up)
        self.on("open")(self._on_open)

    # -- read ----------------------------------------------------------------
    @property
    def cwd(self):
        """The absolute path of the directory currently shown."""
        return self._cwd

    @property
    def root(self):
        """The absolute sandbox root; navigation can't go above this."""
        return self._root

    # -- public decorators ---------------------------------------------------
    def on_select(self, fn):
        """Decorator: handler fired with the absolute path of a clicked file."""
        self._select_cbs.append(fn)
        return fn

    def on_navigate(self, fn):
        """Decorator: handler fired with the new directory when it changes."""
        self._nav_cbs.append(fn)
        return fn

    # -- Python-driven control ----------------------------------------------
    def go(self, path):
        """Navigate to ``path`` (a directory inside ``root``), live.

        ``path`` may be absolute or relative to the current directory. Outside
        ``root`` or not a directory, it's ignored.
        """
        target = os.path.realpath(os.path.join(self._cwd, path))
        if self._within_root(target) and os.path.isdir(target):
            self._cwd = target
            self._push_listing()
            self._fire_nav()

    def refresh(self):
        """Re-read the current directory and push it (e.g. after files change)."""
        self._push_listing()

    # -- routing handlers (browser -> Python) --------------------------------
    def _on_ready(self, _msg):
        self._push_listing()

    def _on_up(self, _msg):
        if self._cwd != self._root:
            self._cwd = os.path.dirname(self._cwd)
            self._push_listing()
            self._fire_nav()

    def _on_open(self, msg):
        name = msg.get("name")
        if not isinstance(name, str) or not name:
            return
        target = os.path.realpath(os.path.join(self._cwd, name))
        # Reject anything that resolves outside the sandbox or has vanished.
        if not self._within_root(target) or not os.path.exists(target):
            return
        if os.path.isdir(target):
            self._cwd = target
            self._push_listing()
            self._fire_nav()
        else:
            with self._lock:
                self._value = target
            self._push_listing()  # re-render to highlight the selection
            for cb in self._select_cbs:
                try:
                    cb(target)
                except Exception:
                    traceback.print_exc()

    # ``.value`` is the selected file path, set above — not the raw inbound
    # message — so we route without the value-stashing Custom does by default.
    def _handle_input(self, payload):
        event = payload.get(self._event_key) if isinstance(payload, dict) else None
        handlers = list(self._routes.get(event, []))
        if event is not None:
            handlers += self._routes.get(None, [])
        for cb in handlers:
            try:
                cb(payload)
            except Exception:
                traceback.print_exc()

    # -- helpers -------------------------------------------------------------
    def _within_root(self, path):
        return path == self._root or path.startswith(self._root + os.sep)

    def _fire_nav(self):
        for cb in self._nav_cbs:
            try:
                cb(self._cwd)
            except Exception:
                traceback.print_exc()

    def _listing(self):
        try:
            names = os.listdir(self._cwd)
        except OSError:
            return []
        entries = []
        for n in names:
            if not self._show_hidden and n.startswith("."):
                continue
            p = os.path.join(self._cwd, n)
            is_dir = os.path.isdir(p)
            if not is_dir and self._pattern and not fnmatch.fnmatch(n, self._pattern):
                continue
            try:
                size = 0 if is_dir else os.path.getsize(p)
            except OSError:
                size = 0
            entries.append({"name": n, "dir": is_dir, "size": size})
        # Folders first, then files, each alphabetical (case-insensitive).
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return entries

    def _push_listing(self):
        rel = os.path.relpath(self._cwd, self._root)
        display = "/" if rel == "." else "/" + rel.replace(os.sep, "/")
        selected = os.path.basename(self._value) if self._value else None
        self.push({
            "cwd": display,
            "atRoot": self._cwd == self._root,
            "selected": selected,
            "entries": self._listing(),
        })
