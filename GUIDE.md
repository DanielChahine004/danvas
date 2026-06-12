# PyCanvas — A Guide for Newcomers

PyCanvas turns Python into the brain behind a live, infinite, browser-based
canvas (built on [tldraw](https://tldraw.dev)). You create **panels** in Python,
drop them onto the canvas, and read or drive their values live over a WebSocket.
**You never write any frontend code** — Python is the only language you touch.

```python
import pycanvas

canvas = pycanvas.Canvas()                                  # 1. make a canvas
speed  = canvas.slider("speed", min=0, max=100)             # 2. add panels
out    = canvas.label("readout")

@speed.on_change                                            # 3. wire them up
def _(v):
    out.update(f"speed = {v}")

canvas.serve()                                              # 4. serve (opens the browser)
```

Run that file and a browser opens with a slider and a label on an infinite
canvas. Drag the slider; the label follows. That's the whole loop, and the rest
of this guide unpacks what else each line can do.

---

## 1. The mental model

PyCanvas has **four nouns** and **two directions of data**. Hold these in your
head and everything else falls into place.

### The four nouns

| Noun | What it is | You get it from |
|---|---|---|
| **Canvas** | The one object you own. It holds everything and runs the web server. | `pycanvas.Canvas()` |
| **Component** | A panel on the canvas — a slider, a plot, an image, a chat box. | `canvas.slider(...)`, `canvas.plot(...)`, … |
| **Arrow** | A connector drawn between two panels. Reroutes itself as they move. | `canvas.connect(a, b)` |
| **Viewer** | A connected browser. Several people can watch/edit the same canvas at once. | `canvas.viewers` |

### The two directions of data

Everything that happens is one of these two flows:

```
   Python  ──update()──►  browser        "I changed a value, show it"
   Python  ◄─on_change──  browser        "the user did something, react to it"
```

- **Python → browser:** you call `component.update(...)` (or `push`, `move`,
  `resize`, `lock`, …). The change appears instantly in every open browser.
- **Browser → Python:** the user drags a slider, clicks a button, picks a file —
  this fires a callback you registered with `@component.on_change` (or
  `on_click`, `on_message`, …).

### Three facts that make it feel magic

1. **State lives in Python.** The browser is a thin view. If a browser reloads or
   a new viewer connects, PyCanvas replays the full current state (every value,
   position, lock, and arrow) to them. Reconnection is seamless.
2. **Everything is thread-safe.** You can call `update()` from any thread while
   the server runs in another. Background worker loops are a first-class pattern.
3. **Names are identities.** Every component and arrow has a unique `name`. It's
   how you look it up (`canvas.speed`), and re-using a name *replaces* the old
   panel instead of stacking a duplicate (great for re-running a cell).

---

## 2. The lifecycle: five steps to building a canvas

Every PyCanvas program is the same five steps. The rest of this guide is one
section per step.

```
  ┌─────────────────────────────────────────────────────────────────┐
  │  1. Create        canvas = pycanvas.Canvas()                     │
  │  2. Add panels    canvas.slider(...) / canvas.plot(...) / ...    │
  │  3. Wire them up   @comp.on_change ... comp.update(...)          │
  │  4. Arrange        position, size, lock, frame, arrows           │
  │  5. Serve & share  canvas.serve(...)                             │
  └─────────────────────────────────────────────────────────────────┘
```

Steps 2–4 can also happen *after* step 5, live, if you serve in the background
(`serve(block=False)`) — that's the notebook workflow (see §9).

---

## 3. Step 1 — Create the canvas

```python
canvas = pycanvas.Canvas()
```

That's it. One `Canvas` per app. It starts empty. Everything else is a method on
this object. Here's the map of what `canvas` can do, so you know what's coming:

| Category | Methods |
|---|---|
| **Add panels** | `slider`, `toggle`, `button`, `label`, `video`, `audio`, `plot`, `live_plot`, `image`, `table`, `markdown`, `chat`, `custom`, `react`, `webview`, `file_browser`, `repl`, `inspector`, `show`, and the generic `insert` |
| **Connect** | `connect`, `disconnect` |
| **Remove** | `remove` |
| **Look up** | `canvas.<name>`, `canvas["<name>"]`, `canvas.components`, `canvas.arrows`, `canvas.viewers` |
| **Serve** | `serve`, `stop`, `wait` |
| **Background work** | `background` |
| **View control** | `set_view` |
| **Persist** | `save`, `load` |
| **Notebook** | `capture_cells`, `stop_capturing_cells`, `enable_repl` |
| **Package** | `bake` |

---

## 4. Step 2 — Add panels (components)

### Two ways to add a panel

Both do the same thing — build a component and place it on the canvas — pick
whichever reads better:

```python
# A) Factory method (shortest; recommended)
speed = canvas.slider("speed", min=0, max=100, x=80, y=80)

# B) Build, then insert (useful for hand-built or reused components)
s = pycanvas.Slider("speed", min=0, max=100)
speed = canvas.insert(s, x=80, y=80)
```

Either way you get the component object back, so you can drive it later.

### Naming: identity vs. caption

Two different things are easy to confuse:

- **`name`** is the **identity** — unique, required (the input panels make it the
  first argument; utility panels default it to their type word). It becomes the
  `canvas.<name>` handle and the **eviction key**: insert again under the same
  name and the old panel is replaced.
- **`label`** is the **caption** shown in the panel's header. Purely cosmetic,
  optional; defaults to the `name`.

```python
canvas.slider("speed", label="Engine speed")  # name="speed", header reads "Engine speed"
canvas.speed                                   # look it up by name
canvas["speed"]                                # same (use this if name isn't a valid identifier)
```

### The component catalog

Components are grouped by what they're *for*. "Direction" tells you the data
flow: **in** (user → Python), **out** (Python → user), or **both**.

#### Inputs — the user drives Python

| Component | Factory | Read | React with |
|---|---|---|---|
| **Slider** | `canvas.slider(name, min=0, max=100, default=None, step=1)` | `.value` (number) | `@on_change` |
| **Toggle** | `canvas.toggle(name, options, default=None)` | `.value` (chosen string) | `@on_change` |
| **Button** | `canvas.button(name, text=None)` | `.value` (click count) | `@on_click` |

#### Outputs — Python drives the display

| Component | Factory | Drive it with |
|---|---|---|
| **Label** | `canvas.label(name, value="")` | `update(text)` |
| **Markdown** | `canvas.markdown(text="")` | `update(text)` |
| **Image** | `canvas.image(src, fit="contain")` | `update(src)` — path / URL / bytes / Matplotlib / PIL / NumPy array |
| **Table** | `canvas.table(data)` | `update(data)` — DataFrame / Series / list-of-dicts / dict-of-columns |
| **Plot** | `canvas.plot(name="plot")` | `update(fig)` — a Plotly figure or HTML string |

#### Streaming — high-rate feeds

| Component | Factory | Drive it with |
|---|---|---|
| **LivePlot** | `canvas.live_plot(traces=None, max_points=300, mode="lines")` | `push({"trace": y})` every loop; `clear()` |
| **VideoFeed** | `canvas.video(name, quality=70)` | `update(frame)` — OpenCV BGR NumPy array |
| **AudioFeed** | `canvas.audio(name, sample_rate=16000, channels=1)` | `update(pcm)` — int16 PCM samples |

> **Plot vs LivePlot.** `Plot` re-renders a whole Plotly figure each update —
> simple, good for occasional charts. `LivePlot` keeps a mounted chart and
> streams only new data points — smooth at high rates. Use `push()` in a loop.

> **Video/audio** are sent as raw binary WebSocket frames (no base64/JSON
> overhead). `VideoFeed` JPEG-encodes via OpenCV (the optional `[video]` extra);
> pass `encode=False` to send JPEG bytes you already have, needing no extra.

#### Content — embed arbitrary HTML, React, or web pages

| Component | Factory | Notes |
|---|---|---|
| **Custom** | `canvas.custom(html=…, css=…, js=…)` | Any HTML in a **sandboxed iframe**. Two-way. See below. |
| **React** | `canvas.react(source=…, props=…)` | Your own React component, compiled in the browser, native to the canvas. |
| **WebView** | `canvas.webview(url)` | Embed any live web page. |
| **FileBrowser** | `canvas.file_browser(root=".", pattern=None)` | Pick a file from disk (sandboxed to `root`). |

#### Interaction between viewers

| Component | Factory | Notes |
|---|---|---|
| **Chat** | `canvas.chat(name="chat")` | A shared message room across all viewers. `post(text)`, `@on_message`. |

#### Code & introspection (local/trusted use)

| Component | Factory | Notes |
|---|---|---|
| **Repl** | `canvas.repl()` | A code cell that runs Python against a shared namespace. Call `canvas.enable_repl()` first. |
| **Inspector** | `canvas.inspector(source="components")` | A live "variable explorer" of your panels or the REPL namespace. |

> **Security:** a `Repl` is unauthenticated remote code execution. PyCanvas
> **refuses** to serve a canvas containing one on a non-local address (LAN /
> tunnel) unless you pass `serve(..., allow_remote_exec=True)`. Only do that on a
> network you trust.

### "Just show me this value" — `canvas.show(...)`

When you don't want to pick a component, `show` inspects the value and inserts
the panel that best renders it — the same call a notebook makes for an `Out[…]`,
but **without IPython**, so it works in plain scripts too:

```python
canvas.show(df)                 # DataFrame  -> Table
canvas.show(fig)                # figure     -> Plot / Image
canvas.show("# Notes\n- a")     # string     -> Markdown
canvas.show({"ok": True})       # dict/list  -> pretty JSON
canvas.show(obj)                # anything with _repr_html_/_repr_png_ -> its rich view
```

No `name` gives each call a fresh panel; pass `name=` to **replace** one in place
(handy in a loop). The same dispatcher is `pycanvas.panel_for(value)` if you want
the component *without* inserting it.

### Custom panels (the escape hatch you'll reach for most)

`Custom` renders any HTML in a sandboxed iframe and gives you a symmetric
`canvas` helper inside it: `canvas.send(data)` posts back to Python,
`canvas.onPush(fn)` receives data Python streams in. Route inbound messages by an
`event` field — no subclass, no dispatcher:

```python
panel = canvas.custom(html="""
  <button onclick="canvas.send({event: 'go'})">Go</button>
""")

@panel.on("go")               # fires only for {event: 'go'}
def handle(msg):
    print(msg)
```

- `panel.update(new_html)` replaces the content (reloads the iframe).
- `panel.push(data)` streams data in **without** reloading — keeps focus, scroll,
  and listeners intact. Receive it with `canvas.onPush(fn)` in the iframe. Use
  this for high-rate feeds.
- Load from a file with `canvas.custom(path="dashboard.html")`.

### React panels (a native component, no `npm` build)

`React` is the native counterpart to `Custom`. Instead of a sandboxed iframe, it
mounts a real React subtree *inside* the panel — inheriting the canvas theme,
dark mode, and selection chrome, and talking to Python directly. You write JSX
**from Python** and it's compiled in the browser at runtime, so there's no build
step:

```python
counter = canvas.react("""
  function Component({ canvas, value, props }) {
    const [n, setN] = React.useState(0)
    return <button onClick={() => { setN(n+1); canvas.send({event:'tap', n:n+1}) }}>
      {props.label}: {n}
    </button>
  }
""", props={"label": "Taps"})

@counter.on("tap")
def _(msg): print(msg["n"])

counter.update(label="Hits")   # patch props -> live re-render
counter.push(live_value)       # stream into the `value` prop, no re-mount
```

**Custom vs React:** reach for **Custom** for plain HTML or when you want the hard
isolation of a sandbox (third-party snippets). Reach for **React** when you want
hooks, the canvas theme, and native selection. A React panel's source runs in the
main page (your trust level) — don't feed it untrusted source; use `Custom` for
that.

### FileBrowser — pick a file, drive a pipeline

The input end of a "choose a file → run something → show the result" loop. The
directory listing happens in Python (the browser can't read disk):

```python
files = canvas.file_browser("files", root="./data", pattern="*.csv")
plot  = canvas.plot("result")

@files.on_select                       # fires with the chosen file's absolute path
def run(path):
    plot.update(build_figure(path))

@files.on_navigate                     # optional: fires with the new dir on each cd
def _(cwd): ...
```

`root` is a **hard sandbox**: every requested path is resolved with `realpath`
and rejected if it escapes `root` (symlinks included) — so a remote viewer can't
walk the rest of your disk. `pattern` is an fnmatch glob filtering *files*;
`show_hidden=False` hides dotfiles. Read `.cwd` and `.value` (last file); drive
it with `go(path)` and `refresh()`.

### Writing your own component

**Prefer `Custom` or `React` first** — between them you get a two-way channel,
live updates, and event routing, all from user code with no package edit and no
`npm` build. Only subclass `BaseComponent` when you need a genuinely new **tldraw
shape** on the frontend, which means editing and rebuilding the React frontend
(`npm run build` in `pycanvas/frontend`). A subclass wires into the bridge
through a few hooks — `register_props()`, `state_payload()`, `update()`, and
`_handle_input()` — and inherits all the geometry/locking machinery. See
[`pycanvas/components/slider.py`](pycanvas/components/slider.py) (minimal) or
[`video.py`](pycanvas/components/video.py) (binary frames) as templates.

---

## 5. Step 3 — Wire them up (interactivity)

This is the two-directions-of-data model in practice.

### Python → browser: `update()` / `push()`

```python
slider.update(42)              # move the slider from Python
label.update("ready")          # set the label text
toggle.update("on")            # set the toggle
liveplot.push({"temp": 21.5})  # stream a data point
```

`update()` carries state — a reconnecting browser replays the latest value
automatically. `push()` is for high-rate streams that you don't need replayed.

### Browser → Python: callbacks

Register a callback with a decorator. The method name tells you the event:

```python
@slider.on_change             # Slider/Toggle moved by the user
def _(v): ...

@button.on_click              # Button pressed
def _(): ...

@custom.on("go")              # Custom/React: a specific {event: 'go'} message
def _(msg): ...

@custom.on_message            # Custom/React: catch-all for any message
def _(data): ...

@files.on_select              # FileBrowser: a file was chosen
def _(path): ...

@chat.on_message              # Chat: every line anyone sends
def _(entry): ...
```

Reads are always available and thread-safe via `.value`:

```python
speed.value      # current slider value
toggle.value     # chosen option
button.value     # click count
```

### Background workers — the producer-loop pattern

Most live canvases have a loop somewhere feeding a panel (a camera, a sensor, a
telemetry stream). Register it with `@canvas.background` and `serve()` starts it
on a daemon thread *in the serving process* — which is what makes it play nicely
with hot reload and single-owner resources like a webcam:

```python
feed = canvas.video("webcam")

@canvas.background
def stream():
    cap = cv2.VideoCapture(0)
    while True:
        ok, frame = cap.read()
        if ok:
            feed.update(frame)

canvas.serve(hot_reload=True)
```

> You *can* start a raw `threading.Thread(...).start()` yourself, but prefer
> `@canvas.background`: it defers the loop to the serving process, so it won't
> grab a camera in the hot-reload monitor and starve the real worker.

### Backpressure: the queue policy

Each component chooses what happens when its updates outpace a slow viewer:

- `queue="fifo"` (default) — deliver every update in order, nothing dropped.
  Right for controls and labels where each value matters.
- `queue="latest"` — keep only the newest pending value per viewer, dropping
  stale ones. Right for live media/telemetry (VideoFeed defaults to this).

Set it any time: `plot.queue = "latest"`.

---

## 6. Step 4 — Arrange & constrain

### Position, size, rotation

Set geometry at insert time, or change it live any time — every write is pushed
to the browser immediately:

```python
canvas.insert(comp, x=80, y=80, w=300, h=160, rotation=15)

comp.x = 200             # move (live)
comp.move(200, 120)
comp.w += 50             # resize (live)
comp.resize(w=400, h=200)
comp.rotation = 30       # degrees, clockwise
```

`x / y / w / h / rotation` are all readable and assignable. Omit `x`/`y` at
insert and the frontend auto-cascades the panel into view.

### Locking — five independent controls

This is the part newcomers most often want a clear table for. Each lock gates a
*different* thing, and they compose. **Python's own `move()`/`resize()`/`update()`
always work regardless** — these gate the *user*, not you.

| You want to… | Use | User can move? | resize? | operate controls? | Python `update()` renders? |
|---|---|:---:|:---:|:---:|:---:|
| Stop dragging only | `draggable=False` | ❌ | ✅ | ✅ | ✅ |
| Stop resizing only | `resizable=False` | ✅ | ❌ | ✅ | ✅ |
| Pin in place, stay usable | `comp.pin()` | ❌ | ❌ | ✅ | ✅ |
| Freeze controls, keep placeable | `operable=False` | ✅ | ✅ | ❌ | ✅ |
| Fully lock (static + inert) | `locked=True` / `comp.lock()` | ❌ | ❌ | ❌ | ❌ |

The three to really understand:

- **`draggable` / `resizable`** gate only *user gestures*. The panel's sliders and
  buttons keep working. `comp.pin()` is the shorthand for both off.
- **`operable=False`** is the inverse: the user can't touch the controls (a
  transparent overlay swallows their clicks), but the panel stays unlocked, so it
  can still be moved **and your `update()` calls keep rendering**. Perfect for a
  control that displays an automatic value the user mustn't drag.
- **`locked=True`** is the hard lock — it freezes everything, *including* your
  programmatic updates.

Every lock is also a settable property: `comp.draggable = False`,
`comp.operable = False`, `comp.lock()` / `comp.unlock()`, `comp.pin()` /
`comp.unpin()`.

### Frameless & non-grabbable panels

Two more knobs for making content feel like it floats on the canvas:

- **`frame=False`** strips the panel's card chrome — background, border, shadow,
  padding, the label header, *and* the hover/selection highlight — so the content
  appears to sit directly on the canvas. The panel still occupies its `w×h` box
  and can be selected/moved.
- **`grabbable=False`** (content panels) drops the click-to-select cover so the
  content is interactive on the first hover, *and* makes the panel invisible to
  selection — no click, marquee, or select-all touches it. Move it from Python
  only.

Pair them for a free-floating live widget:

```python
canvas.custom(name="gauge", html=…, frame=False, grabbable=False)
```

### Arrows — connect panels

Arrows are first-class, managed like components, and reroute automatically as
their endpoints move:

```python
a = canvas.connect(src, dst, name="flow", text="x1", color="blue")

canvas.flow                  # look up by name, like a component
a.color = "red"              # live property change
a.update(dash="dashed", size="l", bend=40)
a.text = "boosted"           # change the caption (identity unchanged)

canvas.disconnect("flow")    # remove by name (or pass the Arrow)
```

Like components, `name` is the arrow's identity (re-connecting under the same
name replaces it; omit it and it's derived from the endpoints), and `text` is the
cosmetic caption.

| Property | Values |
|---|---|
| `color` | black, grey, violet, light-violet, blue, light-blue, yellow, orange, green, light-green, light-red, red, white |
| `dash` | draw, solid, dashed, dotted |
| `size` | s, m, l, xl |
| `arrowhead_start`, `arrowhead_end` | none, arrow, triangle, square, dot, pipe, diamond, inverted, bar |
| `bend` | a number |

> Invalid enum values make tldraw silently reject the shape — stick to the lists.

### Reading the user's layout back

By default geometry flows Python → browser. **Read-back** adds the reverse: when
a user drags or resizes a panel, Python's `comp.x/y/w/h/rotation` update to
match, and an optional callback fires:

```python
@panel.on_layout
def _(comp):
    print("user moved it to", comp.x, comp.y)
```

(Your own `move()`/`resize()` don't trigger this — only user gestures do.)

---

## 7. Step 5 — Serve & share

`canvas.serve()` is the last line of most scripts. Its options unlock everything
from "open it on my screen" to "share it on the public internet."

### Blocking (the default — for scripts)

```python
canvas.serve(port=8000)        # opens the browser, blocks until Ctrl+C
```

### Background (for Jupyter / interactive)

```python
canvas.serve(port=8000, block=False)   # returns immediately
canvas.slider("late")                  # appears live on the already-open page
canvas.stop()                          # shut it down
```

After `serve(block=False)`, every later `insert` / `connect` / `update` is pushed
straight to the open page. In a **plain script** (not a notebook) you must keep
the process alive — call `canvas.wait()` at the end, or the daemon server thread
dies when the script exits.

### Share on your LAN

`host` is the bind address — which network interfaces to listen on:

```python
canvas.serve(host="0.0.0.0")   # reachable from other devices on the same Wi-Fi
```

The default `127.0.0.1` is local-only. With `0.0.0.0`, `serve()` prints the exact
`network:` URL to open on another device (it uses *this* machine's IP). Two
gotchas: your OS firewall may block the port (allow it once), and a `Repl` on the
canvas refuses a non-local bind unless `allow_remote_exec=True`.

### Share across the internet (tunnel)

```python
canvas.serve(tunnel=True)      # prints a public https://… URL anyone can open
```

`tunnel_provider="cloudflared"` (default, no signup) or `"localtunnel"`. A tunnel
exposes the canvas to the whole internet, so it's gated for `Repl` exactly like a
public bind.

### Password-protect it

```python
canvas.serve(host="0.0.0.0", password="hunter2")
```

Visitors see a password page first; the WebSocket is refused until they pass. The
check is per-browser-session (a cookie), so each viewer enters it once. A password
controls *who connects*, not whether a Repl may run — that still needs the
explicit `allow_remote_exec`.

### Control the viewport (`view=` and `set_view`)

Make the same canvas a free creative workspace or a fixed kiosk UI. Pass a `view`
dict to `serve` (initial state) or call `canvas.set_view(...)` live:

```python
canvas.serve(view={"zoom": 1.5, "ui": False, "locked": True})

canvas.set_view(ui=False)                      # hide tldraw's toolbars everywhere
canvas.set_view(zoom=2.0)                      # zoom all viewers to 200%
canvas.set_view(locked=True)                   # freeze pan/zoom (kiosk)
canvas.set_view(x=100, y=200, client_id="…")   # move ONE viewer's camera only
```

| `view` key | Effect |
|---|---|
| `x` / `y` / `zoom` | Initial camera position and zoom (1.0 = 100%) |
| `locked` | `True` freezes pan and zoom entirely |
| `min_zoom` / `max_zoom` | Clamp how far the viewer can zoom |
| `ui` | `False` hides tldraw's toolbars/menus |
| `grid` | `True` shows the background grid |
| `read_only` | `True` puts tldraw in read-only mode (no drawing) |

`set_view` with a `client_id` (from `canvas.viewers`) steers just that one
viewer; omit it to broadcast to everyone.

### Multiple viewers

`canvas.viewers` is the live roster of connected browsers — each a dict with
`id`, `name`, and `color`. The `id` is what `set_view(..., client_id=…)` expects.
The list reflects who's connected *right now*. Pair it with `Chat` for a shared
room.

### Hot reload (auto-restart on save)

```python
canvas.serve(hot_reload=True)
```

Watches the `.py` files next to your script and restarts the whole process on any
save, so edits — a different `default=`, a moved panel, `ui=False` — take effect
immediately. The browser tab reconnects on its own (no new tab). If an edit has a
syntax error, the last working version keeps serving until you fix it. Script-only
(`block=True`); not available in notebooks.

### Desktop app & packaging (`bake`)

Run in a native window instead of the browser, and ship a standalone `.exe`:

```python
canvas.serve(desktop=True)         # native window (pywebview) instead of browser
canvas.bake(name="RobotConsole")   # python script.py -> builds a one-file .exe
```

`bake` bundles Python, the backend, and the frontend with PyInstaller. The built
app needs nothing installed: launching it runs your script in a native window.
The *same script* is both source and app — inside the `.exe` (`sys.frozen`),
`bake` skips the build and just runs. Needs the `[desktop]` extra. CLI equivalent:
`python -m pycanvas.bake your_script.py`.

### Reconnection

The browser auto-reconnects if the server restarts, and the server replays full
state (every value, geometry, lock, and arrow) to any fresh connection — so
reloads and restarts are seamless.

---

## 8. Merging canvases — many hosts, one shared surface

This is PyCanvas's standout collaboration feature, and it works differently from
everything above. Normally one `Canvas` is one Python process feeding one set of
browsers. **Merge** lets several *independent* canvases — each hosted by a
different person, in a different process, on a different machine — appear as a
single unified board that everyone can watch and interact with.

The key idea: **nobody gives up their own canvas.** Each person keeps running
their own `Canvas` on their own port, exactly as before. A separate, lightweight
**aggregator** connects to each of them as a client (just like a browser does),
composites all their panels onto one surface, and re-serves the union on a new
port. It runs *no* component logic and holds *no* state of its own — it caches
each source's panels and **routes interactions back to the owning process**. So a
click on Sarah's button still computes in Sarah's Python; Josef's runs in his.

```
   Sarah's Canvas  (:8001) ─┐
   Josef's Canvas  (:8002) ─┼──►  Merge host (:8080)  ──►  everyone's browser
   Maria's Canvas  (:8003) ─┘        (composites + routes)
```

### Start a merge

From the command line — point it at the running canvases and pick a port:

```bash
# unify three running canvases onto http://localhost:8080
python -m pycanvas.merge :8001 :8002 host3:8003 --port 8080
```

Or from Python (e.g. in a notebook):

```python
from pycanvas import Merge

Merge([8001, 8002]).serve(port=8080)            # blocks, like Canvas.serve()
Merge([8001, 8002]).serve(port=8080, block=False)  # background; later .stop()
```

### Sources can be anywhere

A source is a bare port (`8001`, `:8001`), a `host:port`, **or a full tunnel
URL** — so you can merge canvases that people are hosting on entirely different
networks, as long as each has run `serve(tunnel=True)`:

```bash
python -m pycanvas.merge https://a.loca.lt https://b.loca.lt --tunnel
```

`--tunnel` (or `serve(tunnel=True)`) also publishes the *merged* view itself, so
collaborators on any network can open one `https://…` URL and see everyone's
work together.

### Overlay vs. regions

| Layout | How | Result |
|---|---|---|
| **Overlay** (default) | `Merge([…])` | Every source keeps its real coordinates — canvases stack in the same space, as if drawn on one sheet. |
| **Side-by-side** | `Merge([…], region_width=2000)` | Each source gets its own horizontal strip that many pixels wide, so they sit next to each other instead of overlapping. |

### Live & resilient

Sources connect, disconnect, and reconnect freely. While a source is down its
panels go inert and drop out of the merged view; they reappear (and resume
computing in their owner's process) when it comes back. Viewers on the merged
view also get **shared presence and chat** — they see each other in the roster
and can talk, mediated by the merge host.

### What merges, and the `Repl` gate

- **Merged:** all code-driven panels and arrows, with full two-way interaction
  routed back to each source.
- **Not merged (v1):** free-form user drawings aren't composited, and rearranging
  panels in the merged view is local to the merge host — it isn't pushed back to
  the source canvases.
- **Security:** the merge host runs no code itself, but a browser interacting with
  a merged `Repl` would execute code *in that source's* process. That's refused
  unless you pass `allow_remote_exec=True` (`--allow-remote-exec` on the CLI) —
  same opt-in as `Canvas.serve`.

See [`examples/merge_canvases.ipynb`](examples/merge_canvases.ipynb).

---

## 9. Saving, loading & the notebook workflow

### Save and load a board

One pair of methods persists the whole board to a single JSON file:

```python
canvas.save("board.json")   # panel formation + the user's freehand drawings
canvas.load("board.json")   # snaps panels back, restores drawings
```

The file holds two things:

- **`layout`** — every panel's geometry and lock state (accurate thanks to
  read-back). Panels are *code*, so only their *placement* is saved, never their
  behaviour.
- **`drawings`** — the free-form shapes, text, and arrows the user drew in the UI,
  which have no Python counterpart. These come from a connected browser, so an
  open page is needed to capture them.

Because panels aren't saved as data, **recreate them in code first, then call
`load()`** — it repositions the live panels and lays the saved drawings on top:

```python
canvas = pycanvas.Canvas()
canvas.slider("speed")          # same names as when you saved
# ... recreate the rest of your panels ...
canvas.load("board.json")
canvas.serve()
```

Pass `load(..., formation=False)` to restore only the drawings and leave your
panels where your code placed them.

### Jupyter / notebooks

Serve once in the background, then keep adding panels from later cells:

```python
canvas = pycanvas.Canvas()
canvas.serve(block=False)       # returns immediately; page stays open
# ... any later cell ...
canvas.show(df)                 # appears live on the open page
```

### Mirror every cell's output automatically

`capture_cells()` registers a hook so each cell ending in an expression gets (or
refreshes) its own auto-arranged panel — no manual `insert` per cell:

```python
canvas = pycanvas.Canvas()
canvas.serve(block=False)
canvas.capture_cells()          # now every cell's output lands on the canvas
```

Re-running a cell swaps its panel in place. A `# pycanvas:` directive line in a
cell overrides placement (or opts out with `skip`); `capture_cells(auto=False)`
inverts it to an allowlist. Stop with `stop_capturing_cells()`. The standalone
function is `pycanvas.autopanel(canvas, ...)`.

---

## 10. Quick reference

**Build & drive:**

```python
canvas = pycanvas.Canvas()
comp   = canvas.slider("x", min=0, max=10, x=40, y=40)   # add + place
comp.update(5)                                           # Python -> browser
comp.value                                               # read (thread-safe)

@comp.on_change                                          # browser -> Python
def _(v): ...
```

**Arrange & lock:**

```python
comp.move(100, 100); comp.resize(w=300); comp.rotation = 15
comp.pin()            # fixed but interactive
comp.operable = False # inert controls, still driven by Python
comp.lock()           # frozen completely
comp.frame = False    # no card chrome
canvas.connect(a, b, color="blue", text="flow")
```

**Serve:**

```python
canvas.serve()                                   # script: blocks
canvas.serve(block=False); ...; canvas.wait()    # background, then park
canvas.serve(host="0.0.0.0", password="…")       # LAN, gated
canvas.serve(tunnel=True)                         # public URL
canvas.serve(hot_reload=True)                     # restart on save
canvas.serve(view={"ui": False, "locked": True}) # kiosk
canvas.bake(name="MyApp")                         # build a desktop .exe
```

See [`examples/`](examples/) for full programs — robot control, sensor dashboard,
webcam feed, locking + arrows, chat room, notebook workflows, tunneling, and
packaging. The [README](README.md) is the exhaustive reference for every option.
