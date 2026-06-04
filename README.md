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
servo = canvas.insert(pycanvas.Slider(label="servo_1", min=0, max=180, default=90))
status = canvas.insert(pycanvas.Label(label="status", value="idle"))

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
plot = canvas.insert(pycanvas.LivePlot(label="servos", traces=["s1", "s2"], max_points=300))
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
    pycanvas.Slider(label="servo_1", min=0, max=180, default=90),
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

Components are also reachable by name off the canvas — `name=` on `insert`, or
the component's `label` if it's a valid identifier:

```python
canvas.servo.rotation = 45          # same object as the `servo` variable
canvas["servo"].update(120)
```

> Layout values reflect what Python last set. Dragging or resizing a panel in
> the browser is **not** reported back to Python (so `x`/`y` stay `None` until
> you place a panel from Python).

## Interactive use (Jupyter / notebooks)

`serve()` blocks, which is fine for scripts. In a notebook use
`serve_background()` instead: it starts the server in a thread and returns, so
later cells can keep adding, moving, and removing panels on the **already-open**
canvas.

```python
import pycanvas
canvas = pycanvas.Canvas().serve_background(port=8000)   # returns immediately

# ...any later cell — appears live on the open page...
servo = canvas.insert(pycanvas.Slider(label="servo_1", min=0, max=180, default=90))

canvas.remove(servo)   # pull a panel off the canvas
canvas.stop()          # shut the background server down
```

See [`examples/notebook_dynamic.ipynb`](examples/notebook_dynamic.ipynb) for a
full walkthrough.

> Note: a `Canvas` is single-process — one Python process owns the port and all
> components. Two separate scripts can't add to the same canvas/port.

## Examples

```bash
python examples/hello_world.py        # slider + label
python examples/sensor_dashboard.py   # live VideoFeed + worker thread
python examples/custom_html.py        # hand-written HTML panel, bidirectional
python examples/matplotlib_panel.py   # slider re-renders a matplotlib figure
python examples/plotly_panel.py       # interactive Plotly chart in a panel
python examples/robot_control.py      # everything: sliders, toggle, plot, video
```

The notebook example opens in Jupyter:

```bash
jupyter notebook examples/notebook_dynamic.ipynb   # live add/move/remove panels
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
`rotation` in radians). `update` payloads may include `value`/component props as
well as live layout changes (`x`, `y`, `w`, `h`, `rotation`). `remove` deletes a
panel from connected clients. Server → browser: `register`, `update`, `remove`;
browser → server: `input`.
