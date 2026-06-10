# PyCanvas — Conceptual Guide

A browser-based spatial canvas (tldraw) driven entirely from Python. You create
panels in Python, drop them on an infinite canvas, and read/write their values
live over a WebSocket. No frontend code required.

```python
import pycanvas

canvas = pycanvas.Canvas()
speed = canvas.insert(pycanvas.Slider("speed", min=0, max=100))
out   = canvas.insert(pycanvas.Label("readout"))

@speed.on_change
def _(v):
    out.update(f"speed = {v}")

canvas.serve()   # opens http://127.0.0.1:8000 and blocks
```

---

## Mental model

- **`Canvas`** — the one object you own. It holds components, draws arrows
  between them, and runs the web server.
- **Components** — the panels (Slider, Label, Toggle, VideoFeed, Plot, LivePlot,
  Custom). You build one, `insert` it, then `update()` it or read `.value`.
- **Bridge** — internal. A single WebSocket multiplexes every component by id.
  State lives in Python + the tldraw shape; the browser is a thin view.
- **Direction of data**:
  - Python → browser: `component.update(...)` (and `move`/`resize`/`lock`).
  - Browser → Python: user input fires your `@component.on_change` callbacks.

Everything is thread-safe: you can `update()` from any thread while the server
runs in another.

---

## The Canvas object

```python
canvas = pycanvas.Canvas()
```

| Method | Purpose |
|---|---|
| `insert(component, x=, y=, w=, h=, rotation=, locked=, movable=, resizable=, name=)` | Register a panel, place it, and return it. |
| `remove(component)` | Pull a panel off the canvas (live). |
| `connect(start, end, name=, text=, **props)` | Draw an arrow between two panels; returns an `Arrow`. `name` is identity, `text` is the caption. |
| `disconnect(arrow_or_name)` | Remove an arrow by object or name. |
| `serve(port=8000, open_browser=True, host="127.0.0.1", block=True, wait=True)` | Start server; **block** (default) or, with `block=False`, **return immediately** (Jupyter). |
| `stop()` | Shut down a background server (started with `block=False`). |
| `wait()` | Park the main thread until a background server shuts down (`Ctrl+C`) — keeps a *script* alive after `serve(block=False)`. |

**Identity vs. caption.** Every component and arrow has a unique **`name`** — the
backend identity that becomes its `canvas.<name>` attribute / `canvas["<name>"]`
key and the eviction key (inserting again under the same name replaces the old
one). `name` is **required** when you build a component (it's the first argument);
the utility panels — `Plot`, `LivePlot`, `Repl`, `Inspector`, `Custom` — default
it to their type word. A component's **`label`** (and an arrow's **`text`**) is
purely the caption shown in the UI and is optional — it defaults to the `name`.
`canvas.<name>` works as an attribute when the name is a valid identifier
(otherwise use `canvas["<name>"]`).

```python
canvas.slider("speed", label="Speed")   # name="speed", caption "Speed"
canvas.speed            # the component (by name)
canvas["speed"]         # same

canvas.slider("rpm")    # no label -> caption defaults to the name, "rpm"
```

---

## Components

### Premade

| Component | Construct | Direction | Drive it with | Read |
|---|---|---|---|---|
| **Slider** | `Slider(name, min=0, max=100, default=None, label=None)` | both | `update(value)` | `.value` (number) |
| **Toggle** | `Toggle(name, options, default=None, label=None)` | both | `update(option)` | `.value` (chosen string) |
| **Button** | `Button(name, text=None, label=None)` | in | — | `@on_click`, `.value` (click count) |
| **Label** | `Label(name, value="", label=None)` | out | `update(value)` | — |
| **VideoFeed** | `VideoFeed(name, quality=70, label=None)` | out | `update(frame)` — OpenCV BGR numpy array | — |
| **Plot** | `Plot(name="plot", label=None, width=560, height=420)` | out | `update(fig)` — a Plotly figure or HTML string | — |
| **LivePlot** | `LivePlot(name="live plot", traces=None, max_points=300, mode="lines", layout=None, ..., label=None)` | out | `push({"trace": y, ...})`, `clear()` | — |
| **Custom** | `Custom(html=None, path=None, name="custom", label=None, width=380, height=320, event_key="event")` | both | `update(html)` (reload) / `push(data)` (stream, no reload) | `@on(event)` / `@on_message`, `.value` (last message) |
| **React** | `React(source=None, path=None, name="react", label=None, width=380, height=320, props=None, event_key="event")` | both | `update(**props)` (patch) / `push(data)` (stream) | `@on(event)` / `@on_message`, `.value` (last message) |
| **FileBrowser** | `FileBrowser(root=".", name="files", label=None, width=320, height=420, pattern=None, show_hidden=False)` | both | `go(path)`, `refresh()` | `@on_select` (file path), `@on_navigate` (dir), `.value` (last file), `.cwd` |

