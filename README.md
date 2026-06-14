# PyCanvas

A Python package that spins up a browser-based spatial canvas where UI panels
are defined and controlled entirely from Python. Components are bidirectional —
Python pushes data to them and reads input back in real time over WebSocket.

Built on [tldraw](https://tldraw.dev) (canvas) + React + Vite (frontend) and
FastAPI + WebSockets (backend). The frontend ships pre-built, so users never
touch Node or npm.

## Install

```bash
pip install -e .
```

The base install is lightweight (sliders, plots, tables, custom/React panels,
images, webviews, chat, …). A few features pull in heavier libraries only when
you ask for them, as optional extras:

```bash
pip install -e ".[video]"     # VideoFeed JPEG encoding (OpenCV, ~90 MB)
pip install -e ".[audio]"     # microphone capture for AudioFeed
pip install -e ".[tunnel]"    # public-internet sharing (serve(tunnel=True))
pip install -e ".[desktop]"   # native window + bake() to a standalone app
```

`canvas.video(...)` needs the `[video]` extra for its default frame encoding —
or stream JPEG bytes you've already encoded with `VideoFeed(encode=False)`,
which needs nothing extra.

## Hello world

```python
import pycanvas

canvas = pycanvas.Canvas()
servo = canvas.slider("servo_1", min=0, max=180, default=90)
status = canvas.label("status", "idle")

@servo.on_change
def handle(value):
    status.update(f"servo at {value}")

canvas.serve(port=8000)  # opens the browser, blocks
```

Run it:

```bash
python examples/hello_world.py
```

Drag the slider in the browser → `servo.value` updates in Python and the
label mirrors it. Resize and drag the cards freely on the canvas.

Your `@on_change` / `@on_layout` / `@panel.on(...)` handlers run on a background
worker thread, not the server's event loop — so a handler that blocks (a
`time.sleep`, an HTTP request, a slow computation, moving a real motor) won't
freeze the canvas or stall other viewers; rendering and live feeds keep flowing.
Handlers for a given panel still run **in order**, so a slider drag settles on
its final value.

## Two ways to add a panel

