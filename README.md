# danvas

danvas builds interactive browser UIs — dashboards, control panels, live
visualizations — entirely in Python, with no HTML, JavaScript, or build step. You
make panels (sliders, plots, tables, buttons, video feeds, or your own React/HTML)
in Python; they appear on a zoomable browser canvas. A panel is just a face on
your program: a click or edit calls a normal Python function running in your
process, with full access to your code, libraries, hardware, and files — so
anything you can script, you can give a UI and drive live. State flows over one
WebSocket; Python owns it, the browser renders it and reports what the user did.

It's multi-user out of the box: any number of browsers — phones, tablets, or
desktops — share one live canvas, with a viewer roster, live cursors, chat, and
freehand drawing the host can read back. Share it across your LAN, behind a
password, or over a public HTTPS tunnel — all built in, no extra services.

## Install

```bash
pip install danvas
```

The base install is lightweight. Heavier features are optional extras:

| Extra | Enables |
|---|---|
| `pip install "danvas[video]"` | `VideoFeed` JPEG encoding (OpenCV, ~90 MB) |
| `pip install "danvas[audio]"` | microphone capture for `AudioFeed` |
| `pip install "danvas[tunnel]"` | public sharing (`serve(tunnel=True)`) |
| `pip install "danvas[desktop]"` | native window + `bake()` to a standalone app |

`canvas.video(...)` needs `[video]` for default encoding — or stream
already-JPEG bytes with `VideoFeed(encode=False)`, which needs nothing. For local
development, clone and `pip install -e .`.

## Hello world

```python
import danvas

canvas = danvas.Canvas()
servo  = canvas.slider("servo_1", min=0, max=180, default=90)
status = canvas.label("status", "idle")

@servo.on_change
def handle(value):
    status.update(f"servo at {value}")

canvas.serve(port=8000)   # opens the browser, blocks
```

![The hello-world canvas: a SERVO_1 slider whose value live-updates a STATUS label reading "servo at 113"](docs/hello_world.png)

## The mental model

Every danvas program is the same five steps:

```python
import danvas

canvas = danvas.Canvas()                          # 1. make a canvas
speed  = canvas.slider("speed", min=0, max=100)    # 2. make components (panels)
status = canvas.label("status", "idle")

@speed.on_change                                   #    read user input back …
def _(v, viewer):                                  #    (optional 2nd arg = who did it)
    status.update(f"{viewer['name']} set speed to {v}")   # … and push state out

speed.set_layout(x=40, y=40, w=320)                # 3. place/size it (optional)
canvas.set_view(zoom=1.0, ui=True)                 # 4. set camera/chrome (optional)
canvas.serve(port=8000)                            # 5. serve — opens browser, blocks
```

