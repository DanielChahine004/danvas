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
max_image = 1200               # optional: shrink big pictures as they are fetched
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
- **What a viewer adds.** Drawings, shapes, text and notes made with danvas's
  tools (a live room's guests' included) are kept in that viewer's browser, per
  repository, and come back when they open the link again; nobody else sees
  them, and the repository is never changed. The page says so when it puts them
  back, and **clear mine** beside **Share live** removes them. Preferences
  (theme, colours, tool) are kept per browser too. Clearing the site's data in
  the browser removes all of it.
- **Images.** `max_image = 1200` under `[app]` shrinks PNG/JPEG/WebP files
  wider or taller than that as they are fetched (opaque PNGs become JPEG data,
  keeping their names), so a map with many figures fits in a phone's memory.

## What changes in the browser

- Each viewer runs their own copy of the canvas, unless one of them clicks
  **Share live**: their tab then hosts a live room, and whoever opens its link
  joins that same canvas as another viewer, with shared drawings, cursors and
  panels, as on a served danvas. The relay is [`rooms/`](rooms/README.md).
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
- Only what a browser may do: no local files or sockets, and only
  pure-Python packages or ones Pyodide builds.
- A serial device on the reader's computer, through `danvas.webserial.Serial`
  (Web Serial: Chrome or Edge on a computer). The page holds the port and the
  reader chooses the device in the browser's picker, so call `open()` from a
  button's handler; if the click is too long ago for the browser, the page
  asks with its own "Choose a device" prompt, and a device chosen before is
  reused on the next visit. The same code opens a named port with pyserial
  when the app runs as a script. See
  [examples/static_serial](../examples/static_serial).
- `[[app.mount]]` (`repo`, `files`, `at`, optional `ref`) adds files from a
  second repository, fetched with the reader's sign-in: a reader who cannot
  read it gets the app without them. A mount from another owner's repository
  is asked about first. E.g. figures kept in a private papers repo show on a
  public notes map for collaborators only.
- A private repository needs **Sign in with GitHub** (on the picker, or offered
  when GitHub says "not found"), and the
  [sign-in app](https://github.com/apps/danvas-daniel) given access to that
  repository. Sign-in goes through the worker in [`auth/`](auth/README.md),
  which holds the app's client secret. The token is kept
  for the tab only, renewed before it expires, and never passed to the app's
  code. `#token=<fine-grained token, Contents: read>` works too.

## Building and previewing

    python web/build.py                      # -> web/site/, the whole deployable
    python web/dev_server.py --repo you/notes=../notes
    # open http://127.0.0.1:8765/?repo=you/notes

`dev_server.py` answers the page's GitHub requests for the repos it names from
local folders (any other repo still comes from GitHub) and the page polls them
every 2 s, so a saved edit shows within seconds. `?debug` keeps every frame in
`window.__dv`. `.github/workflows/pages.yml` publishes `web/site` to GitHub Pages.

`danvas/static.py` is the runtime; it refuses to import outside a browser.
