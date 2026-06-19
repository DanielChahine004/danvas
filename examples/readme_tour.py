"""
readme_tour.py — An interactive canvas that walks through the README in order.

Each panel corresponds to a section heading. Scroll down to move through them.
Run with:  python examples/readme_tour.py
"""
import math
import time
import pycanvas
from pycanvas import React as _React

canvas = pycanvas.Canvas()

W = 660   # main column width
GAP = 24  # section gap

with canvas.column(w=W, gap=GAP, origin=(40, 40)):

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Intro
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    title = canvas.markdown("""# PyCanvas
A browser-based spatial canvas controlled entirely from Python.
Panels are **bidirectional** — Python pushes data to them and reads user
input back in real time over one WebSocket.

Built on tldraw + React (frontend) and FastAPI + WebSockets (backend).
The frontend ships pre-built; you never touch Node or npm.

> Scroll down — each section matches a README heading.
""", name="intro")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Install
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    install = canvas.markdown("""## Install
```bash
pip install dans-pycanvas
```
Optional extras: `[video]`  `[audio]`  `[tunnel]`  `[desktop]`
""", name="install")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Hello World
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    hw_hdr = canvas.markdown("## Hello World", name="hw_hdr")

    servo = canvas.slider("servo_1", min=0, max=180, default=90, label="Servo 1", w=320)
    hw_status = canvas.label("hw_status", "idle", right_of=servo, gap=GAP, w=276)

    @servo.on_change
    def _(v):
        hw_status.update(f"servo at {v:.0f}°")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Mental model  (sits just before §1 in the README)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    mental = canvas.markdown("""## Mental model
The lifecycle is always the same five steps:

1. `Canvas()` — make a canvas
2. `canvas.slider(...)` / `canvas.label(...)` / … — make panels
3. `panel.set_layout(x=, y=, w=, h=)` — place/size *(optional)*
4. `canvas.set_view(zoom=, ui=, ...)` — camera & chrome *(optional)*
5. `canvas.serve(port=8000)` — opens the browser, blocks

**Python owns all state; the browser renders it and reports user actions.**
""", name="mental_model")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §1 The Canvas
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    s1 = canvas.markdown("""# 1. The Canvas
`Canvas()` is the document everything hangs off.
Reach panels by name (`canvas.my_panel` / `canvas["my_panel"]`),
connect them with arrows, or `clear()` / `save()` / `load()` the formation.
""", name="section_1")

    node_a = canvas.label("node_a", "Panel A", w=200)
    node_b = canvas.label("node_b", "Panel B", right_of=node_a, gap=240, w=200)
    canvas.connect(node_a, node_b, text="canvas.connect(a, b)", color="blue")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §2 Components
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    s2 = canvas.markdown("""# 2. Components
`canvas.<factory>(...)` builds a panel and returns the handle.
Input panels (`slider`, `toggle`, `button`, `text_field`) report user actions.
Output panels (`label`, `image`, `markdown`, `plot`, `live_plot`) receive `.update()`.
Bidirectional: `table`, `chat`, `react`, `custom`.
""", name="section_2")

    speed_sl   = canvas.slider("speed",   min=0, max=100, default=50, label="Speed",   w=200)
    enabled_tg = canvas.toggle("enabled", ["off", "on"], default="on", label="Enabled",
                                right_of=speed_sl, gap=16, w=160)
    reset_bt   = canvas.button("reset",   text="Reset", right_of=enabled_tg, gap=16, w=112)
    comp_out   = canvas.label("comp_out", "interact above ↑")

    @speed_sl.on_change
    def _(v):
        comp_out.update(f"speed={v:.0f}  enabled={enabled_tg.value}")

    @enabled_tg.on_change
    def _(v):
        comp_out.update(f"speed={speed_sl.value:.0f}  enabled={v}")

    @reset_bt.on_click
    def _():
        speed_sl.update(50)
        enabled_tg.update("on")
        comp_out.update("reset ↺")

    # --- The three data verbs ---
    verbs = canvas.markdown("""## The three data verbs
| Verb | Means | Replayed on reconnect? | Panels |
|---|---|:---:|---|
| `.update(value)` | **replace** whole state | ✅ | Label, Table, Slider, … |
| `.push(sample)` | **append** one point | ❌ | LivePlot, Custom, React |
| `.add(values, step)` | **record** a distribution snapshot | ✅ | Histogram |
""", name="data_verbs")

    # --- Receiving input ---
    input_hdr = canvas.markdown("""## Receiving input
`@panel.on_change` / `@button.on_click` / `@table.on_select` / `@panel.on_layout`

Any handler may declare a trailing `viewer` arg to see who acted:
`def _(value, viewer):` — gives `viewer["name"]`, `viewer["role"]`, …
""", name="input_hdr")

    name_fld = canvas.text_field("visitor_name", label="Your name",
                                  placeholder="type here and press Enter…", w=300)
    greeting  = canvas.label("greeting", "Enter your name →", right_of=name_fld, gap=GAP, w=296)

    @name_fld.on_change
    def _(text, viewer):
        greeting.update(f"Hello, {text or viewer['name']}!")

    # --- Show anything ---
    show_hdr = canvas.markdown("""## Show anything
`canvas.show(value)` inspects the value and inserts the best panel automatically —
like a notebook deciding how to render an `Out[...]`, but works in plain scripts.
""", name="show_hdr")

    show_dict = canvas.show({"status": "ok", "temp": 42.1, "rpm": 1200},
                             name="show_dict", w=310)
    canvas.show("# Heading\n`canvas.show()` rendered this **Markdown** from a string.",
                 name="show_md", right_of=show_dict, gap=16, w=310)

    # --- Table ---
    table_hdr = canvas.markdown("""## Table — `canvas.table(data)`
Accepts a DataFrame, list-of-dicts, or plain dict.
`@table.on_select` fires with the selected row indices; `table.selected` is always live.
""", name="table_hdr")

    _catalog = [
        {"Component": "slider",     "Direction": "→ Python",   "Key API": "on_change(v)"},
        {"Component": "toggle",     "Direction": "→ Python",   "Key API": "on_change(v)"},
        {"Component": "button",     "Direction": "→ Python",   "Key API": "on_click()"},
        {"Component": "text_field", "Direction": "→ Python",   "Key API": "on_change(text)"},
        {"Component": "label",      "Direction": "Python →",   "Key API": "update(value)"},
        {"Component": "markdown",   "Direction": "Python →",   "Key API": "update(text)"},
        {"Component": "image",      "Direction": "Python →",   "Key API": "update(src)"},
        {"Component": "live_plot",  "Direction": "Python →",   "Key API": "push({trace: y})"},
        {"Component": "table",      "Direction": "↔ both",     "Key API": "update(data) / on_select(idxs)"},
        {"Component": "react",      "Direction": "↔ both",     "Key API": "push(value) / on_message(msg)"},
        {"Component": "chat",       "Direction": "↔ both",     "Key API": "send(msg) / on_message(msg)"},
        {"Component": "show",       "Direction": "Python →",   "Key API": "auto-detects best panel"},
    ]
    demo_table = canvas.table(_catalog, name="component_catalogue",
                               label="Component catalogue", h=280)

    tbl_sel = canvas.label("tbl_sel", "Select a row to see its component →")

    @demo_table.on_select
    def _(indices):
        if not indices:
            tbl_sel.update("Select a row to see its component →")
        else:
            row = _catalog[indices[0]]
            tbl_sel.update(f"{row['Component']}  ·  {row['Direction']}  ·  {row['Key API']}")

    # --- Frameless panels ---
    frameless_hdr = canvas.markdown("""## Frameless panels — `frame=False`
Pass `frame=False` to `canvas.insert()` (or any factory) to strip the card chrome.
Add `grabbable=False` to make the panel behave like pure ambient content.
""", name="frameless_hdr")

    fl_label = canvas.label("fl_label",
                             "✦  No card border, no title bar — just content floating on the canvas.",
                             frame=False, grabbable=False)

    # Uiverse radio-button widget converted from styled-components → plain React
    _radio_raw = """
import React, { useState } from 'react';
import styled from 'styled-components';

const StyledWrapper = styled.div`
  .radio-inputs {
    display: flex;
    flex-wrap: wrap;
    border-radius: 0.5rem;
    background-color: #EEE;
    box-sizing: border-box;
    padding: 0.25rem;
    gap: 0.25rem;
  }
  .radio-inputs .radio {
    flex: 1 1 auto;
    text-align: center;
  }
  .radio-inputs .radio input {
    display: none;
  }
  .radio-inputs .radio .name {
    display: flex;
    cursor: pointer;
    align-items: center;
    justify-content: center;
    border-radius: 0.5rem;
    border: none;
    padding: .5rem 0;
    color: #666;
    transition: all .15s ease-in-out;
    font-size: 13px;
    font-weight: 600;
  }
  .radio-inputs .radio input:checked + .name {
    background-color: #fff;
    color: #222;
    font-weight: 700;
    box-shadow: 0 1px 4px rgba(0,0,0,.15);
  }
`;

const RadioCard = () => {
  const [selected, setSelected] = React.useState('slider');
  const options = ['slider', 'label', 'table', 'react'];
  return (
    <StyledWrapper>
      <div className="radio-inputs">
        {options.map(opt => (
          <label className="radio" key={opt}>
            <input type="radio" name="demo" value={opt}
                   checked={selected === opt}
                   onChange={() => setSelected(opt)} />
            <span className="name">{opt}</span>
          </label>
        ))}
      </div>
    </StyledWrapper>
  );
};

export default RadioCard;
"""
    _radio_src = _React.from_uiverse(_radio_raw)

    radio_panel = canvas.react(source=_radio_src, name="radio_widget",
                                label="Uiverse radio (frame=False)",
                                h=80, frame=False, grabbable=False)

    # --- React panels ---
    react_hdr = canvas.markdown("""## React panels
`canvas.react(source=...)` compiles your JSX in-browser — no npm, inherits the canvas theme.
`canvas.send({...})` posts up to Python; `panel.push(data)` sends down as the `value` prop.
""", name="react_hdr")

    _count = 0
    counter_panel = canvas.react(
        source="""
function Component({ canvas, value }) {
  const n = value ?? 0;
  return (
    <div style={{padding:20, textAlign:'center'}}>
      <div style={{fontSize:52, fontWeight:700, lineHeight:1}}>{n}</div>
      <div style={{marginTop:12, display:'flex', gap:8, justifyContent:'center'}}>
        <button onClick={() => canvas.send({d: 1})}
                style={{padding:'4px 20px', fontSize:14}}>+1</button>
        <button onClick={() => canvas.send({d: -1})}
                style={{padding:'4px 20px', fontSize:14}}>−1</button>
        <button onClick={() => canvas.send({d: 'reset'})}
                style={{padding:'4px 20px', fontSize:14}}>Reset</button>
      </div>
    </div>
  );
}
""",
        name="counter", label="React counter", w=240, h=140,
    )

    @counter_panel.on_message
    def _(msg):
        global _count
        d = msg.get("d", 0)
        _count = 0 if d == "reset" else _count + d
        counter_panel.push(_count)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §3 Layout
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    s3 = canvas.markdown("""# 3. Layout
`x`/`y` = canvas coords · `w`/`h` = pixels. Omit → auto-arrange.
Relative: `below=`, `right_of=`, `left_of=`, `above=` (+ `gap=`).
Containers: `canvas.grid(cols=N)` · `canvas.column()` · `canvas.row()`.
`column.refit()` re-packs after a member panel grows.
""", name="section_3")

    # 3×2 grid of labels demonstrating grid-like layout
    ga = canvas.label("ga", "grid slot 1", w=200)
    gb = canvas.label("gb", "grid slot 2", right_of=ga, gap=12, w=200)
    gc = canvas.label("gc", "grid slot 3", right_of=gb, gap=12, w=200)
    gd = canvas.label("gd", "grid slot 4", w=200)
    ge = canvas.label("ge", "grid slot 5", right_of=gd, gap=12, w=200)
    gf = canvas.label("gf", "grid slot 6", right_of=ge, gap=12, w=200)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §4 Views & Navigation
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    s4 = canvas.markdown("""# 4. Views & Navigation
Pass `view=` to `serve()` or call `canvas.set_view()` live.
Keys: `x`, `y`, `zoom`, `locked`, `ui`, `grid`, `read_only`, `min_zoom`, `max_zoom`.
Scope to a role or single client with `roles=` / `client_id=`.
""", name="section_4")

    zoom_in_bt  = canvas.button("zoom_in",  text="Zoom in (1.5×)",  w=180)
    zoom_out_bt = canvas.button("zoom_out", text="Zoom out (0.5×)", right_of=zoom_in_bt,  gap=12, w=180)
    zoom_rst_bt = canvas.button("zoom_rst", text="Zoom 100%",       right_of=zoom_out_bt, gap=12, w=140)

    @zoom_in_bt.on_click
    def _(): canvas.set_view(zoom=1.5)

    @zoom_out_bt.on_click
    def _(): canvas.set_view(zoom=0.5)

    @zoom_rst_bt.on_click
    def _(): canvas.set_view(zoom=1.0)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §5 Serving & Sharing
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    s5 = canvas.markdown("""# 5. Serving & Sharing
```python
canvas.serve(port=8000)                          # local, blocks
canvas.serve(host="0.0.0.0")                     # LAN — prints the network URL
canvas.serve(password="let-me-in")               # password-gated; session cookie
canvas.serve(passwords={"admin": "pw", ...})     # role-based access
canvas.serve(tunnel=True)                        # public HTTPS via cloudflared
canvas.serve(hot_reload=True)                    # restart on .py save; tab reconnects
canvas.serve(persist=True)                       # auto-save/restore placement
canvas.serve(namespace=globals())                # share script globals with Inspector
```

**Roles** — `serve(passwords={role: pw})` gates access per role.
The same `roles=` / `client_id=` scoping then applies to panel visibility,
content, layout, and view — precedence is `shared < role < client`.
""", name="section_5")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Beyond the five steps — live telemetry
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    beyond = canvas.markdown("""# Beyond the five steps
Live streaming telemetry, ML training dashboards, desktop packaging (`bake()`),
hot reloading, notebooks (`block=False`), canvas merging, and more.

## Live plot — `canvas.live_plot()`
`push({trace: y}, x=step)` appends one point per call. The server coalesces
frames a slow client can't keep up with; `.push([batch], x=[xs])` flushes
many points at once.
""", name="beyond")

    lp = canvas.live_plot("telemetry", traces=["sin", "cos"],
                           label="Live telemetry", h=220)

    @canvas.background
    def _stream():
        t = 0.0
        while True:
            lp.push({"sin": math.sin(t), "cos": math.cos(t)}, x=round(t, 2))
            t += 0.1
            time.sleep(0.05)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Inspector
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    insp_hdr = canvas.markdown("""## Inspector
A live variable/panel explorer. Switch between **panels** (component state +
geometry) and **globals** (script namespace). Also spawnable on demand via the
🔍 toolbar button.
""", name="insp_hdr")

    canvas.inspector(name="readme_inspector", label="Inspector",
                     source="components", refresh=2.0, h=280)

canvas.serve(hot_reload=True, namespace=globals(),
             view={"x": 375, "y": 230, "zoom": 1.8})