`canvas.<component>(...)` builds a panel **and** places it in one call — the
concise default. Every component has a factory: `slider`, `toggle`, `label`,
`video`, `audio`, `plot`, `live_plot`, `histogram`, `custom`, `react`,
`markdown`, `image`, `table`, `file_browser`, `webview`, `chat`, `repl`,
`inspector`. Or skip picking
one entirely — `canvas.show(value)` auto-renders any value as the right panel
(see [Show anything](#show-anything)).

```python
servo = canvas.slider("servo_1", min=0, max=180, default=90)
feed  = canvas.video("camera")
plot  = canvas.live_plot("servos", traces=["s1", "s2"])
```

Factory signatures follow one convention, worth knowing once: **panels you read
from take `name` first** (`slider`, `toggle`, `button`, `label`, `video`,
`audio`, `chat`, `live_plot`, `plot`, `repl`, `inspector` — the name is how
you'll reach them later), while **panels that render content take the content
first** (`image(src)`, `table(data)`, `markdown(text)`, `custom(html)`,
`react(source)`, `webview(url)`, `show(value)`) with `name=` as an optional
keyword that defaults to the type word (`"image"`, `"table"`, …). Either way the
`name` is the unique `canvas.<name>` handle, and an optional `label=` sets a
different on-screen caption. Factories also forward `insert`'s placement, lock,
and `queue` options, so a fully-specified panel fits on one line:

```python
servo = canvas.slider("servo", min=0, max=180, default=90, label="Servo 1", x=80, y=80)
```

The explicit two-step form is still available for when you want to **build a
panel now and insert it later** (or into a different canvas):

```python
s = pycanvas.Slider("servo_1", min=0, max=180, default=90)  # not on a canvas yet
canvas.insert(s, x=80, y=80)                                # place it when ready
```

Sliders take an optional `step` (default `1`). A fractional step makes it a
**float slider** and sets the precision of the manual number-entry box shown
beneath the track — type a value (clamped to `[min, max]`) instead of dragging:

```python
gain = canvas.slider("gain", min=0, max=1, default=0.5, step=0.1)  # float slider
```

Pass `on_release=True` so a *drag* reports only when the user lets go: the thumb
still tracks the cursor live, but `@on_change` fires once, with the settled
value, instead of streaming every intermediate value — handy for a slow handler.
The default (`False`) reports every change as you drag.

```python
gain = canvas.slider("gain", min=0, max=1, step=0.1, on_release=True)
```

## Components

| Component   | Direction      | API |
|-------------|----------------|-----|
| `Slider`    | bidirectional  | `.value`, `@on_change`, `.update(v)` |
| `Toggle`    | bidirectional  | `.value`, `@on_change`, `.update(opt)`; `options=[...]` |
| `Button`    | input          | momentary action; `@on_click`, `.value` (click count); `text=`, `.update(text)` to relabel live |
| `Label`     | output         | escaped plain text/number; `.update(text)`; fits its height with `h="auto"` |
| `VideoFeed` | output         | `.update(bgr_frame)` (OpenCV → binary JPEG over WS) |
| `AudioFeed` | output         | `.update(pcm_chunk)` (PCM → Web Audio playback) |
| `Plot`      | output         | `.update(fig_or_html)` (Plotly figure or HTML) |
| `LivePlot`  | output         | streaming telemetry; `.push({trace: y, ...})`, `.clear()`; optional `smoothing=` |
| `Histogram` | output         | a distribution over training (TensorBoard-style); `.add(values, step)` |
| `Custom`    | bidirectional  | arbitrary HTML in a sandboxed iframe; `@on(event)` / `@on_message`, `.push(data)` / `.push_binary(bytes)`, `.update(html)` |
| `React`     | bidirectional  | your own React component (JSX), rendered natively; `@on(event)` / `@on_message`, `.update(**props)`, `.push(data)` |
| `Markdown`  | output         | rendered Markdown text; `.update(text)` |
| `Image`     | output         | a static image (path/URL/bytes/Matplotlib/PIL/array); `.update(src)` |
| `Table`     | output         | interactive tabular data (DataFrame/Series, records, dict of columns, or a flat dict → key/value rows) — sort, filter, per-column distributions; `.update(data)` |
| `WebView`   | output         | an external website/URL in an iframe; `.navigate(url)` |
| `Chat`      | bidirectional  | shared chat across all viewers; editable names; `.post(text)`, `@on_message` |
| `FileBrowser` | bidirectional | navigate a folder (sandboxed to `root=`); `@on_select` (file path), `@on_navigate`, `.value`, `pattern=` |

### The three data verbs: `update` vs `push` vs `add`

Sending data to a panel uses one of three methods, and which one a panel takes
follows from *what kind of data it holds* — learn the rule once and you can
guess any panel's verb:

| Verb | Means | Replayed on reconnect? | Panels |
|------|-------|:----------------------:|--------|
| **`.update(value)`** | **replace** the panel's whole current state with a new value | ✅ yes — the latest value is the panel's state | `Label`, `Image`, `Table`, `Markdown`, `Plot`, `Slider`, `Toggle`, `Button` (`text`), `VideoFeed`, `AudioFeed` |
| **`.push(sample)`** | **append** one sample to a live, mounted stream, applied incrementally (no full re-render) | ❌ no — it's high-rate telemetry, not state | `LivePlot`, `Custom`, `React` |
| **`.add(values, step)`** | **record** one distribution snapshot at `step`, accumulating a history shown across steps | ✅ yes — the recorded rows are the panel's state | `Histogram` |

The distinction is **replace vs. append vs. record a snapshot**. `update` is
state (so a reconnecting browser gets the latest one); `push` is a firehose you
don't want replayed; `add` is the one special case — a histogram keeps every
snapshot you `add` and shows how the distribution shifts over training, so it
needs both the values *and* the `step` they belong to.

```python
label.update("ready")                          # replace: the label IS this text
plot.push({"train": loss}, x=step)             # append: one more point on a live curve
weights.add(layer.weight, step=epoch)          # record: one distribution row at this step
```

### Plot vs LivePlot

- **`Plot`** renders a full Plotly figure in an iframe — great for occasional,
  rich figures (re-rendered on each `.update`).
- **`LivePlot`** is for **high-frequency telemetry**. Plotly is loaded once with
  the app; `.push(sample)` streams just the data and applies it with
  `Plotly.react` on a chart that stays mounted (no iframe reload). Data bypasses
  the canvas store, so it's smooth even at 10+ Hz.

```python
plot = canvas.insert(pycanvas.LivePlot("servos", traces=["s1", "s2"], max_points=300))
# in your loop:
plot.push({"s1": servo_1.value, "s2": servo_2.value})
```

Traces don't have to be declared up front — `traces=` only fixes the legend
order, and pushing a key it hasn't seen adds that trace on the fly
(`plot.push({"s3": v})`). Pass `x=` to plot against a real step/epoch instead of
the auto-incrementing sample index. And `smoothing=` (an EMA weight in `[0, 1)`,
settable live as `plot.smoothing`) overlays a bold smoothed line on a faint raw
one — the TensorBoard scalar look:

```python
loss = canvas.live_plot("loss", smoothing=0.6)
loss.push({"train": train_loss, "val": val_loss}, x=step)
```

### Histograms — a distribution over training

`Histogram` is the streaming-distribution panel (TensorBoard's HISTOGRAMS tab):
call `.add(values, step)` whenever you want to record a distribution — a layer's
weights or gradients once per epoch — and it shows how that distribution shifts
across steps, as a density heatmap (value-bin vs. step) or `mode="overlay"`
lines. It reuses `Plot`'s Plotly path, so it needs `plotly` only when used.

```python
hist = canvas.histogram("weights/fc1", bins=40)
for epoch in range(epochs):
    hist.add(model.fc1.weight.detach().numpy(), step=epoch)
```

### Streaming performance: queue policy & pre-encoded frames

Two knobs keep pycanvas a thin, fast layer for high-rate feeds (cameras, live
telemetry) without piling up latency on a slow viewer.

**Queue policy** — how a component's updates behave when they outpace the
connection. Every component has a `queue` setting — pass `queue=` to any factory
or `insert(...)`, or set it later as a property:

- **`"fifo"`** (default) — deliver every update in order, nothing dropped. Right
  for controls, labels, anything where each value matters.
- **`"latest"`** — keep only the newest pending value per viewer, dropping stale
  ones. Right for live video/telemetry, where the current frame is all that
  matters. Dict updates merge newest-per-key (so partial `set_layout`s aren't
  lost); binary frames replace wholesale. Bounds the per-viewer backlog to one
  in-flight send + one pending value, so a fast producer can't lag a slow client.

```python
cam  = canvas.video("door")                      # VideoFeed defaults to queue="latest"
plot = canvas.live_plot("temps", queue="latest") # set it at creation...
plot.queue = "latest"                            # ...or any time later
```

**Pre-encoded frames** — `VideoFeed(encode=False)` skips `cv2.imencode` and sends
the bytes you give it as-is (they must already be **JPEG**). Use it to feed a
hardware encoder's output (e.g. a Jetson's NVJPG via GStreamer), keeping the CPU
out of the hot path:

```python
cam = canvas.video("door", encode=False)
cam.update(jpeg_bytes)              # already-encoded JPEG, sent straight through
```

### Custom HTML panels

`Custom` renders any HTML/CSS/JS string (or a file via `path=`) inside a
sandboxed iframe with a **symmetric** two-way channel injected as `canvas`:
`canvas.send(data)` posts back to Python, and `canvas.onPush(fn)` receives data
Python streams in — no `__pycanvas` unwrapping or message-guard boilerplate.

On the Python side, route inbound messages by an `event` field with
`@panel.on("event")` — no subclassing, no hand-written dispatcher. Use
`@panel.on_message` for a catch-all that sees everything:

```python
panel = canvas.custom(html='''
  <button onclick="canvas.send({event: 'go'})">go</button>
  <script>canvas.onPush((msg) => document.body.append(msg))</script>
''')

@panel.on("go")            # fires only for {event: 'go'}
def handle(msg):
    panel.push("clicked")  # -> canvas.onPush in the iframe
```

(`event_key=` changes the field used for routing if your HTML tags messages
differently, e.g. `type`.)

The HTML, CSS and JS can also be supplied as **separate strings** — handy when
pasting a widget from a snippet site like [uiverse.io](https://uiverse.io),
which hands you markup and a stylesheet side by side. They're composed into one
document under the hood:

```python
panel = canvas.custom(html=markup, css=styles, js=behaviour)
panel.update(css=new_styles)        # restyle without touching the markup
```

`panel.update(html)` swaps the whole HTML (reloads the iframe). To stream live
data **without** reloading — keeping the iframe's focus, listeners and scroll —
use `panel.push(data)`, received via `canvas.onPush(fn)`. That's what powers
[`examples/remote_control.py`](examples/remote_control.py), which streams the
host's screen into one panel and replays the browser's mouse/keyboard back onto
the machine (a tiny LAN remote desktop — read its security note first).

For **frame- or array-grade** telemetry — where per-sample JSON/base64 cost would
dominate — `panel.push_binary(bytes)` streams raw bytes on a **binary** WebSocket
frame instead, the same fast path `VideoFeed`/`AudioFeed` use (no JSON serialize,
no base64). The *same* `canvas.onPush(fn)` receives it, but as an `ArrayBuffer`,
so branch on the type to tell the two streams apart — and it honours `queue=`, so
`queue="latest"` drops stale buffers for a slow viewer just as it does for video:

```python
cam = canvas.custom(html='''
  <canvas id="v"></canvas>
  <script>
    canvas.onPush(d => d instanceof ArrayBuffer ? drawFrame(d) : handleControl(d));
  </script>''', queue="latest")
cam.push_binary(jpeg_bytes)          # raw bytes, straight onto a binary frame
```

Because it's just HTML in an iframe, anything that renders to HTML works:

- **matplotlib** — `fig.savefig(buf, 'png')` → base64 `<img>` → `panel.update(html)`
- **Plotly** — `fig.to_html(include_plotlyjs='cdn')` → `panel.update(html)`; the
  chart stays fully interactive (zoom / pan / hover) inside the sandbox.

### Packaging a reusable widget (subclass `Custom`)

For most widgets you don't need a subclass at all — `canvas.custom(html=...)`
plus `@panel.on("event")` is enough (see the `Dial` in
[`examples/custom_component.py`](examples/custom_component.py), built with no
subclass). Subclass `Custom` only when you want to **package** the HTML behind a
typed constructor, or to override `state_payload` so every connecting client
(including late-joiners and reconnects) is seeded with the current state on load
— event routing is already built in:

```python
class Dial(pycanvas.Custom):
    def __init__(self, name="dial", **place):
        super().__init__(html=DIAL_HTML, name=name, w=220, h=260, **place)
        self._angle = 0

    # Called automatically for every connecting client — no "ready" handshake needed.
    def state_payload(self):
        return self._angle          # pushed straight into the iframe via canvas.onPush

    def set_angle(self, deg):
        self._angle = deg
        self.push(deg)             # push to all currently connected clients

dial = canvas.insert(Dial("my_dial"), x=80, y=80)

@dial.on("rotate")                 # routing is inherited from Custom
def _(msg): print("rotated to", msg["deg"])
```

`state_payload` is the key hook: the bridge calls it right after registering the
panel with each new WebSocket client, so the iframe is always seeded with the
current value on load — no polling or `{type: "ready"}` retry needed.

### React panels

`React` is the native counterpart to `Custom`: instead of sandboxed HTML in an
iframe, you ship JSX *source* from Python and it's compiled in the browser
(Babel, lazily loaded — no `npm` build) and mounted as a real React subtree
inside the panel, inheriting the canvas theme and talking to Python with no
postMessage hop. Pass a full component as `source=` (it must define
`function Component({ canvas, value, props })`), or just markup + CSS and let
the wrapper be added for you:

```python
counter = canvas.react(jsx='<button onClick={() => canvas.send({n: 1})}>tap</button>',
                       css='button { font-size: 18px; }')
```

Because it's a real subtree (not a cover-it-to-grab iframe), a React panel stays
interactive from the first hover — cursors, `:hover` and controls respond with no
click-to-arm step — and a small grip fades in on the panel's top-right to drag or
select it without stealing pointers from the content.

`push(data)` reaches the component as the `value` prop (a re-render). For
high-rate streams, subscribe imperatively with `canvas.onFrame(cb)` inside a
`useEffect` and paint each frame yourself (to a `<canvas>`/`<img>`) — that skips
the per-frame re-render the `value` prop would trigger. Use one channel or the
other, not both. For frame- or array-grade telemetry, `push_binary(bytes)` sends
packed bytes on a binary WebSocket frame (no JSON, no base64 — the same fast path
`VideoFeed`/`Custom.push_binary` use); `onFrame` receives it as a zero-copy
`ArrayBuffer` to wrap in a typed array (e.g. `new Float32Array(buf)`). See
[`examples/react_component.py`](examples/react_component.py) for a binary
`onFrame` waveform.

Reach for third-party libraries with `scope=[...]`: each name is loaded as ESM
from a CDN in the browser (nothing is bundled, so listing none costs nothing) and
handed to the component as the `libs` global. Friendly names (`d3`, `lodash`,
`date-fns`, `framer-motion`/`motion`, `lucide`/`lucide-react`) map to pinned,
React-externalised builds so hook-based libs share the app's React; any other
name passes through to esm.sh.

```python
panel = canvas.react('''
  function Component() {
    const x = libs.d3.scaleLinear().domain([0, 100]).range([0, 200])
    return <div>d3 maps 50 → {x(50)}</div>
  }
''', scope=["d3"])
```

uiverse.io exports its React widgets with `styled-components`, which needs an
npm build — `React.from_uiverse(raw)` rewrites such a snippet into plain
React + CSS that the in-browser pipeline accepts:

```python
panel = canvas.react(source=pycanvas.React.from_uiverse(raw_snippet))
```

See [`examples/react_styled_component.py`](examples/react_styled_component.py)
and [`examples/custom_styled_component.py`](examples/custom_styled_component.py)
for both flavours of pasted-widget panel.

### Web pages (WebView)

`WebView` embeds a live website by URL in its own iframe — handy for dashboards,
docs, maps, or videos alongside your panels:

```python
web = canvas.webview("https://en.wikipedia.org/wiki/Robot")
web.navigate("https://example.com")   # point it elsewhere, live
```

Unlike `Custom` (which sandboxes app-authored HTML away from its origin),
`WebView` loads a real third-party site with `allow-same-origin`, so interactive
embeds that need their own origin to run (YouTube's player, maps, web apps) work
instead of rendering blank. YouTube `watch?v=`/`youtu.be` links are rewritten to
their embeddable `/embed/` form automatically.

Embedding only works for sites that permit being framed. Pages that send
`X-Frame-Options: DENY` or a CSP `frame-ancestors` rule (Google, X, GitHub, most
banks) refuse to load — that's a browser security rule, not a PyCanvas limit.

### Audio

`AudioFeed` streams PCM audio to the browser, played back-to-back through the
Web Audio API — the audio analogue of `VideoFeed`. Capture however you like
(e.g. `sounddevice`) and push chunks:

```python
mic = canvas.audio("mic", sample_rate=16000)
mic.update(chunk)   # NumPy float32/int16 or raw int16 bytes
```

Mic capture needs the optional extra (`pip install -e ".[audio]"`); playback
needs nothing. Browsers block autoplay, so each viewer clicks **Enable audio** on
the panel once. See [`examples/webcam_feed.py`](examples/webcam_feed.py) for video
+ audio together.

### Chat & viewers

`Chat` is a shared room for everyone viewing the canvas — the server relays each
line stamped with the sender's identity, and every viewer edits their own
display name in the panel. Python can join in too:

```python
chat = canvas.chat("chat")
chat.post("welcome 👋")        # post as the host
@chat.on_message
def log(entry): print(entry["name"], entry["text"])
```

A small badge at the top of the canvas shows the live viewer count. See
[`examples/chat_room.py`](examples/chat_room.py).

### Live viewer cursors

With `serve(cursors=True)` every viewer reports their pointer, so viewers see
each other's live cursors (each in their roster colour) and **Python can read
every pointer** off the roster as `canvas.viewers[i]["cursor"]` — a
`{"x", "y"}` in canvas coords, or `None` until they move it:

```python
tip = canvas.viewers[0]["cursor"]            # {"x": ..., "y": ...} or None
```

It's the *latest* position (so a loop can sample it at its own rate), and there's
a streaming form too — `@canvas.on_cursor def _(viewer): ...` fires on each move
with the viewer dict. Positions are throttled and dead-banded client-side, then
conflated per viewer, so a moving mouse can't flood the socket. Because it's
viewer telemetry (the host sees every pointer), it's gated like the Inspector:
**default on only for a private local bind**, `cursors=True`/`False` to override.
See [`examples/moving_widget.py`](examples/moving_widget.py) — a unique emoji
figure-8s around each viewer's cursor.

### Inspector from the toolbar

You don't have to add an `Inspector` in code to peek at the canvas. A toolbar
button (bottom-left of the canvas) spawns an ephemeral `Inspector` panel on
demand — click it to drop one in, click again to remove it. From there you can
browse every panel's live name/type/value/geometry, switch its header dropdown
to the kernel **globals** view, and click any row to drill into an object's
fields. It's the same `Inspector` component, just summoned from the UI instead
of `canvas.inspector(...)`.

The panel's footer shows a live **view readout** — `view: x=… y=… zoom=…`,
tracking the camera as you pan and zoom. The numbers are exactly what
`serve(view=...)` / `set_view()` take, so navigate to a framing you like and
copy them to pin it as a fixed view.

Because that panel can surface your component state (and, in globals mode, your
kernel variables) to **everyone** connected, the button is offered only on a
local bind (`127.0.0.1`) by default. On a LAN or tunneled canvas it's hidden
unless you opt in:

```python
canvas.serve(host="0.0.0.0", ui_inspector=True)   # offer it to LAN viewers too
canvas.serve(ui_inspector=False)                   # hide it even locally
```

## Viewport & navigation

Control how the tldraw canvas is framed and navigated, so the same board can be
a free creative workspace or a fixed, chrome-free UI. Pass a `view` dict to
`serve` with any of these keys (all optional):

| Key | Effect |
|-----|--------|
| `x`, `y`, `zoom` | initial camera: centre on canvas point `(x, y)` at `zoom` (1.0 = 100%) |
| `locked` | `True` freezes pan and zoom entirely (a fixed kiosk view) |
| `min_zoom`, `max_zoom` | clamp how far viewers can zoom |
| `ui` | `False` hides tldraw's toolbars/menus **and** the Inspector button |
| `grid` | `True` shows the background grid |
| `read_only` | `True` blocks freehand drawing |

```python
canvas.serve(view={"x": 200, "y": 160, "zoom": 1.0, "locked": True, "ui": False})
```

Change any of it **live** on every connected browser with `set_view` — same
options, given as a dict and/or keywords. Only the keys you pass change; passing
`x`/`y`/`zoom` re-centres the camera now (subject to any lock), omitting them
leaves each viewer where they were looking. Late joiners get the merged config:

```python
canvas.set_view(ui=False)          # hide the chrome now
canvas.set_view({"zoom": 2.0})     # zoom everyone to 200%
canvas.set_view(locked=True)       # freeze pan/zoom live
```

**Per-viewer views.** Pass `client_id` to change the view for **one** viewer
instead of broadcasting to everyone — e.g. a button that jumps just the person
who clicked it to a different region, leaving the others where they are. The id
comes from `canvas.viewers`, which lists everyone connected right now as
`{"id", "name", "color"}` dicts:

```python
for v in canvas.viewers:           # who's connected right now
    print(v["id"], v["name"])

canvas.set_view(x=0, y=0, zoom=1.5, client_id=some_id)   # move just that viewer
```

Omitting `client_id` keeps the default global behaviour. A per-viewer override
is dropped automatically when that viewer disconnects, and new joiners get the
global config (never someone else's per-viewer state).

See [`examples/fixed_view.py`](examples/fixed_view.py).

## Layout: position, size, rotation

Pass placement to `insert`, or change it live at any time. `x`/`y` are canvas
coordinates, `w`/`h` are pixels, `rotation` is in degrees (clockwise). `width`
and `height` are accepted as aliases for `w`/`h` everywhere a panel takes a
size, so you can use the same spelling as the `column(width=…)` / `row(height=…)`
containers (pass one spelling per axis, not both). Omit
`x`/`y` and the panel is **auto-arranged** — unpositioned panels flow
left-to-right, top-to-bottom, packed by their real size with a small gap so they
never overlap (uniform panels read as a tidy grid; mixed sizes pack like
masonry). Omit `w`/`h` to use the component's default size.

```python
servo = canvas.insert(
    pycanvas.Slider("servo_1", min=0, max=180, default=90),
    x=80, y=80, w=320, h=110, rotation=0, name="servo",
)

# Read or change layout live — each write is pushed to the browser immediately:
servo.x = 300                       # move (x/y are None until first placed)
servo.w += 50                       # resize
servo.rotation += 15                # rotate
servo.move(400, 200)                # set x and y together
servo.resize(w=500, h=160)
servo.set_layout(x=120, y=90, rotation=30)   # any combination in one message
```

### Auto height (`h="auto"`)

Text-y panels are hard to size by eye. On the Custom-based panels (`markdown`,
`custom`, `table`, `image`, `label`, …) pass `h="auto"` and the panel's height fits its
rendered content — measured in the browser after layout, and re-fitted when
the content reflows (e.g. you narrow the panel, or `update()` changes the
text). Width stays yours; the fitted height is reported back so `comp.h` stays
in sync:

```python
notes = canvas.markdown("# Heading\n\nas tall as this text, no taller", h="auto")
```

It's also a **live property**, so you can flip a panel into (or out of)
auto-height any time after it's placed — the same as the insert-time form:

```python
notes.h = "auto"     # start fitting the height to the content now
notes.h = 240        # back to a fixed 240px (assigning a number turns auto off)
```

Auto-height is a Custom-panel feature: setting `h="auto"` on a non-Custom panel
(a `Slider`, a `Toggle`, …) warns and leaves its height unchanged, rather than
silently shipping the string to the frontend. (Use `comp.h = "auto"` — the live
property — not `comp.update(h="auto")`; `update()` carries a panel's *value*,
not its layout.)

The fit lands right after the panel first renders (until then it uses the
default height), so a panel placed `below=` an auto-height anchor is positioned
using the anchor's height *at insert time* — give the anchor an explicit `h`
when the gap below it must be exact.

### Relative placement

Instead of computing absolute coordinates, anchor a panel to one already placed
with `below=` / `above=` / `right_of=` / `left_of=` (a component or its name),
spaced by `gap` pixels (default 16). A vertical anchor aligns left edges; a
horizontal one aligns top edges. Combine two to set each axis independently, and
an explicit `x`/`y` overrides the derived coordinate:

```python
plot     = canvas.plot("plot", x=400, y=40, w=600, h=400)
controls = canvas.slider("t", min=0, max=1, step=0.01, below=plot)      # under it
legend   = canvas.markdown("…", right_of=plot, gap=24)                  # beside it
button   = canvas.button("go", below=controls, right_of=plot)           # grid corner
```

The anchor must already have a position — given `x`/`y`, placed relatively
itself, or dragged by a user. (Auto-arranged panels — those inserted without
`x`/`y` — have no Python-side position until a browser reports one.)

### Auto-layout (`grid` / `column` / `row`)

For a dashboard built from many panels, don't track coordinates by hand. Open a
`with canvas.grid(...)` (or `column` / `row`) block and any panel you insert
inside it — without an explicit `x`/`y` or relative anchor — drops into the next
cell, taking the slot size unless you pass your own `w`/`h`:

```python
with canvas.grid(cols=2, slot=(560, 300), gap=24, origin=(40, 40)):
    canvas.live_plot("loss")
    canvas.live_plot("accuracy")    # next column
    canvas.image(fig)               # wraps to the next row
    canvas.markdown(notes, h="auto")  # h='auto' is preserved, not forced to slot
```

`grid(cols=n)` lays uniform `slot=(width, height)` cells out `cols` per row.
`column(width=…)` and `row(height=…)` (the cross-axis size also accepts the
`w`/`h` spelling) flow along one axis and let each panel keep
its **natural size** on the other — so a strip of mixed controls (a label, a few
buttons, a slider) isn't squashed to one height:

```python
with canvas.column(width=320, gap=12, origin=(40, 40)):
    canvas.label("status", "ready")
    canvas.button("start")            # each keeps its own height
    canvas.slider("learning rate", min=0, max=1, step=0.01)
```

`gap` is the spacing and `origin` the top-left corner. An explicit position or a
`below=`/`right_of=` anchor still wins for a given panel, and blocks can be
sequenced or nested to place columns of charts beside columns of media.

Every component has a unique **`name`** — its first constructor argument (pass
`name=` to `insert` to override). That `name` is the component's identity: the
`canvas.<name>` / `canvas["<name>"]` handle, and the key that makes a later
insert under the same name replace the old panel. The `label` is purely the
on-screen caption and is optional — it defaults to the `name`:

```python
canvas.servo.rotation = 45          # same object as the `servo` variable
canvas["servo"].update(120)         # canvas["..."] also works for non-identifier names
```

> Layout values reflect both what Python last set **and** the user's drags,
> resizes and rotations in the browser — those are reported back, so `x`/`y`/
> `w`/`h`/`rotation` stay in sync (register `@panel.on_layout` to react to them).
> A panel's `x`/`y` are `None` only until it's first placed — by Python or a drag.

`canvas.components` is the list of every panel on the canvas, for iterating or
applying something to all of them at once (the arrows are `canvas.arrows`):

```python
for c in canvas.components:         # nudge everything 20px right
    if c.x is not None:
        c.x += 20
```

## Arrows

`connect` draws an arrow between two panels; it binds to them and reroutes as they
move or resize.

```python
a = canvas.connect(servo, status, text="x2", color="blue")  # caption "x2"
a.text = "x3"                       # change the caption live (identity unchanged)
a.update(dash="dashed", bend=40)    # color/dash/size/bend/arrowhead_* ...
canvas.disconnect(a)                # or canvas.disconnect("<name>")
```

Like components, an arrow's identity is its **`name`** (the `canvas.<name>` handle
and eviction key) — but unlike components arrows take **no `label`**; their caption
is **`text`** (nothing is drawn if you omit it). If you don't pass `name=`, it
defaults to `"<start.name>-><end.name>"`, so re-connecting the same two panels
replaces the old arrow instead of stacking a duplicate.

## Locking & interactivity

Five **independent** controls gate how a panel responds to the user. Set any of
them on `insert` (or a factory), or flip them live as a property — each write is
pushed to the browser immediately. Because they're separate axes you can mix them
freely (e.g. pin a panel in place while keeping its slider live).

| Control             | User can move? | User can resize? | Controls operable? | Python `update()` renders? |
|---------------------|----------------|------------------|-----------------------|----------------------------|
| *(default)*         | yes            | yes              | yes                   | yes                        |
| `draggable=False`   | **no**         | yes              | yes                   | yes                        |
| `resizable=False`   | yes            | **no**           | yes                   | yes                        |
| `operable=False`    | yes            | yes              | **no**                | yes                        |
| `grabbable=False`    | **no** (Python only) | **no**     | yes, **immediately**  | yes                        |
| `locked=True`       | **no**         | **no**           | **no**                | **no** (frozen)            |

`grabbable` mostly matters on content-heavy panels (`Custom`, `React`,
`WebView`, plots, chat, repl…). By default those need a first click to *select*
the panel before their content takes the pointer — which also means CSS
`:hover` effects inside the widget don't run until that click.
`grabbable=False` drops that cover **and** makes the panel invisible to
selection entirely: the widget is hover- and click-live from the start, and no
click, marquee, or select-all ever highlights or selects the panel. The
trade-off is that the user can't move or resize it at all — do that from
Python (`move()` / `resize()`), or flip `grabbable` back on.

```python
servo = canvas.slider("servo_1", min=0, max=180, default=90)

servo.draggable = False      # user can't drag the panel; the slider still works
servo.resizable = False      # user can't resize it; the slider still works
servo.operable = False       # user can't operate the slider, but your update()s
                             #   still move the thumb — and the panel stays
                             #   draggable/resizable (those axes are unaffected)
servo.locked = True          # full lock: no move, resize, or interaction — AND
                             #   programmatic update()s stop rendering too
```

Two helpers wrap the common combinations:

```python
servo.pin();  servo.unpin()     # draggable=False + resizable=False (controls stay live)
servo.lock(); servo.unlock()    # full lock on / off
```

#### Stacking order

Where panels overlap, control which sits on top — live, from Python:

```python
panel.to_front()    # above every other panel
panel.to_back()     # beneath every other panel
panel.forward()     # one step up
panel.backward()    # one step down
```

`to_front()` / `to_back()` persist across a reload (a reconnecting client
rebuilds the panel in the new order); `forward()` / `backward()` are a single
overlap-aware nudge and apply to the live canvas only.

The key distinction is **`operable` vs `locked`**: `operable=False` blocks
the *user* from operating the control while your code keeps driving it — a slider
whose thumb tracks an automatic value the user mustn't drag. `lock()` freezes
everything *including* your own `update()` calls, so the thumb would stop moving.
See [`examples/robot_control.py`](examples/robot_control.py) — vision mode makes
the servo sliders inert (`operable=False`) while they sweep on their own.

### Frameless panels

`frame=False` strips a panel's card chrome entirely — background, border,
shadow, padding, the label header, and the hover-highlight outline — so the
component's content appears to sit directly on the canvas:

```python
canvas.insert(widget, x=40, y=40, frame=False)   # or widget.frame = False, live
```

The panel still occupies its `w×h` box and behaves normally otherwise:
selecting it shows the usual selection box and resize handles (handy for
placing it), it just isn't outlined on hover. Frameless `Custom`/`WebView`
iframes and `VideoFeed` letterboxes turn transparent too, so user HTML with a
transparent body (the `css=`/`js=` compose path sets one) floats free. Pair it
with `grabbable=False` for a true free-floating widget — live on hover and
completely untouchable by the user:

```python
canvas.custom(name="gauge", html=..., frame=False, grabbable=False)
```

Add `operable=False` for a panel that's *click-through* too. A `Custom` panel
that's neither selectable (`grabbable=False`) nor operable (`operable=False`) is
treated as purely decorative: its iframe takes no pointer, so clicks fall
through to whatever sits underneath it on the canvas — perfect for a cursor-
following overlay that must never swallow a click (`examples/moving_widget.py`):

```python
canvas.custom(name="orb", html=..., frame=False, grabbable=False, operable=False)
```

## Saving & loading

Persist the whole board — the panel formation **and** the user's freehand
drawings — to one JSON file, then bring it back:

```python
canvas.save("board.json")                    # browser must be open to capture drawings
# next run, recreate the panels in code first (same names), then:
canvas.load("board.json")                    # snaps panels into place + restores drawings
canvas.load("board.json", formation=False)   # drawings only; leave panels where code put them
```

Panels are Python objects, so only their **placement** is saved, never their
behaviour — recreate them in code and `load()` repositions them and merges the
saved drawings on top. See the [GUIDE](GUIDE.md) for details.

## Packaging a desktop app (`bake`)

Ship a canvas as a self-contained executable — no Python, browser, or `pip`
needed on the target machine. `canvas.bake()` bundles your script, the PyCanvas
backend, and the pre-built frontend into one app (via PyInstaller) that runs the
canvas in a **native window** (via pywebview), serving locally just as in dev.

The same file is both source and app. Put `bake()` where you'd call `serve()`:

```python
canvas = pycanvas.Canvas()
# ...build panels...
canvas.bake(name="RobotConsole")     # window_size, icon=, onefile=, distpath= ...
```

- `python your_script.py` → **builds** `dist/RobotConsole(.exe)`.
- launching that executable → **runs** your script in a window (inside the build
  `sys.frozen` is set, so `bake()` skips rebuilding and just shows the canvas).

To build **without editing your script**, use the CLI — it packages the file
without running it, and your existing `serve()` automatically switches to a
native window when frozen:

```bash
python -m pycanvas.bake your_script.py --name RobotConsole
python -m pycanvas.bake your_script.py --onedir --icon app.ico   # folder build, custom icon
```

Building needs the desktop extra (`pip install -e ".[desktop]"`, which pulls
`pywebview` + `pyinstaller`). On Windows the window uses the Edge **WebView2**
runtime (present on current Windows). `serve(desktop=True)` opens the same native
window in development; if pywebview isn't installed it falls back to the browser.
See [`examples/bake_app.py`](examples/bake_app.py).

**What gets bundled.** Only the packages your script actually imports are
included — *not* your whole environment — plus what PyInstaller's hooks add. So
you normally specify nothing. Heavy optional deps are bundled only when the
canvas uses the component that needs them — **numpy** for an `AudioFeed`,
**OpenCV** for a `VideoFeed`, **Pillow** for an `Image` — so a slider-only app
doesn't drag them in. The public tunnel (`pycloudflared`), IPython, ipywidgets
and PyInstaller itself are excluded by default: a standalone local app needs
none of them, and any one would otherwise pull in a large unrelated tree (tqdm →
pandas/scipy/torch, the Jupyter stack, Pillow → numpy). Two escape hatches when
analysis gets it wrong:

```python
canvas.bake(name="App", include=["my_plugin"])   # force-add a dynamic/plugin import
canvas.bake(name="App", exclude=["torch"])        # skip a broken/unused dep that crashes the build
```

When numpy is bundled on a **conda** environment, the MKL DLLs it needs are
detected and bundled automatically (a pip/venv NumPy bundles its own BLAS, so it
needs nothing). If a
build fails, the error names the culprit dependency and the `exclude` fix. The
same options exist on the CLI: `--include`, `--exclude` (both repeatable).

## Show anything

Don't want to pick a component? `canvas.show(value)` inspects the value and
inserts the panel that best renders it — the same way a notebook decides how to
display an `Out[...]`, but it works in plain scripts too (no IPython needed):

```python
canvas.show(df)                    # pandas DataFrame -> interactive Table
canvas.show(fig)                   # Matplotlib / Plotly figure -> Image / Plot
canvas.show("use **bold** here")   # Markdown syntax -> rendered text
canvas.show({"status": "ok"})      # dict / list -> pretty JSON
canvas.show(model)                 # anything with _repr_html_/_repr_png_ -> its rich view
```

`show()` looks at *what's inside* a value, not just its type — strings, paths,
URLs and bytes are inspected rather than dumped verbatim:

```python
canvas.show("report.csv")          # existing file -> interactive Table
canvas.show("photo.png")           # existing image file -> Image
canvas.show("notes.md")            # .md / .json / .html files render by type
canvas.show("https://site.com/x.png")  # image URL / data: URI -> Image
canvas.show("https://example.com") # bare web URL -> a clickable link
canvas.show("<h1>Hi</h1>")         # literal HTML -> rendered HTML
canvas.show(png_bytes)             # image bytes -> Image
canvas.show(Path("chart.png"))     # pathlib.Path works anywhere a path does
```

Dispatch order (most specific first): an existing component passes through, then
Plotly → image-like (Matplotlib/PIL/NumPy) → tabular (DataFrame/records) → rich
`_repr_*` → dict/list (JSON) → image bytes → string → scalar `repr`. Strings are
further inspected for a file path, an image/web URL, literal HTML, or Markdown
syntax (even a short one-liner like `**bold**`); a plain one-liner stays a bold
`Label`. Detection is deliberately conservative — single `*italic*` isn't treated
as Markdown, and a path is only special when it's a real existing file — so
ordinary text isn't misread. With no `name` each call gets a fresh panel; pass
`name=` to replace one in place. The same dispatcher is available standalone as
`pycanvas.panel_for(value)` (builds without inserting), and it's what powers the
notebook cell-capture below.

The three render targets are also components in their own right when you want one
explicitly: `canvas.markdown(text)`, `canvas.image(src)`, `canvas.table(data)`.

**Matplotlib figures don't leak.** Rendering a figure (via `canvas.image(fig)`,
`img.update(fig)`, or `show(fig)`) releases it from pyplot's global registry
after rasterizing — so redrawing a fresh figure on every slider tick or loop
iteration needs no manual `plt.close()`. The figure object itself stays usable.

**The `Table` is interactive.** A DataFrame, CSV, or list of records renders a
panel you can **sort** (click a header — numeric columns sort numerically),
**filter** (a search box hides non-matching rows), and inspect: a *distributions*
toggle reveals a per-column mini-chart — a histogram for numeric columns, a
top-values bar chart for categorical ones. It's all client-side inside the
sandboxed panel, so it needs no extra dependencies and `update(data)` re-renders.

## Tracking an ML training run

There's no logging framework to learn — the panels above already are the
dashboard. Make each one once, keep the handle, and push to it from your loop:

```python
import pycanvas

canvas = pycanvas.Canvas()

loss    = canvas.live_plot("loss", traces=["train", "val"], smoothing=0.6)
weights = canvas.histogram("weights", bins=40)        # distribution over time
canvas.table({"lr": 3e-4, "batch": 64, "optimizer": "adam"})  # hparams -> table

@canvas.background
def train():
    for step in range(steps):
        loss.push({"train": train_loss, "val": val_loss}, x=step)
        if step % 50 == 0:
            weights.add(model.fc1.weight, step=step)          # a histogram row
            canvas.show(make_grid(batch), name="samples")     # latest predictions

canvas.serve()
```

`live_plot` overlays related series on one chart (push any trace key, declared or
not), and `smoothing=` adds the TensorBoard smoothed-over-raw line. `histogram`
shows a distribution shifting across steps. A flat dict renders as a key/value
**table** — the natural home for hyperparameters — and `canvas.show(value)` drops
any figure/array/DataFrame onto the board. Because PyCanvas is bidirectional, the
same loop can read *controls* TensorBoard can't offer — a pause button, a live
learning-rate slider — and `canvas.grid` / `column` / `row` arrange the panels
without hand-placing each. `live_plot`/`histogram` need `plotly`; rendering a
Matplotlib figure needs `matplotlib`.

See [`training_dashboard/train_dashboard.py`](training_dashboard/train_dashboard.py)
for the full board — scalar curves, a weight histogram, a sample-image grid, a
run log, and pause/reset/learning-rate controls.

## Hot reloading (auto-restart on save)

While iterating on a script, pass `hot_reload=True` to `serve()` so PyCanvas
restarts the process whenever you save a `.py` file in the script's folder —
change a `default=`, move a panel, flip `ui=False`, and it takes effect on save
without re-running the command by hand:

```python
canvas.serve(port=8000, hot_reload=True)   # run as `python your_script.py`
```

The browser tab reconnects to the restarted server on its own — no new tab opens
and you don't refresh. It watches by polling file mtimes (no extra dependency).
It's only for the script dev loop: it needs `block=True` (the default) and a real
script entry point, so it raises if combined with `block=False` or run from a
notebook/REPL. Stop it with `Ctrl+C`.

**A broken save won't take the canvas down.** Before restarting, each edit is
pre-flighted — the script is run far enough to confirm it imports and its body
executes — and only a clean run triggers the swap. If the save has an error (a
syntax slip mid-edit, a typo'd name, a bad import) the restart is skipped, the
error is printed, and the **last working version keeps serving**. Fix the file
and save again to pick up from there.

### Background workers (`canvas.background`)

Got a producer loop — a camera capture, a sensor poll, a telemetry stream that
calls `feed.update(...)`? Register it with `canvas.background` instead of starting
a thread yourself. `serve()` runs each registered callable on its own daemon
thread just before it begins serving:

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

**Rule of thumb: wrap every long-running thread in `@canvas.background`** — and
it's *required*, not just tidy, once `hot_reload=True` is in play. Hot reload
makes the original process a file-watching **monitor** that respawns a worker on
every save, and it runs your whole script body first. A thread you start by hand
(`threading.Thread(...).start()` at module scope) therefore runs in *both* the
monitor and the worker — so if it grabs a single-owner resource (a camera, a
serial port, the microphone) the idle monitor holds it and the real worker can
never acquire it. `canvas.background` defers the thread to the serving process
only, so it's started in the worker and never in the monitor.

It's a good habit even without hot reload: the thread starts at `serve()` rather
than at import, so importing your script as a module (tests, `bake`, a notebook)
doesn't kick off the loop. You can use it as a decorator (above) or call it
directly — `canvas.background(stream)` — and any extra `*args`/`**kwargs` are
forwarded when the thread starts. It's for *your* application loops; pycanvas's
own internals (input dispatch, tunnel, reaper) are already managed.

## Interactive use (Jupyter / notebooks)

`serve()` blocks, which is fine for scripts. In a notebook pass `block=False`
instead: it starts the server in a thread and returns, so later cells can keep
adding, moving, and removing panels on the **already-open** canvas.

```python
import pycanvas
canvas = pycanvas.Canvas().serve(port=8000, block=False)   # returns immediately

# ...any later cell — appears live on the open page...
servo = canvas.slider("servo_1", min=0, max=180, default=90)

canvas.remove(servo)   # pull a panel off the canvas
canvas.stop()          # shut the background server down
```

See [`examples/notebook_dynamic.ipynb`](examples/notebook_dynamic.ipynb) for a
full walkthrough.

### Mirror every cell's output automatically

Don't want to wrap each cell's output in a component by hand? Call
`canvas.capture_cells()` (alias `pycanvas.autopanel(canvas)`) once: it registers
an IPython `post_run_cell` hook so **every cell ending in an expression** gets
its own panel, auto-arranged in a grid. Each output is rendered by the same
`show()` dispatcher above (handed the kernel's display formatter), so DataFrames,
matplotlib/Plotly figures, and any `_repr_html_` object look as they do inline. Re-running a cell swaps its panel
in place — and if you'd moved, resized, or rotated that panel in the browser,
the refreshed panel keeps the geometry you left it at instead of snapping back
to the grid. Cells ending in a statement (assignment, `print`, loop) produce no
value and are skipped.

```python
import pycanvas
canvas = pycanvas.Canvas().serve(port=8000, block=False)
canvas.capture_cells(cols=2)   # every later cell now mirrors its output

pd.DataFrame({"x": range(5)})  # -> appears as its own panel, no insert needed

canvas.stop_capturing_cells()  # stop (existing panels stay)
```

**Customising an individual cell.** Auto-placement is just the default — any
cell can override its own panel with a `# pycanvas:` directive line, while
everything you don't specify still falls back to the grid (or to wherever you'd
dragged the panel on a re-run):

```python
# pycanvas: x=40 y=80 w=600 h=400 movable=false
fig                       # this panel is pinned at (40, 80), 600×400, undraggable

# pycanvas: name=metrics label="Live metrics" locked=true
df                        # named (canvas["metrics"]), captioned, fully locked

# pycanvas: skip
secret_value              # not mirrored to the canvas at all
```

Recognised keys: `x y w h rotation` (numbers), `locked movable resizable
interactive` (true/false), `name`/`label` (strings), and the bare tokens `skip`
/ `show`. A directive field is authoritative — e.g. a pinned `x`/`y` snaps back
to the coded position on every re-run — so omit the fields you'd rather leave to
the grid or to the user's own dragging.

**Defaults for every panel.** Anything you'd otherwise repeat per cell can be
set once on the `capture_cells(...)` call — panel size (`slot_w`/`slot_h`), grid
shape (`cols`, `gap`, `origin`), captions (`include_source`), and the default
lock state (`movable`, `resizable`, `locked`, `interactive`). A per-cell
directive still overrides these:

```python
# pin and shrink every panel; cells can still opt back in individually
canvas.capture_cells(slot_w=380, slot_h=260, movable=False, resizable=False)

# pycanvas: movable=true
fig          # this one stays draggable despite the capture-level default
```

**Opt-in instead of opt-out.** By default every expression cell appears (use
`skip` to exclude one). Pass `auto=False` to flip it into an allowlist — then
*nothing* is mirrored unless a cell carries a `# pycanvas:` directive:

```python
canvas.capture_cells(auto=False)   # mirror only cells I explicitly mark

2 + 2                  # no directive -> stays off the canvas

# pycanvas: show
df                     # marked -> appears, using the default grid placement

# pycanvas: x=40 y=80  # any placement directive also opts the cell in
fig
```

See [`examples/notebook_autopanel.ipynb`](examples/notebook_autopanel.ipynb).

> The background server runs in a daemon thread, so `block=False` only stays up
> while the process does. A notebook kernel keeps living, but a plain **script**
> that ends right after `serve(block=False)` would exit and tear the server down
> — call `canvas.wait()` at the end to park the main thread until `Ctrl+C` (handy
> when you serve in the background and then start your own worker threads).

> Note: a `Canvas` is single-process — one Python process owns the port and all
> components. Two separate scripts can't add to the same canvas/port, but you can
> composite several separate canvases onto one view — see [Merging canvases](#merging-canvases).

## Sharing on your network

By default the server binds to `127.0.0.1` (this machine only). To let other
devices on the same network open and interact with the canvas, bind to all
interfaces:

```python
canvas.serve(port=8000, host="0.0.0.0")   # ""/"0.0.0.0" = all interfaces
# or non-blocking: canvas.serve(port=8000, host="0.0.0.0", block=False)
```

When you bind non-locally, `serve()` prints the address to use elsewhere:

```
PyCanvas serving  (Ctrl+C to stop):
  local:   http://127.0.0.1:8000
  network: http://192.168.1.42:8000   <- open this on another device on the same Wi-Fi
```

Open that **network** URL on the other device — it uses *this* machine's IP, not
its own. Everyone connected sees the same canvas and shares control in real time.

Caveats:
- **Firewall** — your OS may block inbound connections; accept the prompt on first
  run, or allow the port (Windows admin shell:
  `New-NetFirewallRule -DisplayName "PyCanvas 8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Any`).
- **No IP / across networks** — LAN sharing only reaches the same network. To
  share with anyone, anywhere, use a tunnel — built in, see
  [Sharing across the internet](#sharing-across-the-internet-tunnels) below.
- **Authentication is opt-in** — by default anyone who can reach the port can
  interact. Set a `password=` (see [Password-protecting a canvas](#password-protecting-a-canvas))
  to gate access, and note the `Repl` remote-exec guard.

## Password-protecting a canvas

Any shared canvas — a LAN bind, a tunnel, or both — can be gated behind a
password so only people you give it to can connect:

```python
canvas.serve(port=8000, host="0.0.0.0", password="let-me-in")
canvas.serve(port=8000, tunnel=True, password="let-me-in")   # works over the tunnel too
```

A visitor is shown a small password page first; once they enter it, a
per-browser session cookie lets them straight through on every later request and
the WebSocket — so they're asked once, not per panel. The password itself is
never stored in the cookie (a random session token is), and the check guards
both the page and the live socket, so an unauthenticated client can't even open
the data channel.

A password controls **who may connect**; it's independent of the `Repl`
remote-exec guard, which controls **whether arbitrary code may run**. A
publicly-served `Repl` still needs the explicit `allow_remote_exec=True` even
behind a password — authenticated viewers would otherwise get code execution.

## Sharing across the internet (tunnels)

LAN sharing only reaches devices on the same network. To let anyone — on any
network, anywhere — open the canvas, pass `tunnel=True`. PyCanvas keeps the
server bound to `127.0.0.1` and opens a public HTTPS tunnel to it, printing a
shareable `https://…` URL:

```python
canvas.serve(port=8000, tunnel=True)
# or non-blocking: canvas.serve(port=8000, tunnel=True, block=False)
```

```
PyCanvas serving at http://127.0.0.1:8000  (Ctrl+C to stop)
PyCanvas public URL: https://timely-exceed-charts-graphic.trycloudflare.com   <- share this with anyone, anywhere
```

Send anyone that URL — the frontend dials its WebSocket from the page origin, so
everything (including video and live plots over `wss`) works through the tunnel
with no extra setup. The tunnel closes automatically when the server stops.

**Backends.** The default is **cloudflared** — no signup, no visitor warning
page. The easiest way to get it is the optional extra, which downloads and
caches the binary for you on first use (no manual install, no PATH fuss):

```bash
pip install -e ".[tunnel]"     # pulls pycloudflared; tunnel=True then just works
```

Or install cloudflared yourself (`brew install cloudflared`,
`winget install --id Cloudflare.cloudflared`, or
[download](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)) —
PyCanvas finds a system install on `PATH` (or in the installer's default
location) too. **localtunnel** is also supported (needs Node: `npm i -g
localtunnel`, or `npx`), but it shows first-time visitors an IP-password
reminder page:

```python
canvas.serve(port=8000, tunnel=True, tunnel_provider="localtunnel")
```

Caveats:
- **Public by default** — the URL is reachable by anyone who has it. Add a
  `password=` to gate it (see [Password-protecting a canvas](#password-protecting-a-canvas)).
  A tunnel exposes the loopback bind to the whole internet, so a canvas containing
  a `Repl` is refused unless you pass `allow_remote_exec=True` (a `Repl` is
  unauthenticated remote code execution — same gate as a public `host=` bind).
- **Quick-tunnel URLs are random and ephemeral** — a new `https://…` name each
  run. That's expected for cloudflared quick tunnels; named tunnels (a Cloudflare
  account) are out of scope here.

## Merging canvases

A `Canvas` is single-process, but you can still build **one shared surface from
several independently-hosted canvases**. Everyone keeps running their own canvas
on their own port; a *merge host* connects to each of them (as a client, like a
browser does), composites their panels onto a single new port, and routes
interactions back to whichever canvas owns each panel.

The payoff: computation stays sharded. Sarah's buttons compute in Sarah's
process, Josef's in his — only the *view and the input routing* are unified.

```bash
# unify three running canvases onto http://localhost:8080
python -m pycanvas.merge :8001 :8002 host3:8003 --port 8080
```

```python
from pycanvas import Merge

Merge([8001, 8002]).serve(port=8080)            # blocks, opens the browser
# or in a notebook:
m = Merge([8001, 8002]).serve(port=8080, block=False)
m.stop()
```

By default the canvases are **overlaid**, each panel keeping its real
coordinates. Pass `region_width` (or `--region-width`) to instead spread the
sources side-by-side, each in its own region that many pixels wide. A source's
panels go inert and drop from the view while it's disconnected, and reappear
when it reconnects.

**Across networks.** Sources aren't limited to `host:port` on your LAN — a source
may also be the public URL of a [tunneled](#sharing-across-the-internet-tunnels)
canvas, so a merge host can composite peers anywhere on the internet. And the
merged view itself can be tunneled (`tunnel=True` / `--tunnel`) so collaborators
on any network can open it:

```bash
# merge two remote (tunneled) canvases and expose the result publicly too
python -m pycanvas.merge https://a.trycloudflare.com https://b.loca.lt --tunnel
```

```python
Merge(["https://a.trycloudflare.com", ":8002"]).serve(port=8080, tunnel=True)
```

`http(s)://` URLs are mapped to `ws(s)://…/ws` automatically; bare ports and
`host:port` keep using `ws://` as before.

Caveats / v1 scope:
- Free-form drawings aren't composited — only code-driven panels and arrows are.
- Rearranging panels in the merged view is local to the merge host; it isn't
  pushed back to the source canvases (control interactions *are* routed back).
- A `Repl` panel is **not** drivable from the merged view unless you pass
  `--allow-remote-exec` / `allow_remote_exec=True` — driving one runs arbitrary
  code in the source's process (same gate as `Canvas`).

## Examples

```bash
python examples/hello_world.py        # slider + label
python examples/frontend_backend_tour.py  # interactive tour of the wire protocol, with a live frame tap
python examples/sensor_dashboard.py   # live VideoFeed + worker thread
python examples/custom_html.py        # hand-written HTML panel, bidirectional
python examples/custom_binary_stream.py  # high-rate binary telemetry into a Custom panel (push_binary)
python examples/custom_styled_component.py  # uiverse.io HTML+CSS widget pasted into a Custom panel
python examples/react_styled_component.py   # uiverse.io React widget via React.from_uiverse
python examples/matplotlib_panel.py   # slider re-renders a matplotlib figure
python examples/plotly_panel.py       # interactive Plotly chart in a panel
python examples/robot_control.py      # everything: sliders, toggle, plot, video
python examples/repl_inspector.py     # on-canvas Python REPL + component/globals inspectors
python training_dashboard/train_dashboard.py  # TensorBoard-style training tracker (native panels)
python examples/chat_room.py          # shared chat room with editable viewer names
python examples/public_tunnel.py      # share a canvas worldwide via a public HTTPS tunnel
python examples/remote_control.py     # ⚠ stream this PC's screen + control it remotely (Windows)
```

The notebook examples open in Jupyter:

```bash
jupyter notebook examples/notebook_dynamic.ipynb   # live add/move/remove panels
jupyter notebook examples/merge_canvases.ipynb     # two canvases composited onto one merge host
```

> The matplotlib/plotly examples need extra libs: `pip install matplotlib plotly`

## Developing the frontend

The built bundle lives in `pycanvas/frontend/dist/` and is committed. To rebuild:

```bash
cd pycanvas/frontend
npm install
npm run build
```

For standalone UI work without a Python backend, run `npm run dev` and open
`http://localhost:5173/?demo` to seed sample shapes.

## Debugging the wire

Everything between Python and the browser is JSON frames over one WebSocket,
so when something "doesn't update" the first question is always: *is the frame
on the wire or not?* Three tools answer it without touching any internals.

**`serve(debug=True)`** logs every frame to the console — what Python sends
(`->`) and what each browser sends back (`<-`) — with the component's friendly
name resolved:

```
[pycanvas] <- input 'speed'   {"type": "input", "id": "...", "payload": {"value": 7}}
[pycanvas] -> update 'mirror' {"type": "update", "id": "...", "payload": {"value": "saw 7"}}
```

**`canvas.on_frame(fn)`** is the programmatic version — a decorator-friendly
observer called as `fn(direction, msg)` for every frame (`direction` is
`"out"` or `"in"`; heartbeats are skipped, binary media frames arrive as a
small `{"type": "binary", "id", "media", "bytes"}` summary). Taps may safely
drive components — frames a tap itself causes are not re-tapped, so e.g.
mirroring traffic into a panel can't loop:

```python
@canvas.on_frame
def log(direction, msg):
    print(direction, msg["type"], msg.get("id"))
```

**Connection lines** are always printed, debug or not, so a viewer reaching
(or losing) the server is never invisible:

```
[pycanvas] viewer 'Otter' connected (replayed 4 panels, 1 arrows)
[pycanvas] viewer 'Otter' disconnected
```

See [`examples/frontend_backend_tour.py`](examples/frontend_backend_tour.py)
for an interactive walkthrough of the protocol that mirrors live frames onto
the canvas itself.

**Stale tabs heal themselves.** Panel ids are minted fresh on every run of your
script, and a browser tab from an earlier run reconnects automatically without
reloading the page. The server stamps each run with an id in its `welcome`
frame; when the frontend sees the run change, it drops the previous run's
panels before the new run's are replayed — so re-running a script never leaves
dead, stacked duplicates behind, with or without `hot_reload`.

## WebSocket protocol

All JSON over a single connection at `ws://localhost:{port}/ws`:

```json
{ "type": "register", "id": "<id>", "component": "Slider", "props": { ... }, "x": 80, "y": 80, "rotation": 0 }
{ "type": "update",   "id": "<id>", "payload": { "value": 120 } }
{ "type": "remove",   "id": "<id>" }
{ "type": "input",    "id": "<id>", "payload": { "value": 120 } }
```

High-rate media (`VideoFeed`, `AudioFeed`) skips JSON entirely: each chunk is
sent as a **binary** WebSocket frame — a 2-byte header `[type][id-length]`, the
component id, then the raw payload (JPEG bytes for video, little-endian int16 PCM
for audio). The browser feeds it straight into a `Blob`/`ArrayBuffer` with no
base64 decode and no JSON parse (~33% fewer bytes than a base64 data-URL).
Control messages stay JSON — they're low-rate and self-describing, so binary
would cost readability for no throughput.

`register` carries optional `x`/`y`/`rotation` (top-level shape placement;
`rotation` in radians) plus optional lock/appearance flags (`locked`, `movable`,
`resizable`, `interactive`, `selectable`, `frame`). `update` payloads may include
`value`/component props as well as live layout changes (`x`, `y`, `w`, `h`,
`rotation`) and those same flags. `locked` maps to tldraw's `isLocked`;
`movable`/`resizable`/`interactive`/`selectable`/`frame` ride in the shape's
`meta` (`lockMove`/`lockResize`/`lockInput`/`noGrab`/`noFrame`) so they gate
user gestures (or strip the card chrome) without freezing programmatic
updates. `remove` deletes a
panel from connected clients. Server → browser: `register`, `update`, `remove`;
browser → server: `input`.
