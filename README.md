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

## Two ways to add a panel

`canvas.<component>(...)` builds a panel **and** places it in one call — the
concise default. Every component has a factory: `slider`, `toggle`, `label`,
`video`, `audio`, `plot`, `live_plot`, `custom`, `file_browser`, `webview`,
`chat`, `repl`, `inspector`.

```python
servo = canvas.slider("servo_1", min=0, max=180, default=90)
feed  = canvas.video("camera")
plot  = canvas.live_plot("servos", traces=["s1", "s2"])
```

The first argument is the component's `name` (its unique `canvas.<name>` handle);
an optional `label=` sets a different on-screen caption. Factories also forward
`insert`'s placement and lock options, so a fully-specified panel fits on one line:

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
| `Button`    | input          | momentary action; `@on_click`, `.value` (click count); `text=` |
| `Label`     | output         | `.update(text)` |
| `VideoFeed` | output         | `.update(bgr_frame)` (OpenCV → binary JPEG over WS) |
| `AudioFeed` | output         | `.update(pcm_chunk)` (PCM → Web Audio playback) |
| `Plot`      | output         | `.update(fig_or_html)` (Plotly figure or HTML) |
| `LivePlot`  | output         | streaming telemetry; `.push({trace: y, ...})`, `.clear()` |
| `Custom`    | bidirectional  | arbitrary HTML in a sandboxed iframe; `@on(event)` / `@on_message`, `.push(data)`, `.update(html)` |
| `WebView`   | output         | an external website/URL in an iframe; `.navigate(url)` |
| `Chat`      | bidirectional  | shared chat across all viewers; editable names; `.post(text)`, `@on_message` |
| `FileBrowser` | bidirectional | navigate a folder (sandboxed to `root=`); `@on_select` (file path), `@on_navigate`, `.value`, `pattern=` |

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

### Streaming performance: queue policy & pre-encoded frames

Two knobs keep pycanvas a thin, fast layer for high-rate feeds (cameras, live
telemetry) without piling up latency on a slow viewer.

**Queue policy** — how a component's updates behave when they outpace the
connection. Every component has a `queue` setting (settable as a property, so it
works on any of them):

- **`"fifo"`** (default) — deliver every update in order, nothing dropped. Right
  for controls, labels, anything where each value matters.
- **`"latest"`** — keep only the newest pending value per viewer, dropping stale
  ones. Right for live video/telemetry, where the current frame is all that
  matters. Dict updates merge newest-per-key (so partial `set_layout`s aren't
  lost); binary frames replace wholesale. Bounds the per-viewer backlog to one
  in-flight send + one pending value, so a fast producer can't lag a slow client.

```python
cam = canvas.video("door")          # VideoFeed defaults to queue="latest"
plot = canvas.live_plot("temps")
plot.queue = "latest"               # drop stale telemetry for slow viewers
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

`panel.update(html)` swaps the whole HTML (reloads the iframe). To stream live
data **without** reloading — keeping the iframe's focus, listeners and scroll —
use `panel.push(data)`, received via `canvas.onPush(fn)`. That's what powers
[`examples/remote_control.py`](examples/remote_control.py), which streams the
host's screen into one panel and replays the browser's mouse/keyboard back onto
the machine (a tiny LAN remote desktop — read its security note first).

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
        super().__init__(html=DIAL_HTML, name=name, width=220, height=260, **place)
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

### Inspector from the toolbar

You don't have to add an `Inspector` in code to peek at the canvas. A toolbar
button (bottom-left of the canvas) spawns an ephemeral `Inspector` panel on
demand — click it to drop one in, click again to remove it. From there you can
browse every panel's live name/type/value/geometry, switch its header dropdown
to the kernel **globals** view, and click any row to drill into an object's
fields. It's the same `Inspector` component, just summoned from the UI instead
of `canvas.inspector(...)`.

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

See [`examples/fixed_view.py`](examples/fixed_view.py).

## Layout: position, size, rotation

Pass placement to `insert`, or change it live at any time. `x`/`y` are canvas
coordinates, `w`/`h` are pixels, `rotation` is in degrees (clockwise). Omit
`x`/`y` to let the canvas auto-place the panel; omit `w`/`h` to use the
component's default size.

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

Four **independent** controls gate how a panel responds to the user. Set any of
them on `insert` (or a factory), or flip them live as a property — each write is
pushed to the browser immediately. Because they're separate axes you can mix them
freely (e.g. pin a panel in place while keeping its slider live).

| Control             | User can move? | User can resize? | Controls interactive? | Python `update()` renders? |
|---------------------|----------------|------------------|-----------------------|----------------------------|
| *(default)*         | yes            | yes              | yes                   | yes                        |
| `movable=False`     | **no**         | yes              | yes                   | yes                        |
| `resizable=False`   | yes            | **no**           | yes                   | yes                        |
| `interactive=False` | yes            | yes              | **no**                | yes                        |
| `locked=True`       | **no**         | **no**           | **no**                | **no** (frozen)            |

```python
servo = canvas.slider("servo_1", min=0, max=180, default=90)

