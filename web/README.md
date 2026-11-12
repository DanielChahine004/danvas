# Static danvas

One static page that runs **a GitHub repository's danvas app in the reader's
browser**, with no server to keep alive:

    https://danielchahine004.github.io/danvas/?repo=owner/name[&ref=branch][&app=path/danvas.toml]

danvas's Python bridge and the app's own Python run in
[Pyodide](https://pyodide.org) inside a Web Worker (`worker.js`); danvas's
frontend, unchanged, talks to them through a socket that relays to the worker
instead of a WebSocket. The app is the same code that
`canvas.serve()` would host: a script such as `examples/hello_world.py` runs
as written.

## The repository's `danvas.toml`

```toml
[app]
entry    = "app.py"            # a script that calls canvas.serve(), or "module:function"
requires = ["github:owner/pkg@v1.2"]   # code from another repo, pinned; plain names install from PyPI
paths    = ["tools/pkg/src"]   # ...or code kept in this repo, put on sys.path
packages = ["numpy"]           # extra Pyodide/PyPI packages
files    = ["data/"]           # what the app reads (files or folder/ prefixes)
cwd      = ""                  # folder the app runs in, relative to the repo
```

- **Where the code comes from.** `requires = ["github:…@tag"]` uses a shared,
  pinned version from its own repository (its `src/` if it has one); `paths`
  uses a copy kept in this repository, free to edit. A fetched package's
  `pyproject.toml` dependencies are installed too.
- **What is read.** `files`, plus anything the entry module's `files()`
  function returns (paths relative to `cwd`), so a package can name what its own
  config points at. [notesmap](https://github.com/DanielChahine004/notesmap)
  does this: its repos need only `files = ["notesmap.toml"]`.
- **Updates.** The page checks the repository for new commits every minute,
  fetches what changed, and calls the reload hook the entry returned (a callable,
  or an object with `.reload()`), else rebuilds the canvas.
- **Drawings.** What a viewer draws with danvas's tools is kept in their
  browser, per repository.

## What changes in the browser

- Each viewer runs their own copy of the canvas: no shared live session.
- There are no threads. danvas's handler kernels and `threading.Thread` run on
  the worker's event loop, and `time.sleep` returns at once. Handlers that
  return promptly work as they do served; `@canvas.background` loops must be
  `async def`. A slow handler delays the app's replies but never freezes the
  canvas, which runs on the page.
- The app is kept apart from the page. Its code (Python, and JavaScript reached
  through Pyodide's `js` module) runs in the worker, which has no DOM, no
  `localStorage` or `sessionStorage`, and makes no GitHub requests: it cannot
  read other repositories' saved drawings or a GitHub token. The page fetches
  the repository and hands the worker the files.
- `canvas.serve(...)` hands the canvas to the page and returns; its view and
  Inspector settings apply, its network settings do not. The Inspector stays
  off unless asked for.
- Only what a browser may do: no local files, hardware or sockets, and only
  pure-Python packages or ones Pyodide builds.
- A private repository needs `#token=<fine-grained token, Contents: read>` for
  now, kept for the tab only and never passed to the app's code.

## Building and previewing

    python web/build.py                      # -> web/site/, the whole deployable
    python web/dev_server.py --repo you/notes=../notes
    # open http://127.0.0.1:8765/?repo=you/notes

`dev_server.py` answers the page's GitHub requests for the repos it names from
local folders (any other repo still comes from GitHub) and the page polls them
every 2 s, so a saved edit shows within seconds. `?debug` keeps every frame in
`window.__dv`. `.github/workflows/pages.yml` publishes `web/site` to GitHub Pages.

`danvas/static.py` is the runtime; it refuses to import outside a browser.