Every component takes `name` (its unique identity) first, then an optional
`label` caption; the input components (Slider/Toggle/Label/VideoFeed) **require**
`name`, the utility panels default it. `canvas.<component>(...)` factory methods
take the same arguments.

Notes:
- **Slider / Toggle** are bidirectional — moving them in the browser fires your
  callback; `update()` moves them from Python.
- **Plot vs LivePlot**: `Plot` re-renders a Plotly iframe per update (simple,
  good for occasional figures). `LivePlot` keeps a mounted chart and streams
  only data arrays — smooth at high rates; call `push()` every loop.
- **VideoFeed** expects OpenCV BGR frames (`cv2`); they're JPEG-encoded and sent
  as binary WebSocket frames (no base64/JSON). **AudioFeed** streams int16 PCM
  the same way.

### Custom panels (arbitrary HTML)

`Custom` renders any HTML in a sandboxed iframe with a symmetric `canvas` helper:
`canvas.send(data)` posts back to Python, `canvas.onPush(fn)` receives data
Python streams in. Route inbound messages by an `event` field with
`@panel.on("event")` (or `@panel.on_message` for a catch-all) — no subclass, no
hand-written dispatcher:

```python
panel = canvas.custom(html="""
  <button onclick="canvas.send({event: 'go'})">Go</button>
""")

@panel.on("go")            # fires only for {event: 'go'}
def handle(msg):
    print(msg)             # {'event': 'go'}
```

Load from a file with `Custom(path="dashboard.html")`. Replace content live with
`panel.update(new_html)` (this reloads the iframe).

To stream live data **without** reloading — keeping the iframe's focus,
listeners and scroll intact — use `panel.push(data)`, received via
`canvas.onPush(fn)`. This suits high-rate feeds and two-way interactive panels:

```python
panel.push(frame_b64)      # Python -> iframe, no reload
```
```js
canvas.onPush((data) => render(data))   // no __pycanvas unwrapping needed
```

`examples/remote_control.py` uses exactly this to stream the machine's screen
into a panel while capturing the browser's mouse/keyboard to drive the host (a
small LAN remote desktop — note its security warning).

### React panels (your own component, rendered natively)

