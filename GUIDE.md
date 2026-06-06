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
| **Label** | `Label(name, value="", label=None)` | out | `update(value)` | — |
| **VideoFeed** | `VideoFeed(name, quality=70, label=None)` | out | `update(frame)` — OpenCV BGR numpy array | — |
| **Plot** | `Plot(name="plot", label=None, width=560, height=420)` | out | `update(fig)` — a Plotly figure or HTML string | — |
| **LivePlot** | `LivePlot(name="live plot", traces=None, max_points=300, mode="lines", layout=None, ..., label=None)` | out | `push({"trace": y, ...})`, `clear()` | — |
| **Custom** | `Custom(html=None, path=None, name="custom", label=None, width=380, height=320)` | both | `update(html)` (reload) / `push(data)` (stream, no reload) | `.value` (last message) |

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
  as base64.

### Custom panels (arbitrary HTML)

`Custom` renders any HTML in a sandboxed iframe. A `canvas.send(data)` helper is
injected so the panel can push data back to Python:

```python
panel = canvas.insert(pycanvas.Custom(html="""
  <button onclick="canvas.send({clicked: true})">Go</button>
"""))

@panel.on_message          # Custom uses on_message, not on_change
def handle(data):
    print(data)            # {'clicked': True}
```

Load from a file with `Custom(path="dashboard.html")`. Replace content live with
`panel.update(new_html)` (this reloads the iframe).

To stream live data **without** reloading — keeping the iframe's focus,
listeners and scroll intact — use `panel.push(data)`. It arrives as a `message`
event inside the iframe (`e.data.__pycanvas` holds your `data`), so the page can
update in place. This suits high-rate feeds and two-way interactive panels:

```python
panel.push(frame_b64)      # Python -> iframe, no reload
```
```js
window.addEventListener('message', (e) => {
  if (e.data && e.data.__pycanvas !== undefined) render(e.data.__pycanvas)
})
```

`examples/remote_control.py` uses exactly this to stream the machine's screen
into a panel while capturing the browser's mouse/keyboard to drive the host (a
small LAN remote desktop — note its security warning).

### Writing your own

Subclass `BaseComponent`, set `component` to a frontend shape type, and
implement `update()`. (Most needs are met by `Custom` — only subclass when you
add a new tldraw shape on the frontend.)

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
| Pin in place, stay usable | `comp.pin()` (`unpin()`) | ❌ | ❌ | ✅ |
| Fully lock (static + inert) | `locked=True` / `comp.lock()` (`unlock()`) | ❌ | ❌ | ❌ |

Key distinction:
- **`movable` / `resizable`** gate only *user gestures*; the panel's sliders and
  buttons keep working. Use `pin()` for an interactive-but-fixed panel.
- **`locked`** is the hard lock — it also blocks interaction (a locked slider
  won't emit changes).
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