servo.movable = False        # user can't drag the panel; the slider still works
servo.resizable = False      # user can't resize it; the slider still works
servo.interactive = False    # user can't operate the slider, but your update()s
                             #   still move the thumb — and the panel stays
                             #   movable/resizable (those axes are unaffected)
servo.locked = True          # full lock: no move, resize, or interaction — AND
                             #   programmatic update()s stop rendering too
```

Two helpers wrap the common combinations:

```python
servo.pin();  servo.unpin()    # movable=False + resizable=False (controls stay live)
servo.lock(); servo.unlock()   # full lock on / off
```

The key distinction is **`interactive` vs `locked`**: `interactive=False` blocks
the *user* from operating the control while your code keeps driving it — a slider
whose thumb tracks an automatic value the user mustn't drag. `lock()` freezes
everything *including* your own `update()` calls, so the thumb would stop moving.
See [`examples/robot_control.py`](examples/robot_control.py) — vision mode makes
the servo sliders inert (`interactive=False`) while they sweep on their own.

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
you normally specify nothing. Two escape hatches when analysis gets it wrong:

```python
canvas.bake(name="App", include=["my_plugin"])   # force-add a dynamic/plugin import
canvas.bake(name="App", exclude=["torch"])        # skip a broken/unused dep that crashes the build
```

On a **conda** environment the MKL DLLs NumPy needs are detected and bundled
automatically (a pip/venv NumPy bundles its own BLAS, so it needs nothing). If a
build fails, the error names the culprit dependency and the `exclude` fix. The
same options exist on the CLI: `--include`, `--exclude` (both repeatable).

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
its own panel, auto-arranged in a grid. Outputs render through Jupyter's own
display machinery, so DataFrames, matplotlib/Plotly figures, and any
`_repr_html_` object look as they do inline. Re-running a cell swaps its panel
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
- **No authentication** — anyone who can reach the port can interact. Use only on
  networks you trust (and note the `Repl` remote-exec guard).

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
- **Public, unauthenticated** — the URL is reachable by anyone who has it. A
  tunnel exposes the loopback bind to the whole internet, so a canvas containing
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
python examples/sensor_dashboard.py   # live VideoFeed + worker thread
python examples/custom_html.py        # hand-written HTML panel, bidirectional
python examples/matplotlib_panel.py   # slider re-renders a matplotlib figure
python examples/plotly_panel.py       # interactive Plotly chart in a panel
python examples/robot_control.py      # everything: sliders, toggle, plot, video
python examples/repl_inspector.py     # on-canvas Python REPL + component/globals inspectors
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
`rotation` in radians) plus optional lock flags (`locked`, `movable`,
`resizable`, `interactive`). `update` payloads may include `value`/component
props as well as live layout changes (`x`, `y`, `w`, `h`, `rotation`) and those
same lock flags. `locked` maps to tldraw's `isLocked`; `movable`/`resizable`/
`interactive` ride in the shape's `meta` (`lockMove`/`lockResize`/`lockInput`) so
they gate user gestures without freezing programmatic updates. `remove` deletes a
panel from connected clients. Server → browser: `register`, `update`, `remove`;
browser → server: `input`.
