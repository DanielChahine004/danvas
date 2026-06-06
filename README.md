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
`video`, `plot`, `live_plot`, `custom`, `repl`, `inspector`.

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

## Components

| Component   | Direction      | API |
|-------------|----------------|-----|
| `Slider`    | bidirectional  | `.value`, `@on_change`, `.update(v)` |
| `Toggle`    | bidirectional  | `.value`, `@on_change`, `.update(opt)`; `options=[...]` |
| `Label`     | output         | `.update(text)` |
| `VideoFeed` | output         | `.update(bgr_frame)` (OpenCV → base64 JPEG) |
| `Plot`      | output         | `.update(fig_or_html)` (Plotly figure or HTML) |
| `LivePlot`  | output         | streaming telemetry; `.push({trace: y, ...})`, `.clear()` |
| `Custom`    | bidirectional  | arbitrary HTML in a sandboxed iframe; `.update(html)`, `@on_message` |

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

### Custom HTML panels

`Custom` renders any HTML/CSS/JS string (or a file via `path=`) inside a
sandboxed iframe. A `canvas.send(data)` helper is injected so the panel can
post structured data back to Python:

```python
panel = canvas.insert(pycanvas.Custom(html="<button onclick=\"canvas.send({hi:1})\">go</button>"))

@panel.on_message
def handle(data):
    print(data)   # -> {'hi': 1}
```

`panel.update(html)` swaps the whole HTML (reloads the iframe). To stream live
data **without** reloading — keeping the iframe's focus, listeners and scroll —
use `panel.push(data)`; it arrives as a `message` event in the iframe
(`e.data.__pycanvas` is your `data`). That's what powers
[`examples/remote_control.py`](examples/remote_control.py), which streams the
host's screen into one panel and replays the browser's mouse/keyboard back onto
the machine (a tiny LAN remote desktop — read its security note first).

Because it's just HTML in an iframe, anything that renders to HTML works:

- **matplotlib** — `fig.savefig(buf, 'png')` → base64 `<img>` → `panel.update(html)`
- **Plotly** — `fig.to_html(include_plotlyjs='cdn')` → `panel.update(html)`; the
  chart stays fully interactive (zoom / pan / hover) inside the sandbox.

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
page. Install it once (`brew install cloudflared`,
`winget install --id Cloudflare.cloudflared`, or
[download](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)).
**localtunnel** is also supported (`pip`-free, needs Node: `npm i -g localtunnel`,
or `npx`), but it shows first-time visitors an IP-password reminder page:

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

`register` carries optional `x`/`y`/`rotation` (top-level shape placement;
`rotation` in radians) plus optional lock flags (`locked`, `movable`,
`resizable`, `interactive`). `update` payloads may include `value`/component
props as well as live layout changes (`x`, `y`, `w`, `h`, `rotation`) and those
same lock flags. `locked` maps to tldraw's `isLocked`; `movable`/`resizable`/
`interactive` ride in the shape's `meta` (`lockMove`/`lockResize`/`lockInput`) so
they gate user gestures without freezing programmatic updates. `remove` deletes a
panel from connected clients. Server → browser: `register`, `update`, `remove`;
browser → server: `input`.
