# PyCanvas

A browser-based spatial canvas whose panels are defined and controlled entirely
from Python. Panels are bidirectional — Python pushes data to them and reads
user input back in real time over one WebSocket.

Built on [tldraw](https://tldraw.dev) + React + Vite (frontend) and FastAPI +
WebSockets (backend). The frontend ships pre-built; you never touch Node or npm.

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

The loop is always: build panels → register callbacks → `serve()`. Python owns
all state; the browser renders it and reports user actions.

Your `@on_change` / `@on_layout` / `@panel.on(...)` handlers run on a background
worker thread, so a blocking handler (`time.sleep`, an HTTP call, a slow
compute) won't freeze the canvas or stall other viewers. Handlers for one panel
run **in order**.

## Creating panels

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

## Components

| Component | Direction | API |
|---|---|---|
| `Slider` | bidirectional | `.value`, `@on_change`, `.update(v)`; `step=` (fractional → float slider + number entry), `on_release=True` (report only on let-go) |
| `Toggle` | bidirectional | `.value`, `@on_change`, `.update(opt)`; `options=[...]` |
| `Button` | input | `@on_click`, `.value` (click count), `text=`, `.update(text)` |
| `Label` | output | escaped text/number; `.update(text)`; `h="auto"` |
| `VideoFeed` | output | `.update(bgr_frame)` → binary JPEG; `encode=False` for pre-encoded |
| `AudioFeed` | output | `.update(pcm_chunk)` → Web Audio playback |
| `Plot` | output | `.update(fig_or_html)` (Plotly figure or HTML, in an iframe) |
| `LivePlot` | output | streaming telemetry; `.push({trace: y}, x=)`, `.clear()`, `smoothing=` |
| `Histogram` | output | distribution over time; `.add(values, step)` |
| `Custom` | bidirectional | arbitrary HTML in a sandboxed iframe; `@on(event)`/`@on_message`, `.push(data)`/`.push_binary(bytes)`, `.update(html/css/js)` |
| `React` | bidirectional | your JSX, compiled in-browser; `@on(event)`/`@on_request`, `.update(**props)`, `.push(data)` |
| `Markdown` | output | rendered Markdown; `.update(text)` |
| `Image` | output | path/URL/bytes/Matplotlib/PIL/array; `.update(src)`, `fit=` |
| `Table` | output | DataFrame/Series/records/dict → sortable, filterable; `.update(data)` |
| `WebView` | output | external site in an iframe; `.navigate(url)` |
| `Chat` | bidirectional | shared room across viewers; `.post(text)`, `@on_message` |
| `FileBrowser` | bidirectional | navigate a folder (sandboxed to `root=`); `@on_select`, `.value`, `pattern=` |
| `Download` | input | a button that sends a host file/`bytes` to the viewer; `source=` (path or bytes) or `@provide`, `filename=` |
| `Upload` | input | a button / drop-zone that receives a viewer's file into Python; `@on_upload`, `.value`, `dest=` (stream to disk), `accept=`, `multiple=`, `max_size=` |
| `Repl` | bidirectional | on-canvas Python REPL; needs `enable_repl()` |
| `Inspector` | output | live panel/globals state browser |

### The three data verbs

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

## Controlling panels live

Every panel:

```python
panel.update(...)                 # push new state (signature varies per component)
panel.move(x, y); panel.resize(w, h); panel.rotate(deg)
panel.set_layout(x=, y=, w=, h=, rotation=, locked=, ...)   # any combo, one message
panel.to_front(); panel.to_back(); panel.forward(); panel.backward()   # z-order
panel.x, panel.y, panel.w, panel.h, panel.rotation   # read/write live
panel.value                       # current value (sliders, toggles, button count)
panel.queue = "latest"            # backpressure policy (below)
```

`to_front`/`to_back` persist across reload; `forward`/`backward` are a live
nudge only.

Canvas-level:

```python
canvas.remove(panel)
canvas.connect(a, b, text="x2", color="blue")   # arrow that follows both panels
canvas.disconnect(arrow)                         # or by name
canvas.servo_1            # reach any panel by name (canvas["servo_1"] also works)
canvas.components         # list of every panel (canvas.arrows for connectors)
```

## Receiving input

```python
@slider.on_change         # fn(value)
@toggle.on_change         # fn(value)
@button.on_click          # fn()
@panel.on_layout          # fn(comp), after a user drag/resize (geometry synced)
@chat.on_message          # fn(entry); reply with chat.post(text)
```

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

`role` is the server-trusted login level (`None` unless `passwords=` is set);
`id`/`name`/`color` come from the live roster — fine for attribution, not
authorization. (Uploads receive the same `viewer`; see *File uploads* above.)

**Custom panels (your own protocol):** browser JS calls
`canvas.send({event:'x', ...})`; Python routes with `@panel.on("x")`; Python
replies via `panel.push(data)`, received in JS by `canvas.onPush(cb)`.

## Layout

`x`/`y` are canvas coords, `w`/`h` pixels (aliases `width`/`height`), `rotation`
degrees clockwise. Omit `x`/`y` → panels auto-arrange (left-to-right,
top-to-bottom, packed by size). Omit `w`/`h` → component default.

**Relative placement** — anchor to a placed panel; `gap` defaults to 16:

```python
controls = canvas.slider("t", min=0, max=1, step=0.01, below=plot)
legend   = canvas.markdown("…", right_of=plot, gap=24)
button   = canvas.button("go", below=controls, right_of=plot)   # two anchors = both axes
```

`below`/`above` align left edges; `right_of`/`left_of` align tops. An explicit
`x`/`y` overrides the derived coordinate. The anchor must already have a
position.

**Auto-layout containers** — open a `with` block; panels inside without an
explicit position drop into the next slot:

```python
with canvas.grid(cols=2, slot=(560, 300), gap=24, origin=(40, 40)):
    canvas.live_plot("loss")
    canvas.live_plot("accuracy")      # next column
    canvas.image(fig)                 # wraps to next row

with canvas.column(width=320, gap=12):    # stacks; each keeps its natural height
    canvas.label("status", "ready")
    canvas.button("start")
    canvas.slider("lr", min=0, max=1, step=0.01)
```

`row(height=…)` is the horizontal twin of `column`. An explicit position or
relative anchor still wins per panel.

**Auto-height** — `h="auto"` fits a panel's height to its rendered content
(Custom-/React-based panels: `markdown`, `custom`, `table`, `image`, `label`,
controls). Also a live property:

```python
notes = canvas.markdown("# Heading\n\nas tall as this text", h="auto")
notes.h = 240          # assigning a number turns auto off
```

Layout reflects both what Python set **and** the user's drags/resizes/rotations
(reported back, so `x`/`y`/`w`/`h`/`rotation` stay in sync). A panel's `x`/`y`
are `None` until first placed.

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

## Custom HTML panels

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
  `queue=`.
- `event_key=` changes the routing field (default `event`).
- Anything that renders to HTML works: matplotlib (`savefig` → base64 `<img>`),
  Plotly (`fig.to_html(include_plotlyjs='cdn')`, stays interactive).

Subclass `Custom` only to package HTML behind a typed constructor or to override
`state_payload()` — called for every connecting client so the iframe is seeded
with current state on load (no ready-handshake needed).

## React panels

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

- `push_binary(bytes)` → `onFrame` as a zero-copy `ArrayBuffer`.
- `scope=["d3"]` loads ESM libs from a CDN, exposed as the `libs` global.
  Friendly names (`d3`, `lodash`, `date-fns`, `framer-motion`, `lucide`) map to
  pinned React-externalised builds; anything else passes through to esm.sh.
- `React.from_uiverse(raw)` rewrites a `styled-components` snippet into plain
  React + CSS the in-browser pipeline accepts.
- `h="auto"`/`w="auto"` shrink the panel to its content.

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
    # {"role": "manager", "id": "a1b2c3d4", "name": "Fox", "color": "#ef4444"}
    file.save(f"uploads/{viewer.get('role') or viewer['name']}/")
```

`role` is the **server-trusted** login level (`None` unless you set
`passwords={...}`) — gate permissions on this. `id`/`name`/`color` come from the
live viewer roster (great for attribution/per-user folders, but client-reported,
so not for authorization).

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

**Streaming performance** — `queue` policy decides what happens when updates
outpace a slow viewer:

- `"fifo"` (default) — deliver everything in order.
- `"latest"` — keep only the newest pending value per viewer (right for
  video/telemetry). `VideoFeed` defaults to this.

```python
plot = canvas.live_plot("temps", queue="latest")   # or plot.queue = "latest" later
```

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

## Background workers

Register producer loops (camera, sensor, telemetry) with `@canvas.background` —
`serve()` runs each on a daemon thread in the serving process only.

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

**Required, not just tidy, with `hot_reload=True`:** a hand-started thread at
module scope runs in *both* the file-watching monitor and the worker, so it
would double-grab a single-owner resource (camera, serial port). `background`
defers the thread to the worker only.

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
[`training_dashboard/train_dashboard.py`](training_dashboard/train_dashboard.py).

## Viewport & navigation

Pass a `view` dict to `serve` (all keys optional):

| Key | Effect |
|---|---|
| `x`, `y`, `zoom` | initial camera (centre on `(x, y)` at `zoom`; 1.0 = 100%) |
| `locked` | `True` freezes pan/zoom (kiosk view) |
| `min_zoom`, `max_zoom` | clamp zoom range |
| `ui` | `False` hides tldraw chrome **and** the Inspector button |
| `grid` | `True` shows the background grid |
| `read_only` | `True` blocks freehand drawing |

```python
canvas.serve(view={"x": 200, "y": 160, "zoom": 1.0, "locked": True, "ui": False})
```

Change it live with `set_view` (same keys; only those you pass change). Pass
`client_id` to move just one viewer (ids from `canvas.viewers`):

```python
canvas.set_view(ui=False)
canvas.set_view({"zoom": 2.0})
canvas.set_view(x=0, y=0, zoom=1.5, client_id=some_id)   # one viewer only
```

A toolbar button (bottom-left) spawns an ephemeral `Inspector` on demand —
offered only on a local bind by default (`ui_inspector=True`/`False` to
override), since it can surface state to everyone.

## Hot reloading

```python
canvas.serve(port=8000, hot_reload=True)   # run as `python your_script.py`
```

Restarts the process when you save a `.py` in the script's folder; the browser
tab reconnects on its own. A broken save is pre-flighted and skipped — the last
working version keeps serving. Needs `block=True` and a real script entry point.

## Notebooks

`serve(block=False)` returns immediately so later cells edit the open canvas:

```python
canvas = pycanvas.Canvas().serve(port=8000, block=False)
servo = canvas.slider("servo_1", min=0, max=180, default=90)   # appears live
canvas.remove(servo)
canvas.stop()
```

`canvas.capture_cells(cols=2)` (alias `pycanvas.autopanel(canvas)`) mirrors
every expression cell's output to its own auto-arranged panel (rendered via
`show()`); re-running a cell swaps its panel in place, keeping any geometry you
dragged. Override one cell with a `# pycanvas: x=40 y=80 w=600 locked=true`
directive line (or `# pycanvas: skip`); pass `auto=False` to flip to an
allowlist. Stop with `stop_capturing_cells()`.

In a plain script, a daemon background server dies with the process — call
`canvas.wait()` to park the main thread.

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

**Roles** — serve different views to different users from the same port. Use
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
into a `Blob`/`ArrayBuffer`. Server → browser: `register`/`update`/`remove`;
browser → server: `input`.

## Examples

```bash
python examples/hello_world.py            # slider + label
python examples/frontend_backend_tour.py  # interactive tour of the wire, live frame tap
python examples/sensor_dashboard.py       # live VideoFeed + worker thread
python examples/custom_html.py            # hand-written bidirectional HTML panel
python examples/custom_binary_stream.py   # high-rate binary telemetry (push_binary)
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
python training_dashboard/train_dashboard.py   # TensorBoard-style training tracker
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

See [GUIDE.md](GUIDE.md) for deeper detail.