Build panels → register callbacks → `serve()`. The rest of this README follows
that path: [the canvas](#the-canvas) → [components](#components) →
[layout](#layout) → [shapes](#canvas-shapes) → [views & roles](#views-navigation--roles)
→ [serving & sharing](#serving--sharing) → [persistence, inspection & packaging](#persistence-inspection--packaging),
then [how it works](#how-it-works).

> **Handlers run on one ordered worker thread**, separate from the event loop, so
> a blocking handler (`time.sleep`, an HTTP call, slow compute) never freezes
> rendering or live `update()`s, and handlers run **in order**. That thread is
> shared, so a genuinely slow handler delays other panels' handlers until it
> returns — mark it [`threaded=True`](#receiving-input) to move it off.

# The canvas

`danvas.Canvas()` is the document everything hangs off. Build panels with its
factories (below) and manage them through it:

```python
canvas.remove(panel)      # destroy: gone from Python and browser
canvas.hide(panel)        # remove from browser, keep Python state (value/callbacks)
canvas.unhide(panel)      # show again at last position
canvas.clear()            # remove every panel + arrow at once
canvas.connect(a, b, text="x2", color="blue")   # arrow bound between two panels (or shapes)
canvas.disconnect(arrow)
canvas.servo_1            # reach any panel by name (canvas["servo_1"] also works)
canvas.components         # list of every panel (visible and hidden)
canvas.arrows             # list of arrows
panel.visible             # True if currently shown
```

**`hide` vs `remove`** — `hide` is reversible: the panel disappears from the
browser but stays alive in Python (id, value, callbacks intact); `unhide` brings
it back. `remove` is permanent — the id is gone, and re-inserting makes a brand
new panel. Use hide/unhide for panels you toggle on and off; remove when one is
genuinely done.

### Shared React components & styles

When you build custom UI with `react(...)`, repeating the same widget in every
panel's source gets old. `canvas.define()` registers a JSX component **once**,
usable *by name* in every React panel; `canvas.style()` adds **one** global
stylesheet (vs a panel's own scoped `css=`):

```python
canvas.define("StatusPill", """
  function StatusPill({ kind, children }) {
    return <span className={"pill " + kind}>{children}</span>
  }
""")
canvas.style(".pill{padding:2px 10px;border-radius:999px} .pill.ok{color:#4ade80}")

# now ANY react() panel can use it, no re-declaration:
canvas.react("function Component(){ return <StatusPill kind='ok'>In stock</StatusPill> }")
```

Both replay to every browser on connect and apply live while serving; a `define()`
mid-session recompiles open panels. They reach native `react()` panels only —
sandboxed `custom()` iframes are isolated. See `examples/shared_components.py`.

# Components

`canvas.<factory>(...)` builds a panel **and** places it, returning the handle.

```python
servo = canvas.slider("servo_1", min=0, max=180, default=90)
feed  = canvas.video("camera")
plot  = canvas.live_plot("servos", traces=["s1", "s2"])
```

**Argument convention.** Content-rendering panels take their content first
(`image(src)`, `table(data)`, `markdown(text)`, `toggle(options)`, `custom(html)`,
`react(source)`, `webview(url)`, `show(value)`); all others put `name=` first.
`name` is optional everywhere (it defaults to the type, e.g. `"slider"`) and is
the panel's Python identity — the `canvas.<name>` handle and the key that makes a
later insert under the same name **replace in place**. `label=` sets a different
on-screen caption. Every factory also forwards [`insert`'s placement and lock
options](#layout).

Build-now-place-later (or onto another canvas):

```python
s = danvas.Slider("servo_1", min=0, max=180, default=90)
canvas.insert(s, x=80, y=80)
```

## The component catalogue

| Component | Direction | API |
|---|---|---|
| `Slider` | bidirectional | `.value`, `@on_change`, `.update(v)`; `step=` (fractional → float slider + number entry), `on_release=True`; live: `.min`, `.max`, `.step`, `.color` |
| `Toggle` | bidirectional | `.value`, `@on_change`, `.update(opt)`; `options=[...]`; live: `.options`, `.color` |
| `Button` | input | `@on_click`, `.value` (click count), `text=`, `.update(text)`; live: `.text`, `.color` |
| `TextField` | bidirectional | single-line or `multiline=True`; `@on_change` on Enter/blur; `.value`, `.update(text)`, `placeholder=`; live: `.placeholder`, `.color` |
| `Label` | output | escaped text/number; `.update(text)`; `h="auto"`; live: `.color` |
| `Markdown` | output | rendered Markdown; `.update(text)` |
| `Image` | output | path/URL/bytes/Matplotlib/PIL/array; `.update(src)`, `fit=`; live: `.color` |
| `Table` | bidirectional | DataFrame/Series/records/dict/array → sortable, filterable, paginated; toolbar toggles index/column-visibility/row-selection (+ ✎ edit when `editable=True`); `@on_select(indices)`, `@on_edit(row, col, value)`; `.selected`, `.update(data)` |
| `Plot` | output | `.update(fig)` — a Plotly figure rendered natively, with the interactive toolbar (zoom/pan/box-zoom/save-PNG on hover) |
| `LivePlot` | output | streaming telemetry; `.push({trace: y \| [y…]}, x=)`, `.clear()`, `smoothing=`; live: `.max_points`, `.mode`, `.color` |
| `Histogram` | output | distribution over time; `.add(values, step)`; `color=` tints frame + chart |
| `VideoFeed` | output | `.update(bgr_frame)` → binary JPEG; `encode=False` for pre-encoded |
| `AudioFeed` | output | `.update(pcm_chunk)` → Web Audio playback |
| `Chat` | bidirectional | shared room across viewers; `.post(text)`, `@on_message` |
| `WebView` | output | external site in an iframe; `.navigate(url)` |
| `FileBrowser` | bidirectional | navigate a folder (sandboxed to `root=`); `@on_select`, `.value`, `pattern=` |
| `Upload` | input | click/drop zone receiving a viewer's file; `@on_upload`, `dest=` (stream to disk), `accept=`, `multiple=`, `max_size=` |
| `Download` | input | button sending a host file/`bytes` to the viewer; `source=` or `@provide`, `filename=` |
| `Custom` | bidirectional | arbitrary HTML/CSS/JS in a sandboxed iframe; `@on(event)`/`@on_message`/`@on_binary`, `.push(data)`/`.push_binary(bytes)`, `.update(html)` |
| `React` | bidirectional | your JSX, compiled in-browser, theme-aware; `@on(event)`/`@on_request`, `.update(**props)`, `.push(data)`, `css=` |
| `Inspector` | output | live panel/globals state browser |

Most `color=` panels expose `.color` (and most accept `lock`/`chrome` flags) —
see [Controlling panels live](#controlling-panels-live).

## The three data verbs

| Verb | Means | Replayed on reconnect? | Panels |
|---|---|:--:|---|
| `.update(value)` | **replace** the panel's whole state | ✅ | Label, Image, Table, Markdown, Plot, Slider, Toggle, Button, VideoFeed, AudioFeed |
| `.push(sample)` | **append** one sample to a live stream | ❌ | LivePlot, Custom, React |
| `.add(values, step)` | **record** one distribution at `step` | ✅ | Histogram |

```python
label.update("ready")                  # replace: the label IS this text
plot.push({"train": loss}, x=step)     # append: one more point on a live curve
weights.add(layer.weight, step=epoch)  # record: one distribution row
```

`LivePlot.push` also takes a **batch** (a list/array per trace, with matching `x`)
to add many points in one call — your lever on update *rate*: buffer in your loop
and flush when you choose. The server coalesces updates a slow client can't keep
up with, as a safety ceiling.

```python
plot.push({"train": losses, "val": vals}, x=steps)   # many points at once
```

## Show anything

`canvas.show(value)` inspects a value and inserts the best-fitting panel — like a
notebook deciding how to render `Out[...]`, but in plain scripts too:

```python
canvas.show(df)                        # DataFrame / records / list-of-lists → Table
canvas.show({"lr": 3e-4, "epochs": 40})# flat dict → key/value Table
canvas.show(arr_uint8)                 # NumPy uint8 / RGB array → Image
canvas.show(fig)                       # Matplotlib / Plotly → Image / Plot
canvas.show({"nested": {"a": 1}})      # nested dict/list → collapsible JSON tree
canvas.show("use **bold**")            # Markdown syntax → rendered text
canvas.show("report.csv")              # existing file → Table; "photo.png" → Image
canvas.show(model)                     # _repr_html_/_repr_png_ → its rich view
```

Dispatch is conservative (a single `*italic*` isn't Markdown; a path must be a
real file). No `name` → a fresh panel each call; `name=` replaces in place.
`danvas.panel_for(value)` builds without inserting. Extra kwargs are forwarded to
the chosen component when it accepts them, ignored otherwise; placement kwargs
(`below=`, `x=`, …) always route to `insert()`. See `examples/show_anything.py`.

## Controlling panels live

Every panel:

```python
panel.update(...)                 # push new state (signature varies per component)
panel.move(x, y); panel.resize(w, h); panel.rotate(deg)
panel.set_layout(x=, y=, w=, h=, rotation=, opacity=, locked=, ...)   # any combo, one message
panel.to_front(); panel.to_back(); panel.forward(); panel.backward()  # z-order
panel.x, panel.y, panel.w, panel.h, panel.rotation, panel.opacity     # read/write live
panel.label = "New title"         # live card-header rename
panel.value                       # current value (sliders, toggles, button count)
panel.queue = "latest"            # backpressure policy (below)
```

**Accent color** — any `color=` panel exposes `.color` as a live read/write
property; assigning re-tints the panel's theme + card border instantly:

```python
status = canvas.label("status", "idle", color=(0, 200, 0))
status.color = (100, 100, 255)   # (r, g, b) tuple or "#rrggbb" hex
status.color = None              # reset to default theme
```

Works at construction and live on Label, Slider, Toggle, Button, TextField, Table,
Chat, Image, VideoFeed, AudioFeed, Plot, LivePlot, FileBrowser, and any
`react`/`custom` panel. Some components also have specific live setters
(`slider.min/max/step`, `toggle.options`, `text_field.placeholder`).

**Streaming backpressure** — `queue` decides what happens when updates outpace a
slow viewer: `"fifo"` (default, deliver everything in order) or `"latest"` (keep
only the newest pending value per viewer — right for video/telemetry; `VideoFeed`
defaults to it).

```python
plot = canvas.live_plot("temps", queue="latest")   # or plot.queue = "latest" later
```

## Receiving input

| Handler | Component(s) | Args | Fires when |
|---|---|---|---|
| `@panel.on_change` | Slider, Toggle, TextField | `(value)` | user commits a value |
| `@button.on_click` | Button | `()` | pressed |
| `@table.on_select` | Table | `(indices)` | row selection changes |
| `@table.on_edit` | Table | `(row, col, value)` | cell edited (`editable=True`); value is a string |
| `@chat.on_message` | Chat | `(entry)` | viewer posts |
| `@browser.on_select` / `on_navigate` | FileBrowser | `(path)` / `(cwd)` | file clicked / dir changed |
| `@upload.on_upload` | Upload | `(file)` | file received (`file.data` or `file.path`) |
| `@panel.on_layout` | any | `(comp)` | user drags/resizes the panel |
| `@panel.on("event")` / `on_message` | Custom, React | `(msg)` | `canvas.send(...)` from the panel |
| `@panel.on_binary` | Custom | `(data: bytes)` | `canvas.sendBinary(buf)` from the iframe |
| `@panel.on_request("event")` | React | `(req)` | `await canvas.request(...)` — return resolves the Promise |
| `@panel.on_error` | Custom, React | `(msg)` | JS error in the panel |

These are plain registration methods, so `@panel.on_change` and
`panel.on_change(fn)` are the same — decorate for the common case, or call it
directly to reuse one handler across panels.

**Who did it.** *Any* handler may declare a trailing `viewer` parameter; one-arg
handlers are unchanged:

```python
@slider.on_change
def _(value, viewer):     # {"id","name","color","device","role"}
    print(viewer["name"], "→", value)
```

<a id="the-viewer-dict"></a>The `viewer` dict (same shape everywhere):

| key | what | trust |
|---|---|---|
| `id` | stable per-connection roster id (use with `client_id=`) | client-side — attribution only |
| `name` / `color` | editable display name / roster color | client-side |
| `device` | `"mobile"` / `"desktop"` from the User-Agent | client-side — presentation only |
| `role` | login level from `serve(passwords=)`, else `None` | **server-trusted — authorize on this** |
| `cursor` | `{"x","y"}` in canvas coords, or `None` (with `serve(cursors=True)`) | client-side |

Gate permissions on `role` only. On an **upload** the attribution fields are
`None` unless the uploader is still connected (the file arrives over HTTP, matched
to the live roster by id) — read them with `.get(...)` and a fallback.

**Action routing + field validation.** `@panel.on("name")` dispatches by the
payload's routing field, so a panel with several actions reads as one named
handler each instead of an `if msg["action"] == …` ladder (set the field with
`event_key=`). `fields={name: type}` coerces values off the wire before the
handler runs — a value that can't coerce drops the message (and logs why) instead
of crashing the handler:

```python
@stock.on("item_set", fields={"stock": int, "price": int})
def _(msg):                        # msg["stock"]/["price"] are ints here
    inventory[msg["item"]] = {"stock": msg["stock"], "price": msg["price"]}
```

**Handler threading — three modes.** Handlers run on one shared dispatch thread by
default (off the event loop, so the UI never freezes — but a slow one holds up
those queued behind it). Two flags move a handler off it:

| Flag | Thread model | Right for |
|---|---|---|
| *(default)* | inline on the shared dispatch thread | fast handlers: state updates, canvas calls |
| `threaded=True` | a new daemon thread *per call* | occasional slow work (HTTP, `sleep`, one-off compute) |
| `dedicated=True` | one persistent thread *for this handler* | handlers firing rapidly with non-trivial work |

```python
@fetch.on_click(threaded=True)              # new thread per click; doesn't block others
def _(viewer):
    table.update(slow_api_call())

@speed.on_change(dedicated=True, queue="latest")   # own serialised thread; drop stale drags
def _(v):
    status.update(heavy_compute(v))
```

`threaded` may run alongside itself (guard shared state); `dedicated` is always
serialised on its own thread. `queue=` (only with `dedicated`) is `"fifo"` (run
all in order) or `"latest"` (keep only the newest pending call). The two are
mutually exclusive. *(This `queue=` is handler-side dispatch backpressure;
`insert(panel, queue=…)` is the separate browser-delivery backpressure above.)*

**Adapt to who connects.** `canvas.on_connect(fn)` runs `fn(viewer)` once per
join (e.g. a mobile layout via per-viewer `client_id=`); `on_disconnect(fn)` is
the cleanup twin. `on_cursor(fn)` streams pointer moves (`serve(cursors=True)`).
Don't trust `device` for auth — it's a spoofable User-Agent guess.

```python
@canvas.on_connect
def adapt(viewer):
    if viewer["device"] == "mobile":
        for i, panel in enumerate(panels):
            panel.set_layout(client_id=viewer["id"], x=0, y=i * 220, w=360)
```

## Custom & React panels

Two factories ship your own UI from Python. **`react`** mounts JSX as a real React
subtree (native, theme-aware, interactive from first hover — reach for this
first); **`custom`** drops HTML/CSS/JS into a sandboxed iframe. Both are
bidirectional: `canvas.send(...)` posts up to Python, `push`/`update` send down.

### React

Pass full `source=` defining `function Component({ canvas, value, props })`, or
just `jsx=` markup + `css=`:

```python
counter = canvas.react(jsx='<button onClick={() => canvas.send({n: 1})}>tap</button>',
                       css='button { font-size: 18px; }')
```

- `value` is the latest `push(data)`; `props` is the `update(**props)` dict
  (replayed on reconnect). The `canvas` prop is the bridge handle:
  `send(data)` (→ `@on`/`@on_message`), `request(data)` (awaitable, → `@on_request`),
  `onFrame(cb)` (subscribe to `push` with no re-render; `ArrayBuffer` for binary),
  `viewport(cb)` / `setView({x,y,zoom})` (read/move the camera), `chat` (the shared room).
- `push_binary(bytes)` → `onFrame` as a zero-copy `ArrayBuffer` (React can receive
  binary but not send it — use `Custom` for browser→Python binary).
- `scope=["d3"]` loads ESM libs from a CDN as the `libs` global (friendly names
  like `d3`/`lodash`/`framer-motion`/`lucide` are pinned builds).
- `wasm_path="sim.wasm"` (or `wasm=bytes`) embeds a module, reached as
  `await canvas.wasm` inside the JSX. For >1 MB, host the `.wasm` and fetch by URL.
- `source=` normalises pasted web snippets (`import`/`export`, `styled-components`,
  hooks). React panels **auto-height by default** (pass numeric `h` to pin).
- **`update(roles=…, client_id=…, **props)`** scopes props to specific viewers as
  a persistent per-viewer overlay (precedence shared < role < client) — each viewer
  sees only their slice, replayed on reconnect (unlike one-shot `push`). See [Roles](#views-navigation--roles).
- **`panel.watch(path=…, css_path=…)`** dev hot-reloads the JSX/CSS on save;
  **`panel.validate()`** is a fast structural lint.

### Custom

```python
panel = canvas.custom(html='''
  <button onclick="canvas.send({event: 'go'})">go</button>
  <script>canvas.onPush((msg) => document.body.append(msg))</script>
''')

@panel.on("go")            # routes {event:'go'}; @on_message is the catch-all
def handle(msg):
    panel.push("clicked")  # → canvas.onPush in the iframe
```

- `html`/`css`/`js` may be separate strings (handy for pasted snippets).
  `update(html)` swaps the whole document; `push(data)` streams without reloading.
- `push_binary(bytes)` streams raw bytes (no base64); `canvas.onPush` gets an
  `ArrayBuffer`. Honours `queue="latest"` for video/sensor streams.
- **`canvas.sendBinary(buf)`** transfers an `ArrayBuffer` *up* to Python with zero
  overhead → `@panel.on_binary` (mark it `threaded=True` if it decodes/computes).
- **`canvas.requestCamera(opts)`** / **`requestMicrophone(opts)`** capture the
  webcam/mic from the **parent page** (browsers block `getUserMedia` in a sandboxed
  iframe), relaying frames up to `@on_binary` and back into the iframe via
  `onPush`. *(These three are Custom-only — they need direct socket access the
  React subtree doesn't expose.)*

## Specific panels

**Web pages** — `WebView` embeds a real site (`watch?v=` links rewritten to
`/embed/`); sites sending `X-Frame-Options: DENY` refuse to load (a browser rule).

```python
web = canvas.webview("https://en.wikipedia.org/wiki/Robot"); web.navigate("https://example.com")
```

**Downloads** — `Download` sends a host file/`bytes` to the viewer; *host code*
picks what each click serves (nothing to sandbox). Static `source=`, or
`@download.provide` to build content per click. The browser only ever sees an
unguessable, short-lived URL behind the auth gate.

```python
canvas.download("report", source="out/report.pdf", text="Download report")

dl = canvas.download("export", text="Export CSV")
@dl.provide
def _():
    return ("data.csv", make_csv().encode())   # (filename, bytes); path or bytes
```

**Uploads** — `Upload` streams a viewer's file *up* over plain HTTP (no WS size
limit), behind the same auth gate. Bytes arrive in memory (`file.data`); pass
`dest=` a directory to stream to disk (`file.path`, constant memory). The
browser-supplied filename is sandboxed inside `dest`. Set `max_size` on any
public canvas.

```python
up = canvas.upload("upload", text="Upload CSV", accept=".csv", max_size=5_000_000)
@up.on_upload
def got(file, viewer):               # .name .size .data/.path; viewer optional
    table.update(list(csv.DictReader(io.StringIO(file.data.decode()))))
```

**Audio** — `AudioFeed` streams PCM via Web Audio (capture needs `[audio]`;
playback needs nothing). Each viewer clicks **Enable audio** once.

```python
mic = canvas.audio("mic", sample_rate=16000); mic.update(chunk)   # float32/int16/bytes
```

**Chat & cursors** — `Chat` is a shared room (each viewer edits their name; Python
can `post`/`@on_message`). `serve(cursors=True)` lets viewers see each other's
cursors and Python read every pointer (`canvas.viewers[i]["cursor"]`, or
`@canvas.on_cursor`). Default on only for a private local bind.

# Layout

`x`/`y` are canvas coords, `w`/`h` pixels (aliases `width`/`height`), `rotation`
degrees clockwise. Omit `x`/`y` → panels auto-arrange; omit `w`/`h` → component
default. Every factory and `insert(...)` accepts the same `**place` options:

| Option | What it does |
|---|---|
| `x` / `y` | canvas position; omit → auto-arrange |
| `w` / `h` (`width`/`height`) | size in px; `"auto"` fits content (Custom/React panels) |
| `rotation` / `opacity` | degrees clockwise / 0.0–1.0 |
| `below` / `above` / `right_of` / `left_of` | place relative to another panel… |
| `gap` | …this many px away (default 16) |
| `queue` | backpressure: `"fifo"` or `"latest"` |
| `roles` / `lock_for` | login roles that may see it / get it non-interactive |
| `locked` / `draggable` / `resizable` / `operable` / `grabbable` / `frame` | lock & chrome flags (below) |

> **Prefer relative placement.** Hard-coded `x`/`y` is brittle — resize one panel
> and everything below needs adjusting. `below=`/`above=`/`right_of=`/`left_of=`
> pin each panel to a neighbour, so the layout stays gap-correct.

```python
controls = canvas.slider("t", min=0, max=1, step=0.01, below=plot)
legend   = canvas.markdown("…", right_of=plot, gap=24)
button   = canvas.button("go", below=controls, right_of=plot)   # two anchors = both axes
```

`below`/`above` align left edges; `right_of`/`left_of` align tops. When an
auto-height panel settles its height, panels anchored below it shift
automatically.

**Containers** — `column` stacks top-to-bottom, `row` side-by-side; nest with
`.row()`/`.column()`. Use `.add()` or a `with` block to capture panels:

```python
layout = canvas.column(x=60, y=40, w=640, gap=20)
layout.add(canvas.label("title", "My App"))
with layout.row(gap=8):                        # nested row
    canvas.label("step", "Step: 0")
    canvas.button("start", text="Start")
layout.add(canvas.markdown("…", h="auto"))     # grows; panels below auto-shift
```

`grid` fills a fixed grid slot-by-slot; `streamlit()` returns a full-width column
with vertical-scroll navigation (every child spans the window):

```python
with canvas.grid(cols=2, slot=(560, 300), gap=24, x=40, y=40):
    canvas.live_plot("loss"); canvas.live_plot("accuracy"); canvas.image(fig)
```

A container repacks when an `h="auto"` panel grows; `container.move(x, y)`
repositions the tree; `insert_before(ref, panel)` / `insert_after(...)` splice in.
`canvas.reset_layout()` restores every panel to its Python-defined position.

**Auto-height** — `h="auto"` fits a panel to its rendered content
(Custom/React-based: markdown, custom, table, image, label). React-based panels
auto-height by default. Also live: `notes.h = 240` turns it off.

## Locking & interactivity

Five independent controls; set on a factory/`insert`, or flip live as a property.

| Control | Move? | Resize? | Controls operable? | `update()` renders? |
|---|---|---|---|---|
| *(default)* | yes | yes | yes | yes |
| `draggable=False` | **no** | yes | yes | yes |
| `resizable=False` | yes | **no** | yes | yes |
| `operable=False` | yes | yes | **no** | yes |
| `grabbable=False` | **no** (Python only) | **no** | yes, immediately | yes |
| `locked=True` | **no** | **no** | **no** | **no** (frozen) |

```python
servo.operable = False      # user can't operate it; your update()s still drive it
servo.locked   = True       # full freeze, including programmatic updates
servo.pin(); servo.unpin()  # = draggable=False + resizable=False
servo.lock(); servo.unlock()
```

`operable=False` blocks the *user* while your code keeps driving; `locked=True`
freezes everything. `grabbable=False` (content-heavy panels) drops the
click-to-select cover so the widget is live immediately. `frame=False` strips card
chrome (background/border/shadow/padding/label) so content sits on the canvas —
pair with `grabbable=False` for a free-floating widget.

# Canvas shapes

Beyond panels you can place **managed canvas shapes** — vector shapes, freehand
strokes, text, sticky notes, lines, frames, highlighter marks. These are
Python-owned: they survive reload, update live, and are excluded from the
free-form drawing sync.

| Factory | Creates | Key kwargs |
|---|---|---|
| `canvas.geo(x, y, w, h, geo=…)` | rectangle, ellipse, cloud, star, diamond, … | `geo`, `color`, `fill`, `dash`, `size`, `text` |
| `canvas.text(x, y, text=…)` | floating text | `color`, `size`, `font`, `align` |
| `canvas.note(x, y, text=…)` | sticky note | `color`, `size`, `font` |
| `canvas.draw(points, …)` | freehand stroke (list of `(x, y[, pressure])`) | `color`, `size`, `dash`, `isClosed` |
| `canvas.highlight(points, …)` | semi-transparent highlighter | `color`, `size` |
| `canvas.line(points, …)` | polyline / cubic spline | `color`, `dash`, `size`, `spline="cubic"` |
| `canvas.frame(x, y, w, h, label=…)` | named artboard container | `label` |

Every factory accepts `name=`, `x=`/`y=`/`rotation=`/`opacity=`, and relative
placement. Every property is live-writable:

```python
box = canvas.geo(x=40, y=40, w=200, h=120, geo="rectangle", color="blue", fill="semi")
box.color = "orange"; box.text = "updated"; box.w = 240   # live setters
box.update(x=100, opacity=0.7, color="red")               # any mix at once
box.move(x=200); box.remove()
canvas.shapes; canvas.remove_shape("name-or-object")
```

`color` values: black, blue, green, grey, light-blue/green/red/violet, orange,
red, violet, white, yellow. `fill`: none/semi/solid/pattern. `dash`:
draw/dashed/dotted/solid. `size`: s/m/l/xl.

**Arrows connect shapes too.** `canvas.connect(a, b)` binds an arrow between any
two endpoints — panels *or* managed shapes — so a `geo`/`note`/`frame` becomes a
node and the arrow reroutes as you drag it. That makes Python-owned diagrams
(block diagrams, flowcharts, schematics) just shapes + arrows. Every arrow prop
is live-writable, and re-connecting under the same `name` replaces the arrow in
place rather than stacking a duplicate (the name defaults to the endpoints, so an
unnamed re-connect of the same two also replaces):

```python
a = canvas.geo(x=40,  y=40, w=160, h=80, geo="rectangle", text="A")
b = canvas.geo(x=40, y=200, w=160, h=80, geo="rectangle", text="B")
arrow = canvas.connect(a, b, text="A→B", color="blue", dash="dashed", bend=40)
arrow.text = "retry"                              # live caption change
arrow.update(color="red", arrowhead_end="diamond")
canvas.disconnect(arrow)                          # by object or by name
```

| Arrow prop | Values |
|---|---|
| `text` | caption drawn on the arrow (live-writable; omit for none) |
| `color` | same palette as shapes (black, blue, grey, orange, red, …) |
| `dash` | draw / solid / dashed / dotted |
| `size` | s / m / l / xl |
| `bend` | curve amount (number; `0` = straight) |
| `arrowhead_start` / `arrowhead_end` | none / arrow / triangle / square / dot / pipe / diamond / inverted / bar |
| `name` | identity / `canvas.<name>` handle; re-connecting under it replaces in place |

See `examples/managed_shapes.py`.

**Observing user drawings.** User freehand is ephemeral (Python can't pre-place
it), but `on_draw` fires whenever viewers draw, move, or delete shapes, and
`canvas.drawings` is a live `{id: DrawingShape}` snapshot Python can mutate:

```python
@canvas.on_draw
def tidy(event):   # {"added": [...], "updated": [...], "removed": [id, ...]}
    for s in event["added"]:
        if s.type == "draw" and s.color == "red":
            s.update(color="blue")   # recolour user strokes live
```

See `examples/managed_shapes.py`.

# Views, navigation & roles

## The view

Pass a `view` dict to `serve` (all keys optional), and change it live with
`set_view`:

| Key | Effect |
|---|---|
| `x`, `y`, `zoom` | initial camera (centre on `(x, y)` at `zoom`; 1.0 = 100%) |
| `locked` | `True` freezes pan/zoom (kiosk) |
| `min_zoom`, `max_zoom` | clamp zoom range |
| `ui` | `False` hides the editor chrome **and** the Inspector button |
| `grid` | `True` shows the background grid |
| `read_only` | `True` blocks freehand drawing |
| `navigation` | `'free'` (default), `'scroll_y'`, `'scroll_x'`, or a `(mode, zoom)` tuple |

```python
canvas.serve(view={"x": 200, "y": 160, "zoom": 1.0, "locked": True, "ui": False})

canvas.set_view(ui=False)                                  # change live; only keys you pass
canvas.set_view(navigation='scroll_y')                     # wheel scrolls instead of zooming
canvas.set_view(read_only=True, ui=False, roles=["user"])  # scope to a login role
canvas.set_view(x=0, y=0, zoom=1.5, client_id=some_id)     # one viewer only
```

Omit `x`/`y`/`zoom` and each viewer opens framed on the panels they can see. A
constrained `navigation` mode makes the scroll wheel **pan** the free axis and
locks the other axis + the zoom — useful for vertical dashboards / horizontal
timelines; it persists across reloads.

Two toolbar buttons (bottom-left, local-bind default): **Inspector** spawns an
ephemeral state browser on demand; **Graveyard** lists panels a user deleted and
restores them without a restart (Python kept them alive). Override with
`serve(ui_inspector=…, ui_graveyard=…)`.

## Roles: one rule for everything per-viewer

Pass `serve(passwords={role: pw})` and each viewer logs in as a **role**. Everyone
renders the same canvas, but you layer **per-viewer overrides** on top. One rule:

> **Precedence is `shared < role < client`.** Omit the scope to set the shared
> value (everyone); pass `roles=` and/or `client_id=` to override it for those
> viewers. Overrides persist and replay on reconnect.

The same `roles=`/`client_id=` scoping works across four axes:

| Axis | shared | scoped |
|---|---|---|
| **Exists?** (visibility) | `react(..., roles=[])` = all | `roles=["admin"]` on the factory; `add_role`/`remove_role` live |
| **Content** | `panel.update(**props)` | `panel.update(roles=…, client_id=…, **props)` |
| **Layout** | factory `x/y/w/h` / `set_layout(...)` | `set_layout(roles=…, client_id=…, ...)` |
| **View** | `canvas.set_view(...)` | `canvas.set_view(roles=…, client_id=…, ...)` |

```python
controls = canvas.react(CONTROLS_SRC, name="controls", roles=["admin"])  # admins only
speed    = canvas.slider("speed", min=0, max=100, lock_for=["viewer"])    # all see, admins drag

@controls.on_message
def on_action(msg, viewer):           # viewer.role is server-trusted
    print(f"{viewer['name']} ({viewer['role']}) sent {msg}")

canvas.serve(port=8000, host="0.0.0.0",
             passwords={"admin": "secret-admin-pw", "viewer": "public-view-pw"})
```

Roles can be created **after** the server starts (the `passwords=` dict is read
live on each login); reveal a panel to a runtime role with `panel.add_role(name)`
(`remove_role` to hide; `panel.roles` reads the allowlist). When a user drags a
panel, the change writes back to whichever layer their layout came from. See
[`examples/hackathon/hackathon.py`](examples/hackathon/hackathon.py) for a full
JSON-backed app where the admin creates teams (each with its own password and
budget) on the fly.

# Serving & sharing

`canvas.serve(port=8000)` opens the browser and blocks. All its options:

| Option | Default | What it does |
|---|---|---|
| `port` | `8000` | TCP port |
| `host` | `"127.0.0.1"` | bind address; `"0.0.0.0"` exposes on the LAN |
| `open_browser` | `True` | open the system browser on start |
| `block` | `True` | block until shutdown; `False` returns at once for live inserts (notebooks) |
| `wait` | `True` | in background mode, wait until the loop is ready before returning |
| `password` | – | gate the whole canvas behind one password (session cookie) |
| `passwords` | – | `{role: password}` for role-based access |
| `login_message` | – | host note on the password page |
| `tunnel` | `False` | expose publicly through a tunnel |
| `tunnel_provider` | `"cloudflared"` | `"cloudflared"` / `"localtunnel"` |
| `persist` | `False` | auto-save/restore the canvas; `True` or a path |
| `hot_reload` | `False` | restart the process when a `.py` changes (script entry only) |
| `view` | – | camera & chrome dict |
| `cursors` | auto¹ | viewers report pointer position |
| `ui_inspector` | auto¹ | toolbar button to spawn an Inspector |
| `ui_graveyard` | auto¹ | toolbar button to restore deleted panels |
| `desktop` | auto² | open a native window (pywebview) instead of the browser |
| `window_title` / `window_size` | `"danvas"` / `(1200, 800)` | native-window caption / size |
| `tldraw_license_key` | — | **deprecated/ignored** (the frontend is tldraw-free; kept for backwards compatibility) |
| `debug` | `False` | log every WebSocket frame to the console |

¹ on by default only for a private local bind (loopback, no tunnel). ² on by
default only inside a baked executable (`sys.frozen`).

**LAN** — `serve(host="0.0.0.0")` prints the network URL for other devices.

**Password** — gate any shared canvas; viewers see a password page once, then a
session cookie carries them (the password is never stored in the cookie). A
protected canvas also gets a **Sign out** button and an optional `login_message=`
note (shown plain — don't put a secret in it):

```python
canvas.serve(port=8000, host="0.0.0.0",
             passwords={"admin": "secret-admin-pw", "viewer": "view"},
             login_message='Spectators enter "view"; teams enter your given password.')
```

**Tunnel** — expose to the internet over HTTPS; the bind stays on `127.0.0.1` and
a shareable `https://…` URL is printed. `[tunnel]` downloads & caches cloudflared
on first use; the tunnel closes with the server.

```python
canvas.serve(port=8000, tunnel=True)
```

**Hot reload** — `serve(hot_reload=True)` (run as `python your_script.py`)
reloads when you save a `.py` in the script's folder; the tab reconnects on its
own, and a broken save is pre-flighted and skipped. Needs `block=True`. Under a
tunnel, the public URL and viewers' sessions survive each edit. When a save
changed **only the bodies of top-level functions** — fixing what a handler does,
tweaking a helper — the worker swaps those functions in place *without
restarting*, so its heap, daemon threads, open connections, and in-memory state
(a half-trained model, accumulated data) are preserved; the new code runs the
next time the handler fires. Anything else (a new import, a changed signature, a
new panel, an edit to a running `@canvas.background` loop) falls back to a full
restart. To keep training-style state hot across edits, factor the changing work
into a per-iteration `step()` the loop calls — editing `step` is a live swap,
while editing the loop scaffold restarts it.

By default only top-level `.py` files are watched; pass `watch=` to also watch
other files — a glob or list of globs relative to the script's folder
(`serve(hot_reload=True, watch=["*.jsx", "panels/**/*.css"])`). A change restarts
the worker, which re-reads files loaded via `path=` (e.g. a
`canvas.react(path="panel.jsx")`). For a single panel, `panel.watch()`
live-reloads its JSX/CSS *without* a restart; `watch=` is the whole-process
version for arbitrary assets.

**Background workers** — register producer loops (camera, sensor, telemetry) with
`@canvas.background`; `serve()` runs each on a daemon thread *in the serving
process only*. Prefer this over a hand-started thread when using `hot_reload`, so
a single-owner resource isn't double-grabbed across a restart.

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

**Notebooks** — `serve(block=False)` returns immediately so later cells edit the
open canvas; handlers and `threaded=True` keep working (they fire on UI events,
not on which cell ran). `canvas.capture_cells(cols=2)` mirrors every expression
cell to an auto-arranged panel (via `show()`), swapping in place on re-run; a
`# danvas: x=40 w=600 locked=true` directive overrides one cell (or `# danvas:
skip`). In a plain script, call `canvas.wait()` to keep the daemon server alive.

```python
canvas = danvas.Canvas().serve(port=8000, block=False)
servo = canvas.slider("servo_1", min=0, max=180, default=90)   # appears live
```

**Merging canvases** — a *merge host* connects to several running canvases (as a
client), composites their panels onto one port, and routes interactions back to
the owning canvas — computation stays sharded:

```bash
python -m danvas.merge :8001 :8002 host3:8003 --port 8080
```

```python
from danvas import Merge
Merge([8001, 8002]).serve(port=8080)
```

Sources overlay by default (`region_width` spreads them side-by-side); a source
going offline drops its panels until it reconnects. Free-form drawings aren't
composited.

# Persistence, inspection & packaging

## Saving & loading

```python
canvas.save("board.json")                    # browser must be open to capture drawings
# next run, recreate the panels (same names), then:
canvas.load("board.json")                    # snaps panels into place + restores values + drawings
canvas.load("board.json", formation=False)   # drawings only

fut = canvas.save("board.json", blocking=False)   # non-blocking (Jupyter); fut.result() to wait
```

Saved is the canvas's **state**, not its code: each panel's placement/lock state,
the values a user set on input controls (sliders, toggles, text fields), and the
free-form drawings. The panels themselves come back by re-running your script
(behaviour is code), matched to the saved state by name.

**Automatic persistence** — `serve(persist=...)` is the hands-off twin: it loads
the saved state on startup and re-saves on every change, so a canvas survives
restarts with no `save`/`load` of your own.

```python
canvas.serve(persist=True)              # <script>.canvas.json next to your script
canvas.serve(persist="board.json")      # or choose the file
```

On startup each panel snaps back to where it was dragged *and* to the value the
user last set it to; their drawings reappear. The file is rewritten (debounced) on
every move/resize/draw/value-change and once more on a clean shutdown. Delete a
panel from your script and its stale saved state is ignored.

## Inspecting & screenshotting (LLM feedback loop)

Two read-only methods let a script — or an LLM editing one — verify the UI it
built matches the request:

```python
canvas.describe()                       # plain-data inventory: one dict per panel/arrow
                                        # — type, value, x/y/w/h, visible, locked
canvas.screenshot(path="canvas.png")    # whole canvas → PNG (bytes returned)
canvas.screenshot(slider)               # one panel; or [a, b] framed to their bounds
```

`describe()` is the cheap text half (confirm the right components exist, are
wired, laid out, holding expected values — no pixels). `screenshot()` is the
visual half for a VLM. Both round-trip to a connected browser, so `screenshot()`
needs an open tab. It's a *scene* export (shapes at canvas coords, camera-
independent), so it renders native panels faithfully but comes out blank for
sandboxed iframes (`Custom`, `WebView`) and for continuously-streaming canvases
mid-frame (`VideoFeed`, a live `LivePlot`); for those, drive a browser tool at the
served URL.

**From outside the process** — the running server exposes the same checks as
HTTP, behind the auth gate:

```bash
curl localhost:8000/__describe__        # JSON inventory — works with no tab open
curl localhost:8000/__screenshot__.png -o canvas.png   # PNG; needs a tab to render (503 if none)
```

## Tracking an ML training run

The panels *are* the dashboard — no logging framework. Make each once, keep the
handle, push from your loop. Being bidirectional, the same loop can read controls
TensorBoard can't (a pause button, a live LR slider). `live_plot`/`histogram` need
`plotly`.

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

See [`examples/train_dashboard.py`](examples/train_dashboard.py).

## Packaging a desktop app (`bake`)

Ship a canvas as a self-contained executable — no Python/browser/pip on the
target. `bake()` bundles your script + backend + pre-built frontend (PyInstaller)
into an app that runs in a native window (pywebview). Needs `[desktop]`.

```python
canvas.bake(name="RobotConsole")     # also: window_size=, icon=, onefile=, distpath=
```

- `python your_script.py` → **builds** `dist/RobotConsole(.exe)`.
- launching that exe → **runs** the script in a window (rebuild skipped when frozen).

Only packages your script imports are bundled (heavy optional deps only when their
component is used). Build without editing the script via
`python -m danvas.bake your_script.py --name RobotConsole`; force-add a dynamic
import with `include=[...]`, skip a build-breaking dep with `exclude=[...]`.
`serve(desktop=True)` opens the same native window in development.

# How it works

danvas is two halves joined by **one WebSocket**: your Python process, and a
pre-built browser frontend (a custom [Preact](https://preactjs.com) canvas, with
React panels) it serves. You never touch the frontend — it ships compiled in the
package, no Node or build step. The backend is ~660 kB of pure Python over four
dependencies (FastAPI, uvicorn, websockets, orjson); the browser page loads
~0.1 MB gzipped, with Plotly fetched on demand only when you add a chart.

**The model: Python owns state, the browser renders it.** Each panel is a Python
*component* with a unique id. The **bridge** turns your calls into small JSON
frames — `register` (a panel appeared), `update` (its state changed), `remove` —
and ships them to every browser, where each component is a canvas *panel*:
built-ins are native React widgets, `custom` is a sandboxed iframe, `react` is
your JSX compiled in-browser (Sucrase) and mounted as a real React subtree. User
actions travel back as `input` / `layout` frames.

**Replay is why reconnects "just work".** The browser holds no source of truth, so
on every (re)connect the bridge replays the full state — each panel's `register` +
current payload — filtered by the viewer's role. The same path powers hot reload
(a fresh worker replays everything) and the per-viewer model: a panel's
props/layout are the shared base plus any role/client **overlays**, merged at
replay and pushed live to matching viewers via `send_to_role` / `send_to_client`.

**Threading.** An asyncio event loop owns the socket; your handlers run on a single
ordered worker thread, so a slow handler never freezes rendering (offload
genuinely slow work with `threaded=True`). High-rate media (`VideoFeed`,
`AudioFeed`, `push_binary`) skips JSON on a binary frame.

**Auth & sharing.** Roles come from the login password, carried in a signed
session cookie (no server-side store), so a viewer stays logged in across
reconnects and restarts. Under `hot_reload`, a long-lived monitor process re-execs
the worker on each save and owns the tunnel + cookie secret, so the public URL and
sessions survive edits.

**Where to look:** `danvas/canvas.py` (the `Canvas` façade + factories),
`danvas/bridge.py` (wire / replay / per-viewer sends), `danvas/server.py` (FastAPI
+ auth), `danvas/components/` (the panels), `danvas/frontend/src/bridge.js` (the
browser side).

## Debugging the wire

Everything is JSON frames over one WebSocket, so "why didn't it update" is always
"is the frame on the wire?".

```python
canvas.serve(debug=True)        # log every frame: -> out (Python), <- in (browser)

@canvas.on_frame                # programmatic tap; fn(direction, msg)
def log(direction, msg):
    print(direction, msg["type"], msg.get("id"))
```

`on_frame` watches the wire; `on_dispatch` watches *which handler ran*. As each
input/layout handler is queued, starts, and finishes (or errors), the tap gets a
trace event — `comp`, `handler` (name + `file:line`), `mode`
(`inline`/`threaded`/`dedicated`), `phase`, and `dur_ms` — with all the handlers
one action fans out to sharing a `trace` id. Threaded handlers report from their
own thread, so concurrent runs are visible. It's the data behind a "yellow while
running, green when done" view, and it's off (zero cost) until a tap is added.

```python
@canvas.on_dispatch
def _(e):
    print(e["phase"], e["handler"], e.get("dur_ms"))   # queued / start / done
```

For an on-canvas view instead of a tap, `canvas.trace()` drops in a live,
back-traceable panel (also launchable from the Inspector's **Trace** button) that
groups each action's handlers, colours them amber→green→red, lists the living
background threads, and gives each action a **copy** button. `canvas.trace_calls()`
turns on *deep* tracing: the trace then follows each handler **into your own
functions** (not danvas/stdlib/package internals), indented by call depth, so you
see which function called which. Deep tracing has a real cost, so it's opt-in and
meant for development.

From the moment a canvas starts serving, the last 50 actions are recorded into a
ring buffer whether or not a panel is open — so a trace panel opened *after*
something happened shows it, and `canvas.trace_history()` returns the recent
actions (each `{trace, comp, event, frames}`) for after-the-fact or scripted
debugging. The background recording is shallow; turn on `trace_calls()` for the
nested detail.

Connection lines always print. Stale tabs heal themselves: panel ids are minted
per run, and a tab from an earlier run drops the old panels and replays the new
ones — re-running never leaves duplicates.

All JSON at `ws://localhost:{port}/ws`:

```json
{ "type": "register", "id": "<id>", "component": "Slider", "props": {…}, "x": 80, "y": 80 }
{ "type": "update",   "id": "<id>", "payload": { "value": 120 } }
{ "type": "remove",   "id": "<id>" }
{ "type": "input",    "id": "<id>", "payload": { "value": 120 } }
```

High-rate media skips JSON: a binary frame of `[type][id-length]` + id + raw
payload (JPEG / int16 PCM), fed straight into a `Blob`/`ArrayBuffer`. The same
format travels both ways — server→browser for `push_binary`/`VideoFeed`/
`AudioFeed`, and browser→server for `canvas.sendBinary()` /
`requestCamera()` / `requestMicrophone()` — all routed to `@panel.on_binary`. The
canonical protocol lives in `danvas/_protocol.py`.

# Examples

```bash
python examples/hello_world.py            # slider + label
python examples/frontend_backend_tour.py  # interactive tour of the wire, live frame tap
python examples/sensor_dashboard.py       # live VideoFeed + worker thread
python examples/show_anything.py          # canvas.show() over every type
python examples/custom_html.py            # hand-written bidirectional HTML panel
python examples/custom_binary_stream.py   # high-rate binary telemetry (push_binary)
python examples/managed_shapes.py          # managed shapes + on_draw observer
python examples/binary_input_test.py      # webcam → Python via canvas.requestCamera
python examples/audio_input_test.py       # microphone → Python via canvas.requestMicrophone
python examples/react_canvas_api.py       # React: canvas.viewport / setView / chat
python examples/matplotlib_panel.py       # slider re-renders a matplotlib figure
python examples/plotly_panel.py           # interactive Plotly chart
python examples/robot_control.py          # sliders, toggle, plot, video together
python examples/download_button.py        # download a host file / generated data
python examples/upload_button.py          # upload a file from the browser to Python
python examples/chat_room.py              # shared chat with editable names
python examples/moving_widget.py          # per-viewer cursor-following emoji
python examples/public_tunnel.py          # share worldwide via HTTPS tunnel
python examples/train_dashboard.py        # TensorBoard-style training tracker
python examples/hackathon/hackathon.py    # roles, per-team budgets, runtime teams
```

Notebooks: `examples/notebook_dynamic.ipynb` (live add/move/remove),
`examples/notebook_autopanel.ipynb` (cell capture),
`examples/merge_canvases.ipynb` (two canvases on one merge host). The
matplotlib/plotly examples need `pip install matplotlib plotly`.

# Developing the frontend

The built bundle lives in `danvas/frontend/dist/` and is committed. Rebuild:

```bash
cd danvas/frontend
npm install
npm run build          # or: npm run dev + http://localhost:5173/?demo for standalone UI work
```

# Licence

danvas's own source code is under the
[GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-or-later). Commercial
licences — which waive the AGPL copyleft for internal or proprietary use — are
available on request via daniel.chahine004@gmail.com.

**Scope.** The AGPL covers danvas's own code. The pre-built frontend bundle in
`danvas/frontend/dist/` is compiled from third-party packages under *their*
licences — all **permissive (MIT)**, built on [Preact](https://preactjs.com)
(the [Inter](https://rsms.me/inter/) typeface is under the SIL Open Font License).
See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

**No frontend licence key is required.** The frontend is fully open and permissive
— there is no proprietary component, no production licence key, and no watermark.
Run danvas in development or production freely; only danvas's own AGPL terms apply
to danvas's code.

> Older releases bundled [tldraw](https://tldraw.dev) (proprietary licence, with a
> production key and "made with tldraw" watermark). The frontend has been rewritten
> to be tldraw-free, so that requirement no longer applies. `serve(tldraw_license_key=…)`
> is accepted but ignored (kept for backwards compatibility).
