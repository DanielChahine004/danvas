# PyCanvas

A browser-based spatial canvas whose panels are defined and controlled entirely
from Python. Panels are bidirectional — Python pushes data to them and reads
user input back in real time over one WebSocket.

Built on [tldraw](https://tldraw.dev) + React + Vite (frontend) and FastAPI +
WebSockets (backend). The frontend ships pre-built; you never touch Node or npm.
The Python backend is ~560 kB of source with four core dependencies; the browser
page loads under 1 MB gzip (tldraw + React bridge). The Monaco-powered Repl is
code-split and only downloaded when a Repl panel first appears.

## Install

```bash
pip install dans-pycanvas
```

For local development, clone the repo and install in editable mode instead:

```bash
pip install -e .
```

The base install is lightweight. Heavier features are optional extras:

| Extra | Enables |
|---|---|
| `pip install "dans-pycanvas[video]"` | `VideoFeed` JPEG encoding (OpenCV, ~90 MB) |
| `pip install "dans-pycanvas[audio]"` | microphone capture for `AudioFeed` |
| `pip install "dans-pycanvas[tunnel]"` | public sharing (`serve(tunnel=True)`) |
| `pip install "dans-pycanvas[desktop]"` | native window + `bake()` to a standalone app |

`canvas.video(...)` needs `[video]` for default encoding — or stream
already-JPEG bytes with `VideoFeed(encode=False)`, which needs nothing.

## Hello world

```python
import pycanvas

canvas = pycanvas.Canvas()
servo  = canvas.slider("servo_1", min=0, max=180, default=90)
status = canvas.label("status", "idle")

@servo.on_change
def handle(value):
    status.update(f"servo at {value}")

canvas.serve(port=8000)   # opens the browser, blocks
```

![The hello-world canvas in the browser: a SERVO_1 slider whose value live-updates a STATUS label reading "servo at 113"](docs/hello_world.png)

The loop is always: build panels → register callbacks → `serve()`. Python owns
all state; the browser renders it and reports user actions.

Your `@on_change` / `@on_layout` / `@panel.on(...)` handlers run on a single
ordered worker thread, separate from the event loop — so a blocking handler
(`time.sleep`, an HTTP call, a slow compute) never freezes rendering or live
`update()` broadcasts, and handlers run **in order**. That one thread is shared
across panels, though, so a genuinely slow handler delays other panels' handlers
and the echo of other users' actions until it returns. For slow work, mark the
handler [`threaded=True`](#receiving-input) so it runs on its own thread and the
shared worker stays free.

## Mental model

The lifecycle is always the same handful of steps:

```python
import pycanvas

canvas = pycanvas.Canvas()                        # 1. make a canvas
speed  = canvas.slider("speed", min=0, max=100)   # 2. make components (panels)
status = canvas.label("status", "idle")

@speed.on_change                                  #    read user input back …
def _(v, viewer):                                 #    … with who did it (optional 2nd arg)
    status.update(f"{viewer['name']} set speed to {v}")   # … and push state out

speed.set_layout(x=40, y=40, w=320)               # 3. place/size it (optional —
                                                  #    factory x=/y=/w=/h= work too)
canvas.set_view(zoom=1.0, ui=True)                # 4. set the camera/chrome (optional)
canvas.serve(port=8000)                           # 5. serve — opens the browser, blocks
```

**Python owns all state; the browser renders it and reports user actions.** The
loop is always: make panels → register callbacks → `serve()`.

This README follows those five steps — [1. The canvas](#1-the-canvas),
[2. Components](#2-components), [3. Layout](#3-layout),
[4. Views & navigation](#4-views--navigation),
[5. Serving & sharing](#5-serving--sharing) — then
[Beyond the five steps](#beyond-the-five-steps) for the rest.

# 1. The canvas

`pycanvas.Canvas()` is the document everything hangs off. You build panels with
its factories (step 2) and reach or manage them through it:

```python
canvas.remove(panel)
canvas.connect(a, b, text="x2", color="blue")   # arrow that follows both panels
canvas.disconnect(arrow)                         # or by name
canvas.servo_1            # reach any panel by name (canvas["servo_1"] also works)
canvas.components         # list of every panel (canvas.arrows for connectors)
```

`canvas.clear()` removes everything at once; `canvas.save` / `canvas.load`
persist placement (see [Saving & loading](#saving--loading)).

### Shared React components & styles

When you build custom UI with `react(...)` (step 2), repeating the same widget —
a status pill, a stat card, a table — in every panel's source gets old fast.
`canvas.define()` registers a JSX component **once** and makes it available *by
name* in every React panel; `canvas.style()` adds **one** global stylesheet
shared by all of them (vs a panel's own `css=`, which is scoped to that panel):

```python
canvas.define("StatusPill", """
  function StatusPill({ kind, children }) {
    return <span className={"pill " + kind}>{children}</span>
  }
""")
canvas.style(".pill{padding:2px 10px;border-radius:999px} .pill.ok{color:#4ade80}")

# now ANY react() panel can use it, with no re-declaration:
canvas.react("function Component(){ return <StatusPill kind='ok'>In stock</StatusPill> }")
```

`define(name, source=…)` (or `path=` a `.jsx` file) — `name` must be a valid
identifier and match the component the source declares. `style(css)` accumulates
(each call adds rules) and injects into the page `<head>`, so scope selectors
with your own class prefix. Both replay to every browser on connect and apply
live while serving — a `define()` mid-session recompiles the open panels. They're
for native `react()` panels; sandboxed `custom()` iframes are isolated and don't
receive them. See `examples/shared_components.py`.

# 2. Components

`canvas.<factory>(...)` builds a panel **and** places it, returning the handle.

```python
servo = canvas.slider("servo_1", min=0, max=180, default=90)
feed  = canvas.video("camera")
plot  = canvas.live_plot("servos", traces=["s1", "s2"])
```

**Argument convention:** panels you *read from* take `name` first (`slider`,
`toggle`, `button`, `label`, `video`, `audio`, `chat`, `live_plot`, `plot`,
`repl`, `inspector`); panels that *render content* take the content first
(`image(src)`, `table(data)`, `markdown(text)`, `custom(html)`, `react(source)`,
`webview(url)`, `show(value)`) with `name=` optional. `name` is the unique
`canvas.<name>` handle; `label=` sets a different on-screen caption. Every
factory also forwards `insert`'s placement, lock, and `queue` options:

```python
servo = canvas.slider("servo", min=0, max=180, default=90, label="Servo 1", x=80, y=80)
```

The two-step form builds now, places later (or onto another canvas):

```python
s = pycanvas.Slider("servo_1", min=0, max=180, default=90)
canvas.insert(s, x=80, y=80)
```

Or skip choosing a component: `canvas.show(value)` auto-renders any value as the
best panel (see [Show anything](#show-anything)).

## The canvas object

Everything reachable from a `Canvas`, grouped by what it's for:

| Category | Member | What it does |
|---|---|---|
| **Make panels** | `canvas.slider/button/toggle/label/text_field/markdown/image/table/plot/live_plot/histogram/video/audio/chat/webview/custom/react/repl/inspector/upload/download/file_browser(...)` | Build a panel and add it — see the [catalogue](#the-component-catalogue) for each |
| | `canvas.show(value, **place)` | Auto-pick the best panel for any value |
| | `canvas.insert(component, **place)` | Add a hand-built component; returns it |
| | `canvas.remove(component)` / `canvas.clear()` | Remove one panel / all panels + arrows |
| | `canvas.connect(a, b, text=…)` / `canvas.disconnect(arrow)` | Draw / remove an arrow bound between two panels |
| **Shapes** | `canvas.geo/text/note/draw/highlight/line/frame(...)` | Place a managed tldraw shape, Python-owned and live-updatable |
| | `canvas.shapes` | List of managed shapes currently on the canvas |
| | `canvas.drawings` | Snapshot dict of user-drawn ephemeral shapes |
| | `canvas.on_draw(fn)` / `off_draw(fn)` | Stream draw events when users draw, move, or delete shapes |
| | `canvas.remove_shape(shape_or_name)` | Remove a managed shape by object or name |
| **Arrange** | `canvas.column(x, y, w, gap) / row(x, y, h, gap)` | Container — stack panels vertically/horizontally; nest with `.row()` / `.column()` |
| | `canvas.streamlit(gap, padding)` | Full-viewport-width column with vertical-scroll navigation |
| | `with canvas.grid(cols, slot, gap, x, y):` | Grid — panels fill slots left-to-right, wrapping by row |
| | `canvas.reset_layout()` | Restore all panels to their Python-defined positions without moving the camera |
| | `canvas.set_view(zoom=…, locked=…, ui=…, navigation=…, roles=, client_id=)` | Camera, chrome & navigation mode — scriptable and per-viewer |
| **Reach panels** | `canvas[name]` / `canvas.<name>` | Fetch a panel (or arrow) by its name |
| | `canvas.components` / `canvas.arrows` | Lists of what's on the canvas |
| **Shared React** | `canvas.define(name, source/path)` | Register a JSX component usable in every `react()` panel |
| | `canvas.style(css)` | Inject a global stylesheet for native panels |
| **Viewers** | `canvas.viewers` | Live list of connected viewers (`len(...)` = count); each is the [viewer dict](#the-viewer-dict) |
| | `canvas.on_connect(fn)` / `off_connect(fn)` | Run `fn(viewer)` when a viewer joins (e.g. adapt to mobile) |
| | `canvas.on_disconnect(fn)` / `off_disconnect(fn)` | Run `fn(viewer)` when a viewer leaves (cleanup) |
| | `canvas.on_cursor(fn)` / `off_cursor(fn)` | Stream viewer pointer moves (`serve(cursors=True)`) |
| | `canvas.on_frame(fn)` / `off_frame(fn)` | Observe every WebSocket frame (debugging) |
| **Background** | `canvas.background(fn)` | Register a producer loop, started on its own thread at `serve()` (worker-only) |
| **REPL / notebook** | `canvas.enable_repl(namespace)` | Bind the namespace on-canvas `Repl` cells run against |
| | `canvas.capture_cells(...)` / `stop_capturing_cells()` | Mirror notebook cell outputs onto the canvas |
| **Persist** | `canvas.save(path)` / `canvas.load(source)` | Manual snapshot of formation + drawings (auto twin: `serve(persist=…)`) |
| **Serve / run** | `canvas.serve(port=, host=, password=, tunnel=, persist=, hot_reload=, desktop=, view=, …)` | Start the server (blocks unless `block=False`) |
| | `canvas.wait_for_client(timeout=)` | Block until a browser connects |
| | `canvas.stop()` / `canvas.wait()` | Shut down / park the main thread until shutdown |
| | `canvas.bake(name=)` | Build a standalone desktop executable |

Every `on_x` / `off_x` pair is just register / unregister: `on_connect(fn)`
starts calling `fn`, `off_connect(fn)` stops calling that same `fn` (pass back
the function you registered). You normally only need `on_x` — register once at
startup and leave it; `off_x` is there for the rarer case of turning a handler
off partway through.

Panel-level handlers (`@panel.on_change`, `@button.on_click`, `@panel.on(event)`,
`@chat.on_message`) live on the components, not the canvas — see
[Receiving input](#receiving-input); most accept `threaded=True`.

## The component catalogue

| Component | Direction | API |
|---|---|---|
| `Slider` | bidirectional | `.value`, `@on_change`, `.update(v)`; `step=` (fractional → float slider + number entry), `on_release=True` (report only on let-go); live: `.min`, `.max`, `.step`, `.color` |
| `Toggle` | bidirectional | `.value`, `@on_change`, `.update(opt)`; `options=[...]`; live: `.options`, `.color` |
| `Button` | input | `@on_click`, `.value` (click count), `text=`, `.update(text)`; live: `.color` |
| `TextField` | bidirectional | single-line or `multiline=True` textarea; `@on_change` fires on Enter / blur; `.value`, `.update(text)`, `placeholder=`; live: `.placeholder`, `.color` |
| `Label` | output | escaped text/number; `.update(text)`; `h="auto"`; live: `.color` |
| `VideoFeed` | output | `.update(bgr_frame)` → binary JPEG; `encode=False` for pre-encoded |
| `AudioFeed` | output | `.update(pcm_chunk)` → Web Audio playback |
| `Plot` | output | `.update(fig_or_html)` (Plotly figure or HTML, in an iframe) |
| `LivePlot` | output | streaming telemetry; `.push({trace: y \| [y…]}, x=)` (one point or a batch), `.clear()`, `smoothing=` |
| `Histogram` | output | distribution over time; `.add(values, step)` |
| `Custom` | bidirectional | arbitrary HTML in a sandboxed iframe; `@on(event)`/`@on_message`/`@on_binary`, `.push(data)`/`.push_binary(bytes)`, `.update(html/css/js)`; `canvas.sendBinary(buf)` (browser→Python raw bytes); `canvas.requestCamera(opts)`/`canvas.requestMicrophone(opts)` (parent-page device capture → `@on_binary`) |
| `React` | bidirectional | your JSX, compiled in-browser; `@on(event)`/`@on_request`, `.update(**props)` (scope with `roles=`/`client_id=`), `.push(data)`, `css=` |
| `Markdown` | output | rendered Markdown; `.update(text)` |
| `Image` | output | path/URL/bytes/Matplotlib/PIL/array; `.update(src)`, `fit=` |
| `Table` | bidirectional | DataFrame/Series/records/dict → sortable, filterable, paginated; toolbar buttons toggle a `#` index column, a `cols ▾` column-visibility checklist, and a `sel` row-selection column; `@on_select` fires with the list of selected 0-based row indices; `.selected`, `.update(data)` |
| `WebView` | output | external site in an iframe; `.navigate(url)` |
| `Chat` | bidirectional | shared room across viewers; `.post(text)`, `@on_message` |
| `FileBrowser` | bidirectional | navigate a folder (sandboxed to `root=`); `@on_select`, `.value`, `pattern=` |
| `Download` | input | a button that sends a host file/`bytes` to the viewer; `source=` (path or bytes) or `@provide`, `filename=` |
| `Upload` | input | a button / drop-zone that receives a viewer's file into Python; `@on_upload`, `.value`, `dest=` (stream to disk), `accept=`, `multiple=`, `max_size=` |
| `Repl` | bidirectional | on-canvas Python REPL; needs `enable_repl()` |
| `Inspector` | output | live panel/globals state browser |

## Canvas shapes

Beyond panels, you can place **managed tldraw shapes** directly on the canvas —
vector shapes, freehand strokes, text, sticky notes, lines, artboard frames, and
highlighter marks. These are Python-owned: they survive page reload, update live,
and are excluded from the free-form drawing sync. User-drawn shapes are *ephemeral*
— they live in the tldraw store only — but `on_draw` lets Python observe and react
to them.

### Shape factories

All return a shape object whose properties you can read and set live.

| Factory | Creates | Key kwargs |
|---|---|---|
| `canvas.geo(x, y, w, h, geo=…)` | Rectangle, ellipse, cloud, star, diamond, triangle, etc. | `geo`, `color`, `fill`, `dash`, `size`, `text` |
| `canvas.text(x, y, text=…)` | Floating plain text | `color`, `size`, `font`, `align` |
| `canvas.note(x, y, text=…)` | Sticky note (coloured background) | `color`, `size`, `font` |
| `canvas.draw(points, …)` | Freehand stroke (list of `(x, y)` or `(x, y, pressure)` tuples) | `color`, `size`, `dash`, `isClosed`, `isPen` |
| `canvas.highlight(points, …)` | Semi-transparent highlighter | `color`, `size` |
| `canvas.line(points, …)` | Polyline / cubic spline through control points | `color`, `dash`, `size`, `spline="cubic"` |
| `canvas.frame(x, y, w, h, label=…)` | Named artboard container | `label` |

Every factory also accepts `name=` (the Python identity key — reach it via
`canvas.<name>`) and `x=`/`y=`/`rotation=`/`opacity=`.  `geo=` values: `"rectangle"`,
`"ellipse"`, `"cloud"`, `"star"`, `"diamond"`, `"triangle"`, `"pentagon"`,
`"hexagon"`, `"octagon"`, `"arrow"`, `"cross"`, `"check-box"`, `"heart"`,
`"oval"`, `"rhombus"`, `"rhombus-2"`, `"trapezoid"`, `"x-box"`. `color`
values: `"black"`, `"blue"`, `"green"`, `"grey"`, `"light-blue"`, `"light-green"`,
`"light-red"`, `"light-violet"`, `"orange"`, `"red"`, `"violet"`, `"white"`,
`"yellow"`. `fill` values: `"none"`, `"semi"`, `"solid"`, `"pattern"`. `dash`:
`"draw"`, `"dashed"`, `"dotted"`, `"solid"`. `size`: `"s"`, `"m"`, `"l"`, `"xl"`.

```python
import math
import pycanvas

canvas = pycanvas.Canvas()

# Geo shapes
box   = canvas.geo(x=40, y=40, w=200, h=120, geo="rectangle", color="blue", fill="semi")
ell   = canvas.geo(x=260, y=40, w=160, h=120, geo="ellipse", color="green", fill="solid")
cloud = canvas.geo(x=440, y=40, w=180, h=120, geo="cloud", color="light-blue", fill="semi",
                   text="cloud", name="cloud-box")

# Freehand stroke (list of (x, y) tuples; origin is derived automatically)
wave = canvas.draw(
    [(40 + i * 4, 220 + int(25 * math.sin(i * 0.4))) for i in range(60)],
    color="red", size="m", name="wave",
)

# Polyline / cubic spline
canvas.line([(40, 300), (120, 250), (200, 300), (280, 250)], color="black", name="zig")
canvas.line([(320, 300), (400, 250), (480, 300)], color="blue", spline="cubic")

# Sticky note and floating text
canvas.note(x=560, y=40, text="Sticky!", color="yellow")
canvas.text(x=560, y=180, text="Floating text", color="grey", size="l")

# Artboard frame
canvas.frame(x=40, y=360, w=700, h=200, label="Overview", name="frame")

canvas.serve(port=8000)
```

### Live updates

Every shape property can be written after creation; writing broadcasts the change:

```python
cloud_box.color = "orange"          # color setter
cloud_box.text  = "updated"         # text setter (geo/text/note only)
cloud_box.w = 240; cloud_box.h = 140    # size setters (geo/frame)

box.update(x=100, opacity=0.7, color="red")   # any mix of top-level + props

box.move(x=200)                     # move along one axis only
box.remove()                        # delete it
canvas.remove_shape("cloud-box")    # by name, or pass the object
```

`canvas.shapes` is the current list of managed shapes.

### Observing user-drawn shapes

User freehand drawing is ephemeral (Python can't pre-place it), but `on_draw`
fires whenever viewers draw, move, or delete shapes:

```python
@canvas.on_draw
def on_user_draw(event):
    # event = {"added": [DrawingShape, …],
    #           "updated": [DrawingShape, …],
    #           "removed": [shape_id_str, …]}
    for s in event["added"]:
        print(f"new {s.type} at ({s.x:.0f}, {s.y:.0f})  color={s.color}")
    for s in event["updated"]:
        print(f"moved/resized {s.id}")
    for sid in event["removed"]:
        print(f"deleted {sid}")
```

`canvas.drawings` is a live snapshot `{id: DrawingShape}` of all user-drawn shapes.
Each `DrawingShape` exposes `.id`, `.type`, `.x`, `.y`, `.rotation`, `.opacity`,
`.props`, `.color`, `.text`, and `.update(**kw)` / `.remove()` — so Python can
mutate or delete ephemeral shapes too:

```python
@canvas.on_draw
def tidy(event):
    for s in event["added"]:
        if s.type == "draw" and s.color == "red":
            s.update(color="blue")   # immediately recolour user strokes
```

`canvas.off_draw(fn)` deregisters. Use `canvas.on_draw` as a decorator or call it
with a function directly — both forms work.

See [`examples/tldraw_shapes.py`](examples/tldraw_shapes.py) for a runnable demo
covering all shape types and the drawing observer.

## The three data verbs

| Verb | Means | Replayed on reconnect? | Panels |
|---|---|:--:|---|
| `.update(value)` | **replace** the panel's whole state | ✅ yes | `Label`, `Image`, `Table`, `Markdown`, `Plot`, `Slider`, `Toggle`, `Button`, `VideoFeed`, `AudioFeed` |
| `.push(sample)` | **append** one sample to a live stream, no re-render | ❌ no | `LivePlot`, `Custom`, `React` |
| `.add(values, step)` | **record** one distribution snapshot at `step` | ✅ yes | `Histogram` |

```python
label.update("ready")                 # replace: the label IS this text
plot.push({"train": loss}, x=step)    # append: one more point on a live curve
weights.add(layer.weight, step=epoch) # record: one distribution row at this step
```

`Plot` re-renders a full Plotly figure each `.update`; `LivePlot` streams just
the data onto a mounted chart (smooth at 10+ Hz). Trace keys need not be
declared — `traces=` only fixes legend order; pushing a new key adds a trace.

`LivePlot.push` also takes a **batch** — a list/array per trace — to add many
points in one call instead of a loop, with `x` a matching list (or omitted to
auto-index). This is your lever on update *rate*: buffer in your loop and flush
when you choose, so a fast producer needn't render every step (the server
coalesces updates a slow client can't keep up with, as a safety ceiling for when
you don't).

```python
plot.push({"train": losses, "val": vals}, x=steps)   # many points at once
plot.push({"train": losses})                         # x auto-indexes each point
```

## Controlling panels live

Every panel:

```python
panel.update(...)                 # push new state (signature varies per component)
panel.move(x, y); panel.resize(w, h); panel.rotate(deg)
panel.opacity = 0.5               # live fade; 1.0 is fully opaque (default)
panel.set_layout(x=, y=, w=, h=, rotation=, opacity=, locked=, ...)   # any combo, one message
panel.set_layout(x=, y=, roles=["admin"])         # scope to roles/client_id (per-viewer layout)
panel.to_front(); panel.to_back(); panel.forward(); panel.backward()   # z-order
panel.x, panel.y, panel.w, panel.h, panel.rotation   # read/write live
panel.label = "New title"         # live card header rename
panel.value                       # current value (sliders, toggles, button count)
panel.queue = "latest"            # backpressure policy (below)
```

**Accent color** — any `color=` panel accepts `.color` as a live read/write property. Assigning updates both the CSS theme inside the panel and the card border tint immediately, with no restart:

```python
status = canvas.label("status", "idle", color=(0, 200, 0))
status.color = (100, 100, 255)   # live; (r, g, b) tuple or "#rrggbb" hex
status.color = None              # reset to default theme
```

`color=` works at construction and as a live setter on `Label`, `Slider`, `Toggle`, `Button`, `TextField`, and any `react(...)` panel.

**Component-specific live setters:**

```python
slider.min = 0; slider.max = 360; slider.step = 5   # range / step live
toggle.options = ["A", "B", "C"]                     # swap option list live
text_field.placeholder = "Search…"                   # hint text live
```

`to_front`/`to_back` persist across reload; `forward`/`backward` are a live
nudge only.

## Receiving input

```python
@slider.on_change                              # fn(value)
@toggle.on_change                              # fn(value)
@button.on_click                               # fn()
@text_field.on_change                          # fn(text); fires on Enter / blur
@table.on_select                               # fn(indices); 0-based row indices
@panel.on_layout                               # fn(comp), after a user drag/resize
@chat.on_message                               # fn(entry); reply with chat.post(text)

# all input decorators accept threading flags:
@slider.on_change(threaded=True)               # new thread per call
@slider.on_change(dedicated=True)             # one persistent thread for this handler
@slider.on_change(dedicated=True, queue="latest")  # + drop stale calls while busy
```

These are plain registration methods, so `@panel.on_change` and
`panel.on_change(fn)` are the *same thing* — decorate for the usual one-handler
case, or call it directly to reuse one handler across panels
(`for p in panels: p.on_message(handle)`) or to register a function defined
before the panel exists.

**Who did it** — *any* of these handlers may declare a trailing `viewer`
parameter to learn which connected viewer acted; one-arg handlers are unchanged:

```python
@slider.on_change
def _(value, viewer):     # {"id","name","color","role"}
    print(viewer["name"], "→", value)

@panel.on_layout
def _(comp, viewer): ...           # who moved it

@panel.on("save")
def _(msg, viewer): ...            # React/Custom inbound message

@panel.on_request("validate")
def _(req, viewer): ...            # the awaitable path, too
```

**Action routing + field validation.** `@panel.on("name")` dispatches by the
payload's routing field, so a panel with several actions reads as one named
handler each instead of one big `if msg["action"] == …` ladder (set the field
with `event_key=`, e.g. `react(..., event_key="action")`, to match your JSX
`canvas.send({action:'…'})`). Pass `fields={name: type}` to coerce values off the
wire before the handler runs — and give validation a home: a value that can't
coerce drops that message (handler not called) and logs why, instead of crashing
the handler on a bad string.

```python
@stock.on("item_set", fields={"stock": int, "price": int})
def _(msg):                        # msg["stock"]/["price"] are ints here
    inventory[msg["item"]] = {"stock": msg["stock"], "price": msg["price"]}
```

`examples/action_routing.py` is a minimal demo; `examples/hackathon/hackathon.py`
uses the pattern at scale (one handler per admin/team action across several panels).

**Handler threading — three modes.** Handlers run on a shared dispatch thread
by default (off the event loop, so the UI never freezes — but a slow handler
holds up the ones queued behind it). Two flags move a handler off that thread:

| Flag | Thread model | Right for |
|---|---|---|
| *(default)* | Inline on shared dispatch thread | Fast handlers: state updates, canvas calls |
| `threaded=True` | New daemon thread *per call* | Occasional slow work: HTTP, `time.sleep`, one-off compute |
| `dedicated=True` | One persistent thread *for this handler*, launched on first call | Handlers that fire rapidly with non-trivial work |

```python
@fetch.on_click(threaded=True)           # new thread per click; doesn't block others
def _(viewer):
    data = slow_api_call()
    table.update(data)

@speed.on_change(dedicated=True)         # own persistent thread; calls are serialised
def _(v):
    result = heavy_compute(v)
    status.update(result)
```

**`dedicated=True` and `queue=`** — unlike `threaded=True` (which spawns a
fresh thread per call), `dedicated=True` gives the handler *one persistent
thread* with its own queue. Calls are always serialised on that thread (no
concurrent self-invocations), and the shared dispatch thread is never blocked.
The `queue=` parameter controls backpressure on that handler's own queue:

- `"fifo"` (default) — every call is queued and run in order.
- `"latest"` — only the most recent *pending* call is kept. The thread always
  runs the current call to completion, then picks up only the latest one,
  dropping any that piled up in between.

```python
@speed.on_change(dedicated=True, queue="latest")
def _(v):
    result = heavy_compute(v)   # user dragged to 73; intermediate values dropped
    status.update(result)
```

The mental model: default → shared conveyor belt; `threaded` → fork a new worker per task; `dedicated` → hand all tasks to one dedicated worker.

**`threaded` and `dedicated` are mutually exclusive.** `queue=` is only
meaningful with `dedicated=True`. These flags work on all handler decorators:
`on_change`, `on_click`, `on_select`, `on_message`, `on(event)`, `on_binary`.

**When the thread starts** is the whole distinction from
[`canvas.background`](#background-workers): both `threaded=True` and
`dedicated=True` only kick in when the bridge dispatches an event (calling the
handler from your own code is always a plain inline call, no thread).
`canvas.background` launches a thread *once, when serving starts* — for
producer loops (a camera, a sensor) that have no triggering event. The trade-off
for `threaded=True` is concurrency: a threaded handler may run alongside itself
if calls arrive fast, so guard any shared state you write. `dedicated=True`
avoids this — the handler is serialised on its own thread, so no extra locks are
needed unless other code also mutates the same state.

<a id="the-viewer-dict"></a>
The `viewer` dict (same shape everywhere it's handed to you — callbacks, uploads,
cursors):

| key | what | trust |
|---|---|---|
| `id` | stable per-connection roster id (use with `client_id=`) | client-side — attribution only |
| `name` | viewer's editable display name | client-side |
| `color` | roster color | client-side |
| `device` | `"mobile"` or `"desktop"`, from the User-Agent | client-side — presentation only |
| `role` | login level from `serve(passwords=)`, else `None` | **server-trusted — authorize on this** |
| `cursor` | `{"x", "y"}` in canvas coords, or `None` (only with `serve(cursors=True)`) | client-side |

Gate permissions on `role` only; `id`/`name`/`color`/`device`/`cursor` are
reported by the browser, so they're great for attribution and per-viewer
targeting but not for authorization. Every key is always present, but on an
**upload** the attribution fields (`id`/`name`/`color`/`device`/`cursor`) are
`None` unless the uploader is still connected — the file arrives over HTTP and is
matched to the live roster by id, so a disconnected or unrecognised uploader
leaves them `None` (only `role`, from the auth session, is guaranteed). Read them
with `.get(...)` and a fallback.

**Adapt to who joins — `canvas.on_connect`.** Runs `fn(viewer)` once each time a
viewer connects, so you can tailor the canvas to *who* (or *what*) joined. The
common case is a mobile layout — and rather than a separate scoping axis per
attribute, you reuse the per-viewer `client_id=` that `set_layout`/`update`
already take, so the same hook handles `device`, `role`, `name`, anything:

```python
@canvas.on_connect
def adapt(viewer):
    if viewer["device"] == "mobile":            # also: viewer["role"], ["name"], …
        for i, panel in enumerate(panels):
            panel.set_layout(client_id=viewer["id"], x=0, y=i * 220, w=360)
```

It fires after the viewer's initial state is sent, so the override lands as a
live tweak on top (a brief reflow on slow links). `device` is a best-effort,
spoofable User-Agent guess — fine for layout, never for auth.

`canvas.on_disconnect(fn)` is the symmetric twin — `fn(viewer)` once when a
viewer leaves (the departed viewer's last-known dict) — for cleanup: release a
per-viewer resource, log how long they stayed, drop them from your own
bookkeeping. It runs after they're off the roster, so it's for teardown, not for
messaging them.

**Custom panels (your own protocol):** browser JS calls
`canvas.send({event:'x', ...})`; Python routes with `@panel.on("x")`; Python
replies via `panel.push(data)`, received in JS by `canvas.onPush(cb)`.

## Show anything

`canvas.show(value)` inspects the value and inserts the best panel — like a
notebook deciding how to render an `Out[...]`, but works in plain scripts:

```python
canvas.show(df)                    # DataFrame → interactive Table
canvas.show(fig)                   # Matplotlib / Plotly → Image / Plot
canvas.show({"status": "ok"})      # dict / list → pretty JSON
canvas.show("use **bold**")        # Markdown syntax → rendered text
canvas.show("report.csv")          # existing file → Table; "photo.png" → Image
canvas.show("https://site.com/x.png")  # image URL → Image; web URL → link
canvas.show(model)                 # _repr_html_/_repr_png_ → its rich view
```

Dispatch is conservative (single `*italic*` isn't Markdown; a path must be a
real file). No `name` → fresh panel each call; `name=` replaces in place.
`pycanvas.panel_for(value)` builds without inserting. Matplotlib figures are
released from pyplot after rendering — no manual `plt.close()`.

## Create your own components

Beyond the built-ins, two factories ship custom UI straight from Python:
**`react`** mounts your JSX as a real React subtree (the native, theme-aware
path — reach for this first), and **`custom`** drops arbitrary HTML/CSS/JS into a
sandboxed iframe. Both are bidirectional: `canvas.send(...)` posts up to Python,
`push`/`update` send down.

### React panels

`React` ships JSX *source* from Python, compiled in the browser and mounted as a
real React subtree (no npm build, no postMessage hop, inherits the canvas theme,
interactive from first hover). Pass a full `source=` defining
`function Component({ canvas, value, props })`, or just `jsx=` markup + `css=`:

```python
counter = canvas.react(jsx='<button onClick={() => canvas.send({n: 1})}>tap</button>',
                       css='button { font-size: 18px; }')
```

`push(data)` reaches the component as the `value` prop. The `canvas` prop is the
bridge handle:

| `canvas.…` | Does |
|---|---|
| `send(data)` | panel → Python, routed to `@on(event)`/`@on_message` (fire-and-forget) |
| `request(data)` | **awaitable** twin: `await canvas.request({event:'…'})` resolves with the matching `@on_request` handler's return |
| `onFrame(cb)` | subscribe (in `useEffect`) to `push`/`push_binary` with **no re-render**; `ArrayBuffer` for binary. Use this *or* `value`, not both |
| `viewport(cb)` | called now + on every camera move with `{x, y, zoom}`; returns unsubscribe |
| `setView({x, y, zoom})` | pan/zoom the canvas (any subset of keys) |
| `chat` | shared room: `send`, `setName`, `history`, `subscribe`, `identity` |

- `push_binary(bytes)` → `onFrame` as a zero-copy `ArrayBuffer`. Pass `queue="latest"` on the panel for video/sensor streams. React panels can receive binary from Python but cannot send binary back — use a `Custom` panel for browser → Python binary.
- `scope=["d3"]` loads ESM libs from a CDN, exposed as the `libs` global.
  Friendly names (`d3`, `lodash`, `date-fns`, `framer-motion`, `lucide`) map to
  pinned React-externalised builds; anything else passes through to esm.sh.
- `source=` accepts any React snippet pasted from the web — `import`/`export`
  lines, `styled-components`, `@keyframes`, and React hooks are all normalised
  automatically before reaching the browser.
- React panels **auto-height by default** (fit their content); pass a numeric `h`
  to pin one. `w="auto"` opts into content-width too.

Authoring conveniences (Python side):

- **`css=` works with `source=` too** — keep a full component's styles in a
  separate string instead of an inline `<style>`; it's rendered ahead of the
  component (scoped by your own selectors). Load either half from a file with
  `path=` (the JSX) / `css_path=` (the stylesheet) to keep both in sibling files.
  `panel.set_css(...)` updates it live.
- **`panel.update(roles=…, client_id=…, **props)`** — scope an update to specific
  viewers: a login role (from `serve(passwords=)`) and/or an id (from
  `canvas.viewers`). The props are stored as a per-viewer *overlay* on the shared
  state (precedence shared < role < client) and — unlike `push` — **persist and
  replay on reconnect**, so each viewer sees only their own slice (a per-team
  budget, a personalised greeting) with no client-side filtering of a global blob.
  Omit both to update the shared state for everyone. (`update_for(role=…)` is a
  deprecated alias. See `examples/hackathon/hackathon.py`.)
- **`panel.validate()`** — a fast structural lint that catches a missing
  `Component` or unbalanced `()/[]/{}` before they become a cryptic browser
  error. Returns a list of problems (empty = OK); handy as `assert not
  panel.validate()` in a test, or as a startup check.
- **`panel.watch(path=…, css_path=…)`** — dev hot-reload: edit the `.jsx`/`.css`
  on disk, save, and the panel recompiles live (no restart). Build it with
  `path=` and call `panel.watch()` before `serve()`; returns a `stop()`.
- `react(...)` spells out placement/visibility (`x/y/w/h`, `roles`, `lock_for`,
  the lock/chrome flags) as explicit keyword args, so they autocomplete.

### Custom HTML panels

`Custom` renders any HTML/CSS/JS (or a file via `path=`) in a sandboxed iframe
with a symmetric channel injected as `canvas`: `canvas.send(data)` posts to
Python, `canvas.onPush(fn)` receives streamed data.

```python
panel = canvas.custom(html='''
  <button onclick="canvas.send({event: 'go'})">go</button>
  <script>canvas.onPush((msg) => document.body.append(msg))</script>
''')

@panel.on("go")            # routes {event: 'go'}; @panel.on_message is a catch-all
def handle(msg):
    panel.push("clicked")  # → canvas.onPush in the iframe
```

- `html`/`css`/`js` may be separate strings (handy for pasted
  [uiverse.io](https://uiverse.io) snippets); composed under the hood.
- `panel.update(html)` swaps the whole document (reloads the iframe);
  `panel.push(data)` streams without reloading (keeps focus/scroll/listeners).
- `panel.push_binary(bytes)` streams raw bytes on a binary frame (no JSON/base64,
  same fast path as video); `canvas.onPush` receives an `ArrayBuffer`. Honours
  `queue=`; pass `queue="latest"` to drop stale frames under backpressure —
  right for video feeds and high-rate sensor streams where only the newest
  value matters.
- **`canvas.sendBinary(buf)`** — the upward twin: transfers an `ArrayBuffer` from
  the iframe to Python with zero JSON/base64 overhead. Python receives the raw
  bytes with `@panel.on_binary`. Mark the handler `threaded=True` if it does
  any compute (decoding, ML inference) — binary handlers run on the shared
  dispatch thread by default, so a slow one stalls all other panel events:

  ```python
  @panel.on_binary(threaded=True)
  def got(data: bytes, viewer):
      frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
      feed.update(frame)   # update() is thread-safe; no lock needed
  ```

  > **Custom panels only.** `sendBinary` and `requestCamera`/`requestMicrophone`
  > are only available inside a `Custom` (sandboxed-iframe) panel's JS, because
  > they need direct socket access that the React subtree doesn't expose.
  > React panels can *receive* binary from Python via `push_binary` → `onFrame`,
  > but cannot send binary back up. Use a `Custom` panel when you need
  > browser → Python binary.

- **`canvas.requestCamera(opts)`** / **`canvas.releaseCamera()`** — capture
  the webcam from the **parent page** (browsers block `getUserMedia` inside a
  sandboxed null-origin iframe even with `allow="camera"`). The parent runs
  `getUserMedia`, encodes each frame as JPEG, and relays it in two directions
  simultaneously: up to Python as a `BIN_INPUT` frame (received by `@on_binary`)
  and back down into the iframe via `canvas.onPush` (as an `ArrayBuffer`) so
  the panel can display it without a Python round-trip. `opts`: `width` (320),
  `height` (240), `fps` (0 = max rate ≤60), `quality` (0.7). No `fps` or `fps=0`
  means the loop fires on every animation frame and self-throttles when JPEG
  encoding is still in progress (never queues up).

- **`canvas.requestMicrophone(opts)`** / **`canvas.releaseMicrophone()`** — same
  pattern for microphone audio. Before the first chunk arrives, a JSON
  `{event: 'mic_start', sampleRate, channels}` is sent via `canvas.send()` so
  Python knows the stream format. Each subsequent chunk is int16 PCM at the
  browser's native sample rate (~48 kHz), received by `@on_binary`:

  ```python
  @panel.on('mic_start')
  def started(msg, viewer):
      print(msg['sampleRate'], 'Hz')

  @panel.on_binary
  def got_audio(data: bytes, viewer):
      samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768
  ```

  `opts`: `bufferSize` (default 4096 samples ≈ 85–93 ms per chunk). Requires a
  user gesture (e.g. a button click) before calling `requestMicrophone()`.

- `event_key=` changes the routing field (default `event`).
- Anything that renders to HTML works: matplotlib (`savefig` → base64 `<img>`),
  Plotly (`fig.to_html(include_plotlyjs='cdn')`, stays interactive).

Subclass `Custom` only to package HTML behind a typed constructor or to override
`state_payload()` — called for every connecting client so the iframe is seeded
with current state on load (no ready-handshake needed).

## Specific panels

**Web pages** — `WebView` embeds a real third-party site (`allow-same-origin`,
so YouTube/maps/web-apps run; `watch?v=` links are rewritten to `/embed/`).
Sites sending `X-Frame-Options: DENY` refuse to load (a browser rule).

```python
web = canvas.webview("https://en.wikipedia.org/wiki/Robot")
web.navigate("https://example.com")
```

**File downloads** — `Download` is a button that sends a host file (or
freshly-generated `bytes`) to the viewer's machine. *Host code* picks what each
click serves — the viewer never names a path — so, unlike `FileBrowser`, there's
nothing to sandbox. Pass a static `source=` (path or bytes), or register
`@download.provide` to build the content on every click. The browser only ever
receives an unguessable, short-lived URL, streamed behind the canvas's auth gate
(set a `password` for a shared/tunneled canvas and downloads are protected too).

```python
canvas.download("report", source="out/report.pdf", text="Download report")

dl = canvas.download("export", text="Export CSV")
@dl.provide
def _():
    return ("data.csv", make_csv().encode())   # (filename, bytes); path or bytes also ok
```

**File uploads** — `Upload` is the mirror image: a click-or-drop zone that
streams a viewer's file *up* to Python over plain HTTP (no WebSocket size
limits), behind the same auth gate. By default the bytes arrive in memory
(`file.data`); pass `dest=` a directory to stream each upload to disk instead
(`file.path`) — constant memory, right for large files. The browser-supplied
filename is sandboxed inside `dest` (no `../` escape). Set `max_size` on any
public/tunneled canvas.

```python
up = canvas.upload("upload", text="Upload CSV", accept=".csv", max_size=5_000_000)
@up.on_upload
def got(file):                       # an UploadedFile: .name .size .data/.path
    table.update(list(csv.DictReader(io.StringIO(file.data.decode()))))

big = canvas.upload("files", dest="uploads/", multiple=True)   # streamed to disk
```

**Who uploaded it** — `on_upload` takes an optional `viewer` arg:

```python
@up.on_upload
def got(file, viewer):
    # {"role": "manager", "id": "a1b2c3d4", "name": "Fox", "color": "#ef4444",
    #  "cursor": None}  — id/name/color/cursor are None if the uploader has left
    file.save(f"uploads/{viewer.get('role') or viewer.get('name') or 'anon'}/")
```

It's the same [`viewer` dict](#the-viewer-dict) as everywhere else: gate
permissions on the server-trusted `role`; `id`/`name`/`color` are client-reported
(great for attribution/per-user folders, not for authorization) and are `None`
when the uploader isn't a currently-connected viewer — so read them with `.get`
and a fallback, as above.

**Audio** — `AudioFeed` streams PCM played back-to-back via Web Audio. Capture
needs `[audio]`; playback needs nothing. Each viewer clicks **Enable audio**
once (autoplay block).

```python
mic = canvas.audio("mic", sample_rate=16000)
mic.update(chunk)   # NumPy float32/int16 or raw int16 bytes
```

**Chat & viewers** — shared room; each viewer edits their display name. Python
can join:

```python
chat = canvas.chat("chat")
chat.post("welcome 👋")
@chat.on_message
def log(entry): print(entry["name"], entry["text"])
```

**Live cursors** — `serve(cursors=True)`: viewers see each other's cursors and
Python reads every pointer. Default on only for a private local bind.

```python
tip = canvas.viewers[0]["cursor"]    # {"x", "y"} in canvas coords, or None
@canvas.on_cursor
def _(viewer): ...                   # streaming form, fires on each move
```

With cursors on, every entry in `canvas.viewers` (and each callback `viewer`)
carries the extra `cursor` key — see [the `viewer` dict](#the-viewer-dict).

**Streaming performance** — `queue` policy decides what happens when updates
outpace a slow viewer:

- `"fifo"` (default) — deliver everything in order.
- `"latest"` — keep only the newest pending value per viewer (right for
  video/telemetry). `VideoFeed` defaults to this.

```python
plot = canvas.live_plot("temps", queue="latest")   # or plot.queue = "latest" later
```

# 3. Layout

`x`/`y` are canvas coords, `w`/`h` pixels (aliases `width`/`height`), `rotation`
degrees clockwise. Omit `x`/`y` → panels auto-arrange (left-to-right,
top-to-bottom, packed by size). Omit `w`/`h` → component default.

Every factory and `insert(...)` accepts the same `**place` options:

| Option | What it does |
|---|---|
| `x` / `y` | canvas position; omit → auto-arrange |
| `w` / `h` (`width` / `height`) | size in px; `"auto"` fits content (Custom/React panels) |
| `rotation` | degrees clockwise |
| `opacity` | 0.0 = fully transparent → 1.0 = fully opaque (default) |
| `below` / `above` / `right_of` / `left_of` | place relative to another panel… |
| `gap` | …this many px away (default 16) |
| `queue` | backpressure policy: `"fifo"` (all, in order) or `"latest"` (drop stale) |
| `roles` | login roles allowed to see the panel (`[]`/omit = everyone) |
| `lock_for` | roles that get it non-interactive (`operable=False`) |
| `locked` / `draggable` / `resizable` / `operable` / `grabbable` / `frame` | lock & chrome flags — see [Locking & interactivity](#locking--interactivity) |

> **Recommended: use relative placement to avoid overlapping panels.** Hard-coded
> `x`/`y` coordinates are brittle — change one panel's size and everything below
> it needs manual adjustment. `below=`, `above=`, `right_of=`, and `left_of=` pin
> each panel relative to its neighbour, so the layout stays gap-correct no matter
> what changes around it.

**Relative placement** — anchor to a placed panel; `gap` defaults to 16:

```python
controls = canvas.slider("t", min=0, max=1, step=0.01, below=plot)
legend   = canvas.markdown("…", right_of=plot, gap=24)
button   = canvas.button("go", below=controls, right_of=plot)   # two anchors = both axes
```

`below`/`above` align left edges; `right_of`/`left_of` align tops. An explicit
`x`/`y` overrides the derived coordinate. The anchor must already have a
position. When an auto-height panel (`h="auto"`) settles its final height in the
browser, every panel anchored `below=` it (and panels anchored below those)
shift automatically to close or open the gap — no manual refit needed.

**Auto-layout containers** — `column` stacks panels top-to-bottom; `row` places
them side-by-side. Use `.add()` explicitly or open a `with` block to capture
panels automatically. Containers nest — call `.row()` / `.column()` on a
container to add a child container:

```python
layout = canvas.column(x=60, y=40, w=640, gap=20)
layout.add(canvas.label("title", "My App"))

with layout.row(gap=8):               # nested row inside the column
    canvas.label("step", "Step: 0")
    canvas.label("loss", "Loss: —")
    canvas.button("start", text="Start")

layout.add(canvas.markdown("…", h="auto"))   # grows; panels below auto-shift
layout.add(canvas.label("status", "Ready"))
```

`grid` fills a fixed grid slot-by-slot:

```python
with canvas.grid(cols=2, slot=(560, 300), gap=24, x=40, y=40):
    canvas.live_plot("loss")
    canvas.live_plot("accuracy")
    canvas.image(fig)
```

When an `h="auto"` panel grows, the container repacks automatically — siblings
shift to close the gap without any manual call. `container.move(x, y)` repositions
the whole tree live. To insert a panel between existing ones, use
`container.insert_before(ref, panel)` or `container.insert_after(ref, panel)`,
where `ref` is any panel already in that container.

**Streamlit mode** — `canvas.streamlit()` sets vertical-scroll navigation and
returns a full-viewport-width column. Every child spans the browser window and
the page scrolls vertically — no fixed `x`/`y`/`w` needed:

```python
page = canvas.streamlit(gap=20, padding=24)
page.add(canvas.label("title", "Training Dashboard"))

with page.row(gap=10):
    canvas.button("start", text="Start")
    canvas.slider("lr", min=0.001, max=0.05, default=0.01, step=0.001)

page.add(canvas.markdown("…", h="auto"))
page.add(canvas.label("status", "Ready"))
```

**Auto-height** — `h="auto"` fits a panel's height to its rendered content
(Custom-/React-based panels: `markdown`, `custom`, `table`, `image`, `label`,
controls). React-based panels (`react`/`table`/`label`) auto-height by default —
pass a numeric `h` to pin one. Also a live property:

```python
notes = canvas.markdown("# Heading\n\nas tall as this text", h="auto")
notes.h = 240          # assigning a number turns auto off
```

Layout reflects both what Python set **and** the user's drags/resizes/rotations
(reported back, so `x`/`y`/`w`/`h`/`rotation` stay in sync). A panel's `x`/`y`
are `None` until first placed.

**Per-role layout** — scope placement/size to viewers with
`set_layout(roles=…, client_id=…)` (precedence shared < role < client, the same
overlay model as content and view). A role can have its own arrangement, and a
user's drag writes back to whichever layer their layout came from — see the
[Roles](#roles-one-rule-for-everything-per-viewer) overview.

## Locking & interactivity

Five independent controls; set on `insert`/a factory, or flip live as a
property. Mix freely.

| Control | Move? | Resize? | Controls operable? | `update()` renders? |
|---|---|---|---|---|
| *(default)* | yes | yes | yes | yes |
| `draggable=False` | **no** | yes | yes | yes |
| `resizable=False` | yes | **no** | yes | yes |
| `operable=False` | yes | yes | **no** | yes |
| `grabbable=False` | **no** (Python only) | **no** | yes, **immediately** | yes |
| `locked=True` | **no** | **no** | **no** | **no** (frozen) |

```python
servo.draggable = False     # can't drag; slider still works
servo.operable = False      # user can't operate it; your update()s still move the thumb
servo.locked = True         # full freeze, including programmatic updates
servo.pin();  servo.unpin() # draggable=False + resizable=False
servo.lock(); servo.unlock()
```

Key distinction: `operable=False` blocks the *user* while your code keeps
driving the control; `locked=True` freezes everything including your `update()`s.

`grabbable=False` (content-heavy panels) drops the click-to-select cover so the
widget is hover/click-live immediately, and makes the panel unselectable —
move/resize from Python only.

`frame=False` strips card chrome (background, border, shadow, padding, label,
hover outline) so content sits directly on the canvas. Pair with
`grabbable=False` for a free-floating widget; add `operable=False` for a
click-through decorative overlay.

# 4. Views & navigation

Pass a `view` dict to `serve` (all keys optional):

| Key | Effect |
|---|---|
| `x`, `y`, `zoom` | initial camera (centre on `(x, y)` at `zoom`; 1.0 = 100%) |
| `locked` | `True` freezes pan/zoom (kiosk view) |
| `min_zoom`, `max_zoom` | clamp zoom range |
| `ui` | `False` hides tldraw chrome **and** the Inspector button |
| `grid` | `True` shows the background grid |
| `read_only` | `True` blocks freehand drawing |
| `navigation` | `'free'` (default), `'scroll_y'`, `'scroll_x'`, or `(mode, zoom)` tuple — see [Camera navigation mode](#camera-navigation-mode) |

```python
canvas.serve(view={"x": 200, "y": 160, "zoom": 1.0, "locked": True, "ui": False})
```

Omit `x`/`y`/`zoom` and each viewer's canvas opens framed on the panels they can
see — fit and centred (zooming out if they overflow, never in past 100%). Set
any of them to take explicit control.

Change it live with `set_view` (same keys; only those you pass change). Pass
`roles` to scope it to a login role (from `serve(passwords=)`), or `client_id`
to move just one viewer (ids from `canvas.viewers`):

```python
canvas.set_view(ui=False)
canvas.set_view({"zoom": 2.0})
canvas.set_view(read_only=True, ui=False, roles=["user"])  # by login role
canvas.set_view(x=0, y=0, zoom=1.5, client_id=some_id)     # one viewer only
```

Per-role view applies on connect too, so e.g. admins keep the toolbar and
drawing while `"user"` viewers get a chrome-free, read-only canvas. Precedence
is global < per-role < per-client.

A toolbar button (bottom-left) spawns an ephemeral `Inspector` on demand —
offered only on a local bind by default (`ui_inspector=True`/`False` to
override), since it can surface state to everyone.

A second button (**Graveyard**, stacked just above it) lists any panel the user
has deleted from the canvas. Python keeps the component alive — callbacks and
state intact — and clicking **Restore** re-registers the shape without restarting
the script. On by default for a private local bind; override with
`serve(ui_graveyard=True/False)`.

## Camera navigation mode

By default the canvas uses tldraw's free navigation: pinch/scroll to zoom,
drag to pan. Pass `navigation=` to `set_view` to switch to a constrained mode
— useful for vertical dashboards or horizontal timelines where you want the
scroll wheel to actually scroll rather than zoom.

```python
canvas.set_view(navigation='scroll_y')   # vertical scroll only; wheel scrolls down
canvas.set_view(navigation='scroll_x')   # horizontal scroll only; wheel scrolls right
canvas.set_view(navigation='free')       # restore default free navigation
```

Pass a `(mode, zoom)` tuple to lock the zoom level at the same time:

```python
canvas.set_view(navigation=('scroll_y', 0.75))   # vertical scroll, fixed 75% zoom
canvas.set_view(navigation=('scroll_x', 1.5))    # horizontal scroll, fixed 150% zoom
```

Because `navigation=` is a regular `set_view` key it obeys the same scoping
rules as every other view option — scope it to a role or a single viewer:

```python
canvas.set_view(navigation='scroll_y', roles=['kiosk'])   # kiosk viewers scroll only
canvas.set_view(navigation='free', client_id=some_id)     # one viewer gets free nav
```

In a constrained mode:

- The scroll wheel (or trackpad two-finger swipe) **pans** the canvas along
  the free axis instead of zooming.
- The locked axis is fixed — you cannot pan horizontally in `scroll_y` or
  vertically in `scroll_x`.
- Zoom is locked at the supplied level (default 1.0 = 100%); pinch gestures
  do not change it.
- The canvas opens immediately at the correct position and zoom — there is no
  auto-fit re-centre on first scroll.

Navigation mode is stored in the view overlay and replayed to every
reconnecting viewer, so the constraint persists across page reloads and
hot-reloads.

# 5. Serving & sharing

`canvas.serve(port=8000)` opens the browser and blocks. The rest of this section
covers exposing that server to other people and machines.

All of `serve()`'s options in one place:

| Option | Default | What it does |
|---|---|---|
| `port` | `8000` | TCP port to serve on |
| `host` | `"127.0.0.1"` | bind address; `"0.0.0.0"` exposes it on the LAN |
| `open_browser` | `True` | open the system browser on start |
| `block` | `True` | block until shutdown; `False` returns at once for live inserts (notebooks) |
| `wait` | `True` | in background mode, wait until the loop is ready before returning |
| `password` | – | gate the whole canvas behind one password (session cookie) |
| `passwords` | – | `{role: password}` for role-based access (see [Roles](#roles-one-rule-for-everything-per-viewer)) |
| `login_message` | – | host note shown on the password page |
| `tunnel` | `False` | expose publicly through a tunnel |
| `tunnel_provider` | `"cloudflared"` | tunnel backend (`"cloudflared"` / `"localtunnel"`) |
| `allow_remote_exec` | `False` | permit a `Repl` on a non-local/tunneled bind (unauthenticated RCE — opt in deliberately) |
| `persist` | `False` | auto-save/restore the canvas; `True` or a path (see [Saving & loading](#saving--loading)) |
| `hot_reload` | `False` | restart the process when a `.py` changes (script entry only) |
| `view` | – | camera & chrome dict (see [Views & navigation](#4-views--navigation)) |
| `cursors` | auto¹ | viewers report pointer position (`canvas.viewers[i]["cursor"]`) |
| `ui_inspector` | auto¹ | toolbar button letting viewers spawn an Inspector |
| `ui_graveyard` | auto¹ | toolbar button listing panels deleted from the canvas (restore without restarting) |
| `desktop` | auto² | open a native window (pywebview) instead of the browser |
| `window_title` / `window_size` | `"PyCanvas"` / `(1200, 800)` | native-window caption / size |
| `debug` | `False` | log every WebSocket frame to the console |

¹ on by default only for a private local bind (no tunnel, loopback host).
² on by default only inside a baked executable (`sys.frozen`).

## Sharing

**LAN** — bind to all interfaces; `serve()` prints the network URL to open on
other devices:

```python
canvas.serve(port=8000, host="0.0.0.0")
```

**Password** — gate any shared canvas; viewers see a password page once, then a
session cookie carries them (the password is never stored in the cookie):

```python
canvas.serve(port=8000, host="0.0.0.0", password="let-me-in")
```

A password-protected canvas gets two things for free. A **Sign out** button
(bottom-right) clears the session and returns the password page, so a viewer can
switch accounts without restarting — it shows whenever a password is set, even
under a `ui=False` kiosk view (signing out is an auth escape hatch, not app
chrome). And `login_message=` puts a host note on the password page — handy with
`passwords=` to say which password each kind of viewer should enter:

```python
canvas.serve(
    port=8000, host="0.0.0.0",
    passwords={"admin": "secret-admin-pw", "viewer": "view"},
    login_message='Spectators enter "view"; teams enter the password you were given.',
)
```

It's shown as plain text (HTML-escaped, newlines kept) — so don't put a secret
password in it; everyone reaching the login page can read it.

### Roles: one rule for everything per-viewer

Pass `serve(passwords={role: pw})` and each viewer logs in as a **role**. Every
viewer then renders the same canvas, but you can layer **per-viewer overrides** on
top of the shared definition. There's exactly one rule:

> **Precedence is `shared < role < client`.** Omit the scope and you set the
> shared value (everyone); pass `roles=` and/or `client_id=` and you override it
> for just those viewers.

The same `roles=` / `client_id=` scoping works across four axes:

| Axis | shared (everyone) | scoped to viewers |
|---|---|---|
| **Exists?** (visibility) | `canvas.react(..., roles=[])` = all | `roles=["admin"]` on the factory; `add_role`/`remove_role` live |
| **Content** (props/state) | `panel.update(**props)` | `panel.update(roles=…, client_id=…, **props)` |
| **Layout** (x/y/w/h, rotation, locks) | factory `x/y/w/h`, or `panel.set_layout(...)` | `panel.set_layout(roles=…, client_id=…, ...)` |
| **View** (camera, chrome, `read_only`, `ui`) | `canvas.set_view(...)` | `canvas.set_view(roles=…, client_id=…, ...)` |

Scoped overrides **persist and replay on reconnect**, so each viewer keeps their
own slice — a per-team budget, an admin-only toolbar, a kiosk layout. When a user
drags or resizes a panel, the change writes back to whichever layer their layout
came from (their own overlay if any, else the shared base), so hand-arranged
layouts stick. `lock_for=[roles]` is shorthand for "visible but not interactive
for these roles."

In practice — serve different views to different users from the same port. Use
`passwords=` (a `{role: password}` dict) instead of `password=`; mark panels
with `roles=` to restrict visibility; use `lock_for=` to show a panel but make
it non-interactive for certain roles; receive `viewer` as an optional second
argument in any callback to see who triggered it:

```python
# Only "admin" sees the control panel; everyone sees the display.
controls = canvas.react(CONTROLS_SOURCE, name="controls", roles=["admin"])
display  = canvas.react(DISPLAY_SOURCE,  name="display")

# This slider is visible to all but only draggable by admins.
speed = canvas.slider("speed", min=0, max=100, lock_for=["viewer"])

@controls.on_message
def on_action(msg, viewer):           # viewer has id, name, color, role
    print(f"{viewer['name']} ({viewer['role']}) sent {msg}")

canvas.serve(
    port=8000,
    host="0.0.0.0",
    passwords={
        "admin":  "secret-admin-pw",
        "viewer": "public-view-pw",
    },
)
```

Roles work on the same port: the password typed at login determines which panels
a client receives and whether callbacks identify them as `"admin"` or `"viewer"`.
`roles=["admin"]` hides a panel from everyone else; `lock_for=["viewer"]` sends it
to viewers but with `operable=False` so they can't interact. `password=` still
works as before (all viewers get `role=None`).

Roles can be created **after** the server starts: the `passwords=` dict is read
live on every login, so adding a key makes that password valid immediately (no
restart). Reveal a panel to a role added at runtime with `panel.add_role(name)`
(and `panel.remove_role(name)` to hide it again) — both update connected viewers
live, and `panel.roles` reads the current allowlist:

```python
# An admin creates a team at runtime; its members can log in right away.
passwords["Red Team"] = "red-pw"     # same dict passed to serve(passwords=...)
team_panel.add_role("Red Team")      # panel now reaches that role, live
```

See [`examples/stock_management.py`](examples/stock_management.py) for a full
JSON-backed app where the admin creates teams (each with its own password and
budget) on the fly.

**Tunnel** — expose to the whole internet over public HTTPS; keeps the bind on
`127.0.0.1` and prints a shareable `https://…` URL:

```python
canvas.serve(port=8000, tunnel=True)                          # cloudflared (default)
canvas.serve(port=8000, tunnel=True, tunnel_provider="localtunnel")
```

`[tunnel]` downloads & caches cloudflared on first use. The tunnel closes when
the server stops. Quick-tunnel URLs are random and ephemeral.

> **Repl security.** A `Repl` runs arbitrary Python in-process, so any non-local
> exposure (LAN bind, tunnel, merge) is refused unless you pass
> `allow_remote_exec=True` — even behind a password.

## Hot reloading

```python
canvas.serve(port=8000, hot_reload=True)   # run as `python your_script.py`
```

Restarts the process when you save a `.py` in the script's folder; the browser
tab reconnects on its own. A broken save is pre-flighted and skipped — the last
working version keeps serving. Needs `block=True` and a real script entry point.

## Background workers

Register producer loops (camera, sensor, telemetry) with `@canvas.background` —
`serve()` runs each on a daemon thread in the serving process only. It's the
no-event side of the same "give this its own thread" primitive behind
[`threaded=True` handlers](#receiving-input): a `while True` loop here keeps its
thread alive for the app's lifetime; a handler that returns lets its thread
collapse.

```python
feed = canvas.video("webcam")

@canvas.background
def stream():
    cap = cv2.VideoCapture(0)
    while True:
        ok, frame = cap.read()
        if ok: feed.update(frame)

canvas.serve(hot_reload=True)
```

**Prefer `background` with `hot_reload=True`:** a hand-started thread at module
scope starts fresh in every worker on each reload, which is fine for stateless
loops but can briefly double-grab a single-owner resource (camera, serial port)
during the transition between the old and new worker. `background` makes the
intent explicit and is the idiomatic choice here.

## Notebooks

`serve(block=False)` returns immediately so later cells edit the open canvas:

```python
canvas = pycanvas.Canvas().serve(port=8000, block=False)
servo = canvas.slider("servo_1", min=0, max=180, default=90)   # appears live
canvas.remove(servo)
canvas.stop()
```

Handlers keep working after `serve(block=False)` — they fire on UI events just
as in a script, and [`threaded=True`](#receiving-input) behaves the same (it's
triggered by the bridge, not by which cell you're in). `hot_reload` isn't
available here, so `background`'s monitor caveat doesn't apply: its threads
simply start when you call `serve(block=False)`, in the kernel.

`canvas.capture_cells(cols=2)` (alias `pycanvas.autopanel(canvas)`) mirrors
every expression cell's output to its own auto-arranged panel (rendered via
`show()`); re-running a cell swaps its panel in place, keeping any geometry you
dragged. Override one cell with a `# pycanvas: x=40 y=80 w=600 locked=true`
directive line (or `# pycanvas: skip`); pass `auto=False` to flip to an
allowlist. Stop with `stop_capturing_cells()`.

In a plain script, a daemon background server dies with the process — call
`canvas.wait()` to park the main thread.

## Merging canvases

A `Canvas` is single-process, but a *merge host* connects to several running
canvases (as a client), composites their panels onto one port, and routes
interactions back to whichever canvas owns each panel — computation stays
sharded.

```bash
python -m pycanvas.merge :8001 :8002 host3:8003 --port 8080
```

```python
from pycanvas import Merge
Merge([8001, 8002]).serve(port=8080)                  # blocks
Merge(["https://a.trycloudflare.com", ":8002"]).serve(port=8080, tunnel=True)
```

Sources overlay by default (`region_width` spreads them side-by-side). A source
going offline drops its panels until it reconnects. Sources may be tunneled
URLs; the merged view can be tunneled too. Free-form drawings aren't composited.

# Beyond the five steps

## Saving & loading

```python
canvas.save("board.json")                    # browser must be open to capture drawings
# next run, recreate the panels (same names), then:
canvas.load("board.json")                    # snaps panels into place + restores drawings
canvas.load("board.json", formation=False)   # drawings only
canvas.clear()                               # remove all panels and arrows at once

# Non-blocking save — useful in Jupyter so the cell doesn't stall:
fut = canvas.save("board.json", blocking=False)
fut.result()                                 # wait (and raise on error) when ready
```

Panels are code, so only their **placement** is saved, never behaviour.

**Automatic persistence** — `serve(persist=...)` is the hands-off twin of the
above: it loads the saved state on startup and re-saves on every change, so a
canvas survives restarts with no `save`/`load` calls of your own.

```python
canvas.serve(persist=True)              # <script>.canvas.json next to your script
canvas.serve(persist="board.json")      # or choose the file
```

When the file exists it's loaded once your panels are built — each snaps back to
where the user last dragged it and their drawings reappear — then rewritten
(debounced) on every move/resize/draw and once more on a clean shutdown
(`Ctrl+C` / `canvas.stop()`). Delete a panel from your script and its stale
saved position is simply ignored. Leave `persist=False` (the default) to run
entirely fresh from the script every time, reading and writing nothing.

## Tracking an ML training run

The panels above *are* the dashboard — no logging framework. Make each once,
keep the handle, push from your loop:

```python
loss    = canvas.live_plot("loss", traces=["train", "val"], smoothing=0.6)
weights = canvas.histogram("weights", bins=40)
canvas.table({"lr": 3e-4, "batch": 64})       # flat dict → key/value table

@canvas.background
def train():
    for step in range(steps):
        loss.push({"train": train_loss, "val": val_loss}, x=step)
        if step % 50 == 0:
            weights.add(model.fc1.weight, step=step)
            canvas.show(make_grid(batch), name="samples")

canvas.serve()
```

Being bidirectional, the same loop can read controls TensorBoard can't offer (a
pause button, a live LR slider). `live_plot`/`histogram` need `plotly`. See
[`examples/train_dashboard.py`](examples/train_dashboard.py).

## Packaging a desktop app (`bake`)

Ship a canvas as a self-contained executable — no Python/browser/pip on the
target. `bake()` bundles your script + backend + pre-built frontend (via
PyInstaller) into an app that runs in a native window (pywebview).

```python
canvas.bake(name="RobotConsole")     # window_size, icon=, onefile=, distpath= ...
```

- `python your_script.py` → **builds** `dist/RobotConsole(.exe)`.
- launching that exe → **runs** the script in a window (rebuild is skipped when
  frozen).

Build without editing the script via the CLI (your existing `serve()`
auto-switches to a native window when frozen):

```bash
python -m pycanvas.bake your_script.py --name RobotConsole
python -m pycanvas.bake your_script.py --onedir --icon app.ico
```

Needs `[desktop]`. Only the packages your script imports are bundled (heavy
optional deps only when their component is used). Escape hatches:

```python
canvas.bake(name="App", include=["my_plugin"])   # force-add a dynamic import
canvas.bake(name="App", exclude=["torch"])        # skip a dep that breaks the build
```

`serve(desktop=True)` opens the same native window in development.

## How it works

PyCanvas is two halves joined by **one WebSocket**: your Python process, and a
pre-built browser frontend (tldraw + React) it serves. You never touch the
frontend — it ships compiled in the package.

**The model: Python owns state, the browser renders it.** Each panel you make is
a Python *component* object with a unique id. The **bridge** turns your calls
into small JSON frames — `register` (a panel appeared), `update` (its state
changed), `remove` — and ships them to every connected browser. The browser keeps
a tldraw canvas where each component is a *shape*: built-ins (slider, label, …)
are native React widgets, `custom` is a sandboxed iframe, and `react` is your JSX
compiled in the browser (Sucrase) and mounted as a real React subtree. User actions
travel back as `input` / `layout` frames.

**Replay is why reconnects "just work".** The browser is a pure renderer holding
no source of truth, so on every (re)connect the bridge replays the full state —
each panel's `register` + current payload — filtered by the viewer's role. That
same path powers hot reload (a fresh worker replays everything) and the
per-viewer model: a panel's props/layout are the shared base plus any
role/client **overlays**, merged at replay (`register_props_for`, the layout
overlay) and pushed live to matching viewers via `send_to_role` / `send_to_client`.

**Threading.** An asyncio event loop owns the socket; your `@on_change` /
`@on_message` / `@on_layout` handlers run on a single ordered **worker thread**,
so a slow handler never freezes rendering or live `update()` broadcasts (and
outbound sends are marshalled back onto the loop). That worker is shared across
panels, so a slow handler does delay other panels' handlers until it returns —
offload genuinely slow work to your own thread. High-rate media
(`VideoFeed`/`AudioFeed`, `push_binary`) skips JSON entirely on a binary frame.

**Auth & sharing.** Roles come from the password used at login, carried in a
signed session cookie (no server-side session store), so a viewer stays logged in
across reconnects and restarts. Under `hot_reload`, a long-lived *monitor* process
re-execs the worker on each save and owns the tunnel + the cookie secret, so the
public URL and everyone's session survive edits.

**Where to look:** `pycanvas/canvas.py` (the `Canvas` façade + factories),
`pycanvas/bridge.py` (the wire / replay / per-viewer sends), `pycanvas/server.py`
(FastAPI app + auth), `pycanvas/components/` (the panels), and
`pycanvas/frontend/src/bridge.js` (the browser side).

## Debugging the wire

Everything is JSON frames over one WebSocket, so "why didn't it update" is
always "is the frame on the wire?".

```python
canvas.serve(debug=True)        # log every frame: -> out (Python), <- in (browser)

@canvas.on_frame                # programmatic tap; fn(direction, msg)
def log(direction, msg):
    print(direction, msg["type"], msg.get("id"))
```

Connection lines always print (`viewer 'Otter' connected … / disconnected`).
Stale tabs heal themselves: panel ids are minted per run, and a tab from an
earlier run drops the old run's panels and replays the new ones — re-running
never leaves stacked duplicates.

### Protocol

All JSON at `ws://localhost:{port}/ws`:

```json
{ "type": "register", "id": "<id>", "component": "Slider", "props": {…}, "x": 80, "y": 80, "rotation": 0 }
{ "type": "update",   "id": "<id>", "payload": { "value": 120 } }
{ "type": "remove",   "id": "<id>" }
{ "type": "input",    "id": "<id>", "payload": { "value": 120 } }
```

High-rate media (`VideoFeed`/`AudioFeed`, `push_binary`) skips JSON: a binary
frame of `[type][id-length]` + id + raw payload (JPEG / int16 PCM), fed straight
into a `Blob`/`ArrayBuffer`. The same binary frame format travels in both
directions: server → browser for `push_binary` / `VideoFeed` / `AudioFeed`, and
browser → server for `canvas.sendBinary()` / `canvas.requestCamera()` /
`canvas.requestMicrophone()` (the `BIN_INPUT` type), all routed to
`@panel.on_binary` in Python. Server → browser: `register`/`update`/`remove`;
browser → server: `input`.

## Examples

```bash
python examples/hello_world.py            # slider + label
python examples/frontend_backend_tour.py  # interactive tour of the wire, live frame tap
python examples/sensor_dashboard.py       # live VideoFeed + worker thread
python examples/custom_html.py            # hand-written bidirectional HTML panel
python examples/custom_binary_stream.py   # high-rate binary telemetry (push_binary)
python examples/tldraw_shapes.py          # managed geo/text/note/draw/line/frame/highlight + on_draw observer
python examples/binary_input_test.py      # webcam → Python via canvas.requestCamera (browser→host)
python examples/audio_input_test.py       # microphone → Python via canvas.requestMicrophone
python examples/react_canvas_api.py       # React: canvas.viewport / setView / chat
python examples/matplotlib_panel.py       # slider re-renders a matplotlib figure
python examples/plotly_panel.py           # interactive Plotly chart
python examples/robot_control.py          # sliders, toggle, plot, video together
python examples/repl_inspector.py         # on-canvas REPL + inspectors
python examples/download_button.py        # download a host file / generated data
python examples/upload_button.py          # upload a file from the browser to Python
python examples/chat_room.py              # shared chat with editable names
python examples/moving_widget.py          # per-viewer cursor-following emoji
python examples/public_tunnel.py          # share worldwide via HTTPS tunnel
python examples/remote_control.py         # ⚠ stream + control this PC remotely (Windows)
python examples/train_dashboard.py             # TensorBoard-style training tracker
```

Notebooks: `examples/notebook_dynamic.ipynb` (live add/move/remove),
`examples/merge_canvases.ipynb` (two canvases on one merge host). The
matplotlib/plotly examples need `pip install matplotlib plotly`.

## Developing the frontend

The built bundle lives in `pycanvas/frontend/dist/` and is committed. Rebuild:

```bash
cd pycanvas/frontend
npm install
npm run build          # npm run dev + http://localhost:5173/?demo for standalone UI work
```

## Licence

pycanvas is licensed under the [GNU Affero General Public License v3.0](LICENSE)
(AGPL-3.0). Commercial licences are available on request via
daniel.chahine004@gmail.com.