`React` is the native counterpart to `Custom`. Where `Custom` renders HTML in a
*sandboxed iframe* (isolated — no theme or bridge access), `React` takes JSX
**source** and mounts it as a real React subtree **inside** the panel, so it
inherits the canvas theme, dark mode and selection chrome and talks to Python
directly. The JSX is compiled in the browser at runtime (Babel, lazily loaded
the first time a React panel appears — like the Repl's Monaco), so you author
components from Python with **no `npm` build**, exactly like `Custom`.

Your source must define a `function Component`, which receives three props
(`React` and its hooks are in scope):

```python
counter = canvas.react("""
  function Component({ canvas, value, props }) {
    const [n, setN] = React.useState(0)
    return <button onClick={() => { setN(n + 1); canvas.send({ event: 'tap', n: n + 1 }) }}>
      {props.label}: {n}   {/* props from Python */}
    </button>
  }
""", props={"label": "Taps"})

@counter.on("tap")             # canvas.send -> @on, routed by the `event` field
def _(msg): print(msg["n"])

counter.update(label="Hits")   # patch props -> live re-render (merges)
counter.push(live_value)       # stream into the `value` prop, no re-mount
```

| In the component | Direction | Meaning |
|---|---|---|
| `canvas.send(data)` | panel → Python | routed to your `@on(event)` / `@on_message` handlers |
| `value` prop | Python → panel | the latest `push(data)` (no re-mount; for high-rate streams) |
| `props` prop | Python → panel | the dict from `update(**props)` / the `props=` arg; replayed on reconnect |

**Custom vs React.** Reach for `Custom` when the content is plain HTML or you
want the hard isolation of a sandbox (e.g. third-party snippets). Reach for
`React` when you want a real component with hooks, the canvas theme, and native
selection — the things the iframe can't give you. Both compile in the browser,
so neither needs a frontend rebuild. See
[`examples/react_component.py`](examples/react_component.py).

> Security note: a React panel's source runs in the **main page**, not a sandbox
> — it's your own (host-authored) code, the same trust level as the rest of the
> app. Don't feed it source from an untrusted party; use `Custom` (sandboxed) for
> that.

### File browser (pick a file, drive a pipeline)

`FileBrowser` lists a directory on the canvas and lets the user navigate folders
and select a file — the input end of a "choose a file → run something → show the
result" loop. It's a `Custom` panel under the hood (no frontend build), with the
directory listing done in Python (the browser can't read the disk):

```python
files = canvas.file_browser("files", root="./data", pattern="*.csv")
plot  = canvas.plot("result")

@files.on_select                 # fires with the chosen file's absolute path
def run(path):
    plot.update(build_figure(path))   # your pipeline drives another panel

@files.on_navigate               # optional: fires with the new dir on each cd
def _(cwd): ...
```

`root` is a **hard sandbox**: every path the browser asks for is resolved with
`realpath` and rejected if it escapes `root` (symlinks included), so navigating
"up" stops there and a remote viewer can't walk the rest of your disk — which
matters once you `serve(host="0.0.0.0")` or tunnel. `pattern` is an fnmatch glob
that filters *files* (folders always show so the tree stays navigable);
`show_hidden=False` hides dotfiles. Read the current directory with `.cwd` and the
last selected file with `.value`; drive it from Python with `go(path)` (navigate)
and `refresh()` (re-list after files change on disk). See
[`examples/file_browser.py`](examples/file_browser.py).

### Writing your own

**Prefer `Custom` or `React` first.** Between them they give you a two-way
channel, live updates without a reload, and event routing — all from user code
with **no package edit and no `npm` build**: `Custom` for sandboxed HTML, `React`
for a native component with hooks and theme access. Subclass `BaseComponent` only
when you need a genuinely new **tldraw shape** on the frontend (a bespoke canvas
render that neither an iframe nor a hosted React subtree can give you).

A subclass wires into the bridge through a handful of `BaseComponent` hooks — all
the geometry, locking and read-back machinery is inherited, so you only supply the
data behaviour:

| Hook | Direction | Purpose |
|---|---|---|
| `component` (class attr) | — | The frontend shape **type string**; must match a registered tldraw shape util in `pycanvas/frontend`. |
| `register_props()` | Python → browser | The props sent in the initial `register` message that builds the shape. Default returns your constructor `**props`; override to add fields. |
| `state_payload()` | Python → browser | State pushed *right after* register (and replayed to every reconnecting client). Return `None` for nothing. |
| `update(...)` | Python → browser | Your public write method. Call `self._send_update(payload)` (or `self._send_binary(...)` for raw frames) to push to the shape. |
| `_handle_input(payload)` | browser → Python | Called when the shape posts back. Store into `self._value` (under `self._lock`) and fan out to `self._callbacks` so `@on_change` works. |

The default `_handle_input` already stores `payload["value"]` and fires
`on_change`, so a simple bidirectional control may not need to override it. See
[`pycanvas/components/slider.py`](pycanvas/components/slider.py) (minimal) or
[`pycanvas/components/video.py`](pycanvas/components/video.py) (binary frames) as
templates.

**The catch:** the matching frontend shape must exist, which means editing the
React/tldraw frontend and rebuilding it (`npm run build` in
`pycanvas/frontend`) — the step `Custom` exists to spare you. Only take this path
when a new shape is truly required.

---

## Reading and updating values

```python
slider.value                 # current value (read, thread-safe)

slider.update(42)            # push a value to the browser
label.update("ready")
toggle.update("on")
liveplot.push({"temp": 21.5})

@slider.on_change            # browser -> Python (Slider/Toggle)
def _(v): ...

@custom.on_message           # browser -> Python (Custom)
def _(data): ...
```

`update()` is one-directional Python→browser; `on_change`/`on_message` is the
reverse. State persists in Python, so a browser that reconnects replays the
current values automatically.

---

## Layout, locking, resizing

Every component exposes live geometry. Set at insert time or change any time:

```python
canvas.insert(comp, x=80, y=80, w=300, h=160, rotation=15)

comp.x = 200            # move (live)
comp.move(200, 120)
comp.w += 50            # resize (live)
comp.resize(w=400, h=200)
comp.rotation = 30      # degrees, clockwise
```

`x/y/w/h/rotation` are readable and assignable; reads reflect what Python last
set.

### Three independent lock modes

| Goal | API | User can move? | resize? | **interact?** |
|---|---|---|---|---|
| Stop dragging only | `movable=False` / `comp.movable = False` | ❌ | ✅ | ✅ |
| Stop resizing only | `resizable=False` / `comp.resizable = False` | ✅ | ❌ | ✅ |
| Make controls inert, stay placeable | `interactive=False` / `comp.interactive = False` | ✅ | ✅ | ❌ |
| Pin in place, stay usable | `comp.pin()` (`unpin()`) | ❌ | ❌ | ✅ |
| Fully lock (static + inert) | `locked=True` / `comp.lock()` (`unlock()`) | ❌ | ❌ | ❌ |

Key distinction:
- **`movable` / `resizable`** gate only *user gestures*; the panel's sliders and
  buttons keep working. Use `pin()` for an interactive-but-fixed panel.
- **`interactive=False`** is the inverse: the user can't operate the controls (a
  transparent overlay swallows pointer events), but the panel stays *unlocked*,
  so it can still be moved/selected **and** your `update()` calls keep rendering.
  Use it for a control that tracks an automatic value the user mustn't drag — a
  slider whose thumb follows a live reading, say.
- **`locked`** is the hard lock — it also blocks interaction (a locked slider
  won't emit changes) *and* freezes programmatic `update()`s.
- **Python `move()` / `resize()` always work**, regardless of these — they gate
  the user, not you.

```python
canvas.insert(gauge, x=40, y=40, movable=False, resizable=False)  # pinned, live
panel.lock()        # freeze completely
panel.unlock()
```

---

## Arrows

Arrows are first-class, managed like components. They bind to the two panels and
reroute automatically as those panels move or resize.

```python
a = canvas.connect(src, dst, name="flow", text="x1", color="blue")

canvas.flow              # lookup by name (like components)
a.color = "red"          # live property change
a.update(dash="dashed", size="l", bend=40)
a.text = "boosted"       # change the visible caption (identity unchanged)

canvas.disconnect("flow")    # remove by name (or pass the Arrow)
```

`name` is the arrow's **identity**: the `canvas.<name>` lookup key — same
convention as components — and is unique, so connecting again under the same
`name` destroys the old arrow and the new one becomes the reference. Omit it and
the name is derived from the endpoints (`"<start.name>-><end.name>"`), so a
second unnamed arrow between the same two panels replaces the first. `text` is
the **caption** drawn on the
arrow (no caption is shown when omitted); change it freely via `a.text = ...` /
`a.update(text=...)` without disturbing identity.

**Arrow properties** (`connect(..., **props)` or `arrow.update(...)`):

| Prop | Values |
|---|---|
| `color` | black, grey, violet, light-violet, blue, light-blue, yellow, orange, green, light-green, light-red, red, white |
| `dash` | draw, solid, dashed, dotted |
| `size` | s, m, l, xl |
| `arrowhead_start`, `arrowhead_end` | none, arrow, triangle, square, dot, pipe, diamond, inverted, bar |
| `bend` | number |
| `text` | caption string (the visible label on the arrow) |

Invalid enum values make tldraw reject the shape (it won't render) — stick to
the lists above.

---

## Reading the UI back, saving & loading

By default, geometry flows Python → browser. With **read-back**, the reverse
also works: when a user drags, resizes, or rotates a panel, Python's
`comp.x / y / w / h / rotation` update to match, and an optional callback fires.

```python
@panel.on_layout
def _(comp):
    print("user moved it to", comp.x, comp.y)
```

(Your own programmatic `move()`/`resize()` don't trigger this — only user
gestures do.)

### Saving and loading

One pair of methods persists the whole board to a single JSON file:

```python
canvas.save("board.json")     # panel formation + the user's freehand drawings
canvas.load("board.json")     # snaps panels back into place, restores drawings
```

The file holds two things:

- **`layout`** — every panel's geometry and lock state (accurate thanks to
  read-back). Panels are *code*, so only their placement is saved, never their
  behaviour. On load they're matched by id (same run), then by name (across
  runs).
- **`drawings`** — the free-form shapes, text and arrows the user drew in the
  UI, which have no Python counterpart. These come from a connected browser
  (the source of truth), so an open page is needed to capture them; with no
  browser open, `save()` writes the formation alone.

Because panels aren't saved as data, **recreate them in code first, then call
`load()`** — it repositions those live panels and merges the saved drawings on
top of them (bound arrows follow their panels automatically):

```python
canvas = pycanvas.Canvas()
speed = canvas.insert(pycanvas.Slider("speed"), ...)   # same names as when saved
# ... insert the rest of your panels ...
canvas.load("board.json")     # formation + drawings, in one call
canvas.serve()
```

Pass `load(..., formation=False)` to restore only the user's drawings and leave
your panels wherever your code placed them (the saved formation is ignored).

---

## Serving & hosting

### Blocking (scripts)

```python
canvas.serve(port=8000)                 # opens browser, blocks until Ctrl+C
```

### Background (Jupyter / interactive)

```python
canvas.serve(port=8000, block=False)    # returns immediately
canvas.slider("late")                   # appears live on the open page
canvas.stop()                           # shut it down
```

After `serve(block=False)`, every later `insert` / `connect` / `update` is pushed
to the already-open page. This is the notebook workflow: serve once, then keep
adding and driving panels from new cells.

### LAN / sharing

`host` is the **bind address** — which interfaces the server listens on:

```python
canvas.serve(host="0.0.0.0")            # reachable from other devices on the LAN
```

Default `127.0.0.1` is local-only. Use `"0.0.0.0"` (or `""`, same thing) to let
other devices on your Wi‑Fi connect. When you bind non-locally, `serve()` prints
the exact address to open elsewhere:

```
PyCanvas serving  (Ctrl+C to stop):
  local:   http://127.0.0.1:8000
  network: http://192.168.1.42:8000   <- open this on another device on the same Wi-Fi
```

Open that **network** URL on the other device (the phone/laptop uses *this*
machine's IP, not its own). Two gotchas if it won't connect:

- **Firewall** — your OS may block inbound connections to the port. On Windows,
  allow it once: `New-NetFirewallRule -DisplayName "PyCanvas 8000" -Direction
  Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Any` (admin shell).
- **Different network / no IP wanted** — to share without dealing with IPs or
  firewalls, or across networks, keep the default local bind and run a tunnel:
  in VS Code open the **Ports** panel → **Forward a Port** → `8000` for a public
  `https://…` URL; or `ngrok http 8000` / `cloudflared tunnel --url
  http://localhost:8000`.

If a `Repl` is on the canvas, non-local serving is refused unless
`serve(..., allow_remote_exec=True)` — a REPL is unauthenticated remote code
execution, so only enable that on a trusted network.

### Reconnection

The browser auto-reconnects if the server restarts, and the server replays full
state (every component's current values, geometry, locks, and all arrows) to any
fresh connection — so reloads and restarts are seamless.

---

## Patterns

**Background worker driving panels:**

```python
import threading, time

def loop():
    while True:
        plot.push({"temp": read_sensor()})
        time.sleep(0.1)

threading.Thread(target=loop, daemon=True).start()
canvas.serve()
```

**A fixed dashboard layout** — pin panels so users can interact but not rearrange.
Define your own dict mapping each component to the position you want, then insert
them all in a loop (this is just your data — the canvas isn't iterated):

```python
# You build this dict: component -> (x, y) position on the canvas.
layout = {
    pycanvas.Slider("speed"): (80, 80),
    pycanvas.Toggle("mode", options=["a", "b"]): (80, 220),
    pycanvas.Label("status"): (380, 80),
}

for comp, (x, y) in layout.items():
    # movable/resizable False => placed exactly here, but still interactive.
    canvas.insert(comp, x=x, y=y, movable=False, resizable=False)
```

If instead you want to act on panels you've *already* inserted, keep your own
references (or use named lookup) — e.g. `canvas.speed.lock()`. There is no
public "iterate every component on the canvas" API; you track the panels you
care about yourself.

See [`examples/`](examples/) for full programs (robot control, sensor dashboard,
locking + arrows, notebook workflow).
