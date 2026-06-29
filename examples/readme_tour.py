"""
readme_tour.py — An interactive canvas that walks through the README in order.

Each panel corresponds to a section heading. Scroll down to move through them.
Run with:  python examples/readme_tour.py
"""
import math
import time
import danvas

canvas = danvas.Canvas()

W   = 660   # main column width
GAP = 24    # section gap

col = canvas.column(x=40, y=40, w=W, gap=GAP)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Intro
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""# danvas
A browser-based spatial canvas controlled entirely from Python.
Panels are **bidirectional** — Python pushes data to them and reads user
input back in real time over one WebSocket.

Built on a custom Preact canvas (frontend) and FastAPI + WebSockets (backend).
The frontend ships pre-built; you never touch Node or npm.

> Scroll down — each section matches a README heading.
""", name="intro"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Install
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""## Install
```bash
pip install dans-danvas
```
Optional extras: `[video]`  `[audio]`  `[tunnel]`  `[desktop]`
""", name="install"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Hello World
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("## Hello World", name="hw_hdr"))

with col.row(gap=GAP):
    servo     = canvas.slider("servo_1", min=0, max=180, default=90,
                               label="Servo 1", w=320)
    hw_status = canvas.label("hw_status", "idle", w=276)

@servo.on_change
def _(v):
    hw_status.update(f"servo at {v:.0f}°")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mental model
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""## Mental model
The lifecycle is always the same five steps:

1. `Canvas()` — make a canvas
2. `canvas.slider(...)` / `canvas.label(...)` / … — make panels
3. `panel.set_layout(x=, y=, w=, h=)` — place/size *(optional)*
4. `canvas.set_view(zoom=, ui=, ...)` — camera & chrome *(optional)*
5. `canvas.serve(port=8000)` — opens the browser, blocks

**Python owns all state; the browser renders it and reports user actions.**
""", name="mental_model"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §1 The Canvas
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""# 1. The Canvas
`Canvas()` is the document everything hangs off.
Reach panels by name (`canvas.my_panel` / `canvas["my_panel"]`),
connect them with arrows, or `clear()` / `save()` / `load()` the formation.
""", name="section_1"))

with col.row(gap=10*GAP):
    node_a = canvas.label("node_a", "Panel A", w=W // 2 - GAP // 2)
    node_b = canvas.label("node_b", "Panel B", w=W // 2 - GAP // 2)
canvas.connect(node_a, node_b, text="canvas.connect(a, b)", color="blue")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §2 Components
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""# 2. Components
`canvas.<factory>(...)` builds a panel and returns the handle.
Input panels (`slider`, `toggle`, `button`, `text_field`) report user actions.
Output panels (`label`, `image`, `markdown`, `plot`, `live_plot`) receive `.update()`.
Bidirectional: `table`, `chat`, `react`, `custom`.
""", name="section_2"))

with col.row(gap=GAP):
    speed_sl   = canvas.slider("speed",   min=0, max=100, default=50,
                                label="Speed", w=300)
    enabled_tg = canvas.toggle("enabled", ["off", "on"], default="on",
                                label="Enabled", w=160)
    reset_bt   = canvas.button("reset",   text="Reset", w=112)

comp_out = col.add(canvas.label("comp_out", "interact above ↑"))

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

# --- Colour themes ---
col.add(canvas.markdown("""## Colour themes
Pass `color=(r,g,b)` or `color="#hex"` — dark and glow variants are derived
automatically so hover, active and focus states all follow the theme.
""", name="color_hdr"))

with col.row(gap=GAP):
    canvas.button("go_btn",   text="Launch",   color="#10b981", w=136)
    canvas.toggle("mode_tg",  ["A", "B", "C"], color=(168, 85, 247), w=216)
    canvas.slider("heat_sl",  min=0, max=100, default=35, color="#f59e0b", w=268)
    canvas.text_field("tag_fld", placeholder="search…", color="#06b6d4", w=196)

# --- The three data verbs ---
col.add(canvas.markdown("""## The three data verbs
| Verb | Means | Replayed on reconnect? | Panels |
|---|---|:---:|---|
| `.update(value)` | **replace** whole state | ✅ | Label, Table, Slider, … |
| `.push(sample)` | **append** one point | ❌ | LivePlot, Custom, React |
| `.add(values, step)` | **record** a distribution snapshot | ✅ | Histogram |
""", name="data_verbs"))

# --- Receiving input ---
col.add(canvas.markdown("""## Receiving input
`@panel.on_change` / `@button.on_click` / `@table.on_select` / `@panel.on_layout`

Any handler may declare a trailing `viewer` arg to see who acted:
`def _(value, viewer):` — gives `viewer["name"]`, `viewer["role"]`, …
""", name="input_hdr", color=(0,255,255)))

with col.row(gap=GAP):
    name_fld = canvas.text_field("visitor_name", label="Your name",
                                  placeholder="type here and press Enter…",
                                  w=300)
    greeting = canvas.label("greeting", "Enter your name →", w=296)

@name_fld.on_change
def _(text, viewer):
    greeting.update(f"Hello, {text or viewer['name']}!")

# --- Show anything ---
col.add(canvas.markdown("""## Show anything
`canvas.show(value)` inspects the value and inserts the best panel automatically —
like a notebook deciding how to render an `Out[...]`, but works in plain scripts.
""", name="show_hdr"))

with col.row(gap=GAP):
    canvas.show({"status": "ok", "temp": 42.1, "rpm": 1200},
                name="show_dict", w=310, h=160)
    canvas.show("# Heading\n`canvas.show()` rendered this **Markdown** from a string.",
                name="show_md", w=310, h=160)

# --- Table ---
col.add(canvas.markdown("""## Table — `canvas.table(data)`
Accepts a DataFrame, list-of-dicts, or plain dict.
`@table.on_select` fires with the selected row indices; `table.selected` is always live.
""", name="table_hdr"))

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
demo_table = col.add(canvas.table(_catalog, name="component_catalogue",
                                   label="Component catalogue", h=280))

tbl_sel = col.add(canvas.label("tbl_sel", "Select a row to see its component →"))

@demo_table.on_select
def _(indices):
    if not indices:
        tbl_sel.update("Select a row to see its component →")
    else:
        row = _catalog[indices[0]]
        tbl_sel.update(f"{row['Component']}  ·  {row['Direction']}  ·  {row['Key API']}")

# --- Frameless panels ---
col.add(canvas.markdown("""## Frameless panels — `frame=False`
Pass `frame=False` to `canvas.insert()` (or any factory) to strip the card chrome.
Add `grabbable=False` to make the panel behave like pure ambient content.
""", name="frameless_hdr"))

col.add(canvas.label("fl_label",
                      "✦  No card border, no title bar — just content floating on the canvas.",
                      grabbable=False))

col.add(canvas.markdown("""## React panels
`canvas.react(source=...)` compiles your JSX in-browser — no npm, inherits the canvas theme.
`canvas.send({...})` posts up to Python; `panel.push(data)` sends down as the `value` prop.
""", name="react_hdr"))

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
col.add(canvas.react(source=_radio_raw, name="radio_widget",
                     label="Uiverse radio (frame=False)",
                     frame=False, h=80, grabbable=False))

col.add(canvas.react(source="""
import React from 'react';
import styled from 'styled-components';

const Radio = () => {
  return (
    <StyledWrapper>
      <div className="container">
        <div className="radio-wrapper">
          <input className="input" name="btn" id="value-1" type="radio" />
          <div className="btn">
            <span aria-hidden>_</span>Cyber
            <span className="btn__glitch" aria-hidden>_Cyber🦾</span>
            <label className="number">r1</label>
          </div>
        </div>
        <div className="radio-wrapper">
          <input className="input" name="btn" id="value-2" defaultChecked="true" type="radio" />
          <div className="btn">
            _Radio<span aria-hidden>_</span>
            <span className="btn__glitch" aria-hidden>_R_a_d_i_o_</span>
            <label className="number">r2</label>
          </div>
        </div>
        <div className="radio-wrapper">
          <input className="input" name="btn" id="value-3" type="radio" />
          <div className="btn">
            Buttons<span aria-hidden />
            <span className="btn__glitch" aria-hidden>Buttons_</span>
            <label className="number">r3</label>
          </div>
        </div>
      </div>
    </StyledWrapper>
  );
};

const StyledWrapper = styled.div`
  .container { display: flex; flex-direction: row; }

  .radio-wrapper { position: relative; height: 38px; width: 84px; margin: 3px; }

  .radio-wrapper .input {
    position: absolute; height: 100%; width: 100%; margin: 0;
    cursor: pointer; z-index: 10; opacity: 0;
  }

  .btn {
    --primary: #ff184c; --shadow-primary: #fded00; --color: white;
    --font-size: 9px; --shadow-secondary: hsl(60, 90%, 60%);
    --clip: polygon(11% 0, 95% 0, 100% 25%, 90% 90%, 95% 90%, 85% 90%, 85% 100%, 7% 100%, 0 80%);
    --border: 5px; --shimmy-distance: 5;
    --clip-one: polygon(0 2%, 100% 2%, 100% 95%, 95% 95%, 95% 90%, 85% 90%, 85% 95%, 8% 95%, 0 70%);
    --clip-two: polygon(0 78%, 100% 78%, 100% 100%, 95% 100%, 95% 90%, 85% 90%, 85% 100%, 8% 100%, 0 78%);
    --clip-three: polygon(0 44%, 100% 44%, 100% 54%, 95% 54%, 95% 54%, 85% 54%, 85% 54%, 8% 54%, 0 54%);
    --clip-four: polygon(0 0, 100% 0, 100% 0, 95% 0, 95% 0, 85% 0, 85% 0, 8% 0, 0 0);
    --clip-five: polygon(0 0, 100% 0, 100% 0, 95% 0, 95% 0, 85% 0, 85% 0, 8% 0, 0 0);
    --clip-six: polygon(0 40%, 100% 40%, 100% 85%, 95% 85%, 95% 85%, 85% 85%, 85% 85%, 8% 85%, 0 70%);
    --clip-seven: polygon(0 63%, 100% 63%, 100% 80%, 95% 80%, 95% 80%, 85% 80%, 85% 80%, 8% 80%, 0 70%);
    color: var(--color); text-transform: uppercase; font-size: var(--font-size);
    letter-spacing: 3px; position: relative; font-weight: 900;
    width: 100%; height: 100%; line-height: 38px; text-align: center;
    transition: background 0.2s, 0.3s;
  }

  .input:checked + .btn { --primary: #8B00FF; --shadow-primary: #00e572; }
  .input:hover  + .btn  { --primary: #cc133c; --font-size: 11px; }

  .btn:after, .btn:before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    clip-path: var(--clip); z-index: -1;
  }
  .btn:before { background: var(--shadow-primary); transform: translate(var(--border), 0); }
  .btn:after  { background: var(--primary); }

  .btn__glitch {
    position: absolute;
    top: calc(var(--border) * -1); left: calc(var(--border) * -1);
    right: calc(var(--border) * -1); bottom: calc(var(--border) * -1);
    background: var(--shadow-primary);
    text-shadow: 2px 2px var(--shadow-primary), -2px -2px hsl(60,90%,60%);
    clip-path: var(--clip); animation: glitch 2s infinite; display: none;
  }
  .btn__glitch:before {
    content: ''; position: absolute;
    top: calc(var(--border) * 1); right: calc(var(--border) * 1);
    bottom: calc(var(--border) * 1); left: calc(var(--border) * 1);
    clip-path: var(--clip); background: var(--primary); z-index: -1;
  }
  .input:hover   + .btn .btn__glitch { display: block; }
  .input:checked + .btn .btn__glitch { display: block; animation: glitch 5s infinite; }

  .number {
    background: var(--shadow-primary); color: #323232; font-size: 5.5px;
    font-weight: 700; letter-spacing: 1px; position: absolute;
    width: 15px; height: 6px; top: 0; left: 81%; line-height: 6.2px;
  }

  @keyframes glitch {
    0%          { clip-path: var(--clip-one); }
    2%, 8%      { clip-path: var(--clip-two);   transform: translate(calc(var(--shimmy-distance) * -1%), 0); }
    6%          { clip-path: var(--clip-two);   transform: translate(calc(var(--shimmy-distance) * 1%),  0); }
    9%          { clip-path: var(--clip-two);   transform: translate(0, 0); }
    10%         { clip-path: var(--clip-three); transform: translate(calc(var(--shimmy-distance) * 1%),  0); }
    13%         { clip-path: var(--clip-three); transform: translate(0, 0); }
    14%, 21%    { clip-path: var(--clip-four);  transform: translate(calc(var(--shimmy-distance) * 1%),  0); }
    25%         { clip-path: var(--clip-five);  transform: translate(calc(var(--shimmy-distance) * 1%),  0); }
    30%         { clip-path: var(--clip-five);  transform: translate(calc(var(--shimmy-distance) * -1%), 0); }
    35%, 45%    { clip-path: var(--clip-six);   transform: translate(calc(var(--shimmy-distance) * -1%)); }
    40%         { clip-path: var(--clip-six);   transform: translate(calc(var(--shimmy-distance) * 1%)); }
    50%         { clip-path: var(--clip-six);   transform: translate(0, 0); }
    55%         { clip-path: var(--clip-seven); transform: translate(calc(var(--shimmy-distance) * 1%),  0); }
    60%         { clip-path: var(--clip-seven); transform: translate(0, 0); }
    31%, 61%, 100% { clip-path: var(--clip-four); }
  }
`;

export default Radio;
""", name="cyber_radio", label="Uiverse cyber radio (frame=False)", frame=False, grabbable=False))

_colors_raw = """
import React from 'react';
import styled from 'styled-components';

const Card = () => {
  const swatches = [
    '#e11d48','#f472b6','#fb923c','#facc15','#84cc16',
    '#10b981','#0ea5e9','#3b82f6','#8b5cf6','#a78bfa',
  ];
  return (
    <StyledWrapper>
      <div className="body">
        <div className="comic-panel">
          <div className="container-items">
            {swatches.map(c => (
              <button key={c} className="item-color"
                      style={{"--color": c}} aria-color={c} />
            ))}
          </div>
        </div>
      </div>
    </StyledWrapper>
  );
};

const StyledWrapper = styled.div`
  .body {
    display: flex;
    justify-content: center;
    align-items: center;
    width: 100%;
    height: 100%;
    background-color: #f0e8d8;
    font-family: "Bangers", cursive;
    overflow: hidden;
  }
  .comic-panel {
    background: #ffffff;
    border: 4px solid #000;
    padding: 1.2rem;
    border-radius: 8px;
    box-shadow: 4px 4px 0px rgba(0,0,0,1);
  }
  .container-items {
    display: flex;
    transform-style: preserve-3d;
    transform: perspective(1000px);
  }
  .item-color {
    position: relative;
    flex-shrink: 0;
    width: 40px;
    height: 48px;
    border: none;
    outline: none;
    margin: -4px;
    background-color: transparent;
    transition: 300ms ease-out;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .item-color::after {
    position: absolute;
    content: "";
    inset: 0;
    width: 40px;
    height: 40px;
    background-color: var(--color);
    border-radius: 6px;
    border: 3px solid #000;
    box-shadow: 4px 4px 0 0 #000;
    pointer-events: none;
    transition: 300ms cubic-bezier(0.175, 0.885, 0.32, 1.275);
  }
  .item-color::before {
    position: absolute;
    content: attr(aria-color);
    left: 50%;
    bottom: 60px;
    font-size: 16px;
    letter-spacing: 1px;
    line-height: 1;
    padding: 6px 10px;
    background-color: #fef3c7;
    color: #000;
    border: 3px solid #000;
    border-radius: 6px;
    pointer-events: none;
    opacity: 0;
    visibility: hidden;
    transform-origin: bottom center;
    transition: all 300ms cubic-bezier(0.175, 0.885, 0.32, 1.275),
                opacity 300ms ease-out, visibility 300ms ease-out;
    transform: translateX(-50%) scale(0.5) translateY(10px);
    white-space: nowrap;
  }
  .item-color:hover {
    transform: scale(1.5) translateY(-5px);
    z-index: 99999;
  }
  .item-color:hover::before {
    opacity: 1;
    visibility: visible;
    transform: translateX(-50%) scale(1) translateY(0);
  }
  .item-color:active::after {
    transform: translate(2px, 2px);
    box-shadow: 2px 2px 0 0 #000;
  }
  .item-color:hover + * { transform: scale(1.3) translateY(-3px); z-index: 9999; }
  .item-color:hover + * + * { transform: scale(1.15); z-index: 999; }
  .item-color:has(+ *:hover) { transform: scale(1.3) translateY(-3px); z-index: 9999; }
  .item-color:has(+ * + *:hover) { transform: scale(1.15); z-index: 999; }
`;

export default Card;
"""
col.add(canvas.react(source=_colors_raw, name="color_picker",
                     label="Uiverse color picker (frame=False)", frame=False, grabbable=False))

_pills_raw = """
import React, { useState } from 'react';
import styled from 'styled-components';

const PillTabs = () => {
  const [active, setActive] = useState(0);
  const tabs = ['Python', 'React', 'WebSocket'];
  return (
    <StyledWrapper>
      <div className="pills">
        {tabs.map((t, i) => (
          <button key={t}
                  className={`pill ${active === i ? 'active' : ''}`}
                  onClick={() => setActive(i)}>
            {t}
          </button>
        ))}
      </div>
    </StyledWrapper>
  );
};

const StyledWrapper = styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-family: system-ui, sans-serif;

  .pills {
    display: flex;
    gap: 6px;
    background: #1e293b;
    padding: 6px;
    border-radius: 12px;
    border: 1px solid #334155;
  }

  .pill {
    padding: 8px 24px;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
    background: transparent;
    color: #94a3b8;
    transition: all 180ms;
  }

  .pill.active {
    background: #3b82f6;
    color: #fff;
    box-shadow: 0 2px 10px rgba(59,130,246,.45);
  }

  .pill:hover:not(.active) {
    background: #334155;
    color: #e2e8f0;
  }
`;

export default PillTabs;
"""
col.add(canvas.react(source=_pills_raw, name="pill_tabs",
                     label="Uiverse pill tabs (frame=False)",
                     frame=False, h=80, grabbable=False))

_matrix_raw = """
import React from 'react';
import styled from 'styled-components';

const Pattern = () => {
  return (
    <StyledWrapper>
      <div className="matrix-container">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="matrix-pattern">
            {[...Array(40)].map((_, j) => (
              <div key={j} className="matrix-column" />
            ))}
          </div>
        ))}
      </div>
    </StyledWrapper>
  );
};

const StyledWrapper = styled.div`
  .matrix-container {
    position: relative;
    width: 100%;
    height: 100%;
    background: #000;
    display: flex;
  }

  .matrix-pattern {
    position: relative;
    width: 1000px;
    height: 100%;
    flex-shrink: 0;
  }

  .matrix-column {
    position: absolute;
    top: -100%;
    width: 20px;
    height: 100%;
    font-size: 16px;
    line-height: 18px;
    font-weight: bold;
    animation: fall linear infinite;
    white-space: nowrap;
  }

  .matrix-column::before {
    content: "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    position: absolute;
    top: 0;
    left: 0;
    background: linear-gradient(
      to bottom,
      #ffffff 0%, #ffffff 5%, #00ff41 10%, #00ff41 20%,
      #00dd33 30%, #00bb22 40%, #009911 50%, #007700 60%,
      #005500 70%, #003300 80%, rgba(0,255,65,0.5) 90%, transparent 100%
    );
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    writing-mode: vertical-lr;
    letter-spacing: 1px;
  }

  .matrix-column:nth-child(1)  { left: 0px;   animation-delay: -0.2s; animation-duration: 3s;   }
  .matrix-column:nth-child(2)  { left: 25px;  animation-delay: -3.4s; animation-duration: 4s;   }
  .matrix-column:nth-child(3)  { left: 50px;  animation-delay: -0.9s; animation-duration: 2.5s; }
  .matrix-column:nth-child(4)  { left: 75px;  animation-delay: -2.6s; animation-duration: 3.5s; }
  .matrix-column:nth-child(5)  { left: 100px; animation-delay: -0.6s; animation-duration: 3s;   }
  .matrix-column:nth-child(6)  { left: 125px; animation-delay: -2.7s; animation-duration: 4.5s; }
  .matrix-column:nth-child(7)  { left: 150px; animation-delay: -1.3s; animation-duration: 2.8s; }
  .matrix-column:nth-child(8)  { left: 175px; animation-delay: -2.9s; animation-duration: 3.2s; }
  .matrix-column:nth-child(9)  { left: 200px; animation-delay: -0.6s; animation-duration: 3.8s; }
  .matrix-column:nth-child(10) { left: 225px; animation-delay: -1.8s; animation-duration: 2.7s; }
  .matrix-column:nth-child(11) { left: 250px; animation-delay: -1.7s; animation-duration: 4.2s; }
  .matrix-column:nth-child(12) { left: 275px; animation-delay: -2.5s; animation-duration: 3.1s; }
  .matrix-column:nth-child(13) { left: 300px; animation-delay: -0.9s; animation-duration: 3.6s; }
  .matrix-column:nth-child(14) { left: 325px; animation-delay: -2.0s; animation-duration: 2.9s; }
  .matrix-column:nth-child(15) { left: 350px; animation-delay: -2.1s; animation-duration: 4.1s; }
  .matrix-column:nth-child(16) { left: 375px; animation-delay: -3.1s; animation-duration: 3.3s; }
  .matrix-column:nth-child(17) { left: 400px; animation-delay: -1.1s; animation-duration: 3.7s; }
  .matrix-column:nth-child(18) { left: 425px; animation-delay: -2.1s; animation-duration: 2.6s; }
  .matrix-column:nth-child(19) { left: 450px; animation-delay: -0.4s; animation-duration: 4.3s; }
  .matrix-column:nth-child(20) { left: 475px; animation-delay: -1.9s; animation-duration: 3.4s; }
  .matrix-column:nth-child(21) { left: 500px; animation-delay: -0.9s; animation-duration: 2.4s; }
  .matrix-column:nth-child(22) { left: 525px; animation-delay: -3.4s; animation-duration: 3.9s; }
  .matrix-column:nth-child(23) { left: 550px; animation-delay: -0.4s; animation-duration: 3s;   }
  .matrix-column:nth-child(24) { left: 575px; animation-delay: -2.7s; animation-duration: 4.4s; }
  .matrix-column:nth-child(25) { left: 600px; animation-delay: -1.0s; animation-duration: 2.3s; }
  .matrix-column:nth-child(26) { left: 625px; animation-delay: -3.2s; animation-duration: 3.5s; }
  .matrix-column:nth-child(27) { left: 650px; animation-delay: -0.7s; animation-duration: 4s;   }
  .matrix-column:nth-child(28) { left: 675px; animation-delay: -2.5s; animation-duration: 2.8s; }
  .matrix-column:nth-child(29) { left: 700px; animation-delay: -3.2s; animation-duration: 3.6s; }
  .matrix-column:nth-child(30) { left: 725px; animation-delay: -2.7s; animation-duration: 3.2s; }
  .matrix-column:nth-child(31) { left: 750px; animation-delay: -1.8s; animation-duration: 2.7s; }
  .matrix-column:nth-child(32) { left: 775px; animation-delay: -3.6s; animation-duration: 4.1s; }
  .matrix-column:nth-child(33) { left: 800px; animation-delay: -2.1s; animation-duration: 3.1s; }
  .matrix-column:nth-child(34) { left: 825px; animation-delay: -3.4s; animation-duration: 3.7s; }
  .matrix-column:nth-child(35) { left: 850px; animation-delay: -2.8s; animation-duration: 2.9s; }
  .matrix-column:nth-child(36) { left: 875px; animation-delay: -3.7s; animation-duration: 4.2s; }
  .matrix-column:nth-child(37) { left: 900px; animation-delay: -2.3s; animation-duration: 3.3s; }
  .matrix-column:nth-child(38) { left: 925px; animation-delay: -1.9s; animation-duration: 2.5s; }
  .matrix-column:nth-child(39) { left: 950px; animation-delay: -3.5s; animation-duration: 3.8s; }
  .matrix-column:nth-child(40) { left: 975px; animation-delay: -2.6s; animation-duration: 3.4s; }

  .matrix-column:nth-child(odd)::before   { content: "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン123456789"; }
  .matrix-column:nth-child(even)::before  { content: "ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴァィゥェォャュョッABCDEFGHIJKLMNOPQRSTUVWXYZ"; }
  .matrix-column:nth-child(3n)::before    { content: "アカサタナハマヤラワイキシチニヒミリウクスツヌフムユルエケセテネヘメレオコソトノホモヨロヲン0987654321"; }
  .matrix-column:nth-child(4n)::before    { content: "ンヲロヨモホノトソコオレメヘネテセケエルユムフヌツスクウリミヒニチシキイワラヤマハナタサカア"; }
  .matrix-column:nth-child(5n)::before    { content: "ガザダバパギジヂビピグズヅブプゲゼデベペゴゾドボポヴァィゥェォャュョッ!@#$%^&*()_+-=[]{}|;:,.<>?"; }

  @keyframes fall {
    0%   { transform: translateY(-10%); opacity: 1; }
    100% { transform: translateY(200%); opacity: 0; }
  }
`;

export default Pattern;
"""
col.add(canvas.react(source=_matrix_raw, name="matrix_rain",
                     label="Uiverse matrix rain (frame=False)",
                     frame=False, h=200, grabbable=False))

_earth_raw = """
import React from 'react';
import styled from 'styled-components';

const Loader = () => {
  return (
    <StyledWrapper>
      <div className="earth">
        <div className="earth-loader">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
            <path transform="translate(100 100)" d="M29.4,-17.4C33.1,1.8,27.6,16.1,11.5,31.6C-4.7,47,-31.5,63.6,-43,56C-54.5,48.4,-50.7,16.6,-41,-10.9C-31.3,-38.4,-15.6,-61.5,-1.4,-61C12.8,-60.5,25.7,-36.5,29.4,-17.4Z" fill="#7CC133" />
          </svg>
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
            <path transform="translate(100 100)" d="M31.7,-55.8C40.3,-50,45.9,-39.9,49.7,-29.8C53.5,-19.8,55.5,-9.9,53.1,-1.4C50.6,7.1,43.6,14.1,41.8,27.6C40.1,41.1,43.4,61.1,37.3,67C31.2,72.9,15.6,64.8,1.5,62.2C-12.5,59.5,-25,62.3,-31.8,56.7C-38.5,51.1,-39.4,37.2,-49.3,26.3C-59.1,15.5,-78,7.7,-77.6,0.2C-77.2,-7.2,-57.4,-14.5,-49.3,-28.4C-41.2,-42.4,-44.7,-63,-38.5,-70.1C-32.2,-77.2,-16.1,-70.8,-2.3,-66.9C11.6,-63,23.1,-61.5,31.7,-55.8Z" fill="#7CC133" />
          </svg>
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
            <path transform="translate(100 100)" d="M30.6,-49.2C42.5,-46.1,57.1,-43.7,67.6,-35.7C78.1,-27.6,84.6,-13.8,80.3,-2.4C76.1,8.9,61.2,17.8,52.5,29.1C43.8,40.3,41.4,53.9,33.7,64C26,74.1,13,80.6,2.2,76.9C-8.6,73.1,-17.3,59,-30.6,52.1C-43.9,45.3,-61.9,45.7,-74.1,38.2C-86.4,30.7,-92.9,15.4,-88.6,2.5C-84.4,-10.5,-69.4,-20.9,-60.7,-34.6C-52.1,-48.3,-49.8,-65.3,-40.7,-70C-31.6,-74.8,-15.8,-67.4,-3.2,-61.8C9.3,-56.1,18.6,-52.3,30.6,-49.2Z" fill="#7CC133" />
          </svg>
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
            <path transform="translate(100 100)" d="M39.4,-66C48.6,-62.9,51.9,-47.4,52.9,-34.3C53.8,-21.3,52.4,-10.6,54.4,1.1C56.3,12.9,61.7,25.8,57.5,33.2C53.2,40.5,39.3,42.3,28.2,46C17,49.6,8.5,55.1,1.3,52.8C-5.9,50.5,-11.7,40.5,-23.6,37.2C-35.4,34,-53.3,37.5,-62,32.4C-70.7,27.4,-70.4,13.7,-72.4,-1.1C-74.3,-15.9,-78.6,-31.9,-73.3,-43C-68.1,-54.2,-53.3,-60.5,-39.5,-60.9C-25.7,-61.4,-12.9,-56,1.1,-58C15.1,-59.9,30.2,-69.2,39.4,-66Z" fill="#7CC133" />
          </svg>
        </div>
        <p>Connecting...</p>
      </div>
    </StyledWrapper>
  );
};

const StyledWrapper = styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;

  .earth-loader {
    --watercolor: #3344c1;
    --landcolor: #7cc133;
    width: 7.5em;
    height: 7.5em;
    background-color: var(--watercolor);
    position: relative;
    overflow: hidden;
    border-radius: 50%;
    box-shadow:
      inset 0em 0.5em rgb(255, 255, 255, 0.25),
      inset 0em -0.5em rgb(0, 0, 0, 0.25);
    border: solid 0.15em white;
    animation: startround 1s;
    animation-iteration-count: 1;
  }

  .earth p {
    color: white;
    display: flex;
    justify-content: center;
    align-items: center;
    padding-top: 0.25em;
    font-size: 1.25em;
    font-family: "Gill Sans", "Gill Sans MT", Calibri, "Trebuchet MS", sans-serif;
  }

  .earth-loader svg:nth-child(1) {
    position: absolute;
    bottom: -2em;
    width: 7em;
    height: auto;
    animation: round1 5s infinite linear 0.75s;
  }
  .earth-loader svg:nth-child(2) {
    position: absolute;
    top: -3em;
    width: 7em;
    height: auto;
    animation: round1 5s infinite linear;
  }
  .earth-loader svg:nth-child(3) {
    position: absolute;
    top: -2.5em;
    width: 7em;
    height: auto;
    animation: round2 5s infinite linear;
  }
  .earth-loader svg:nth-child(4) {
    position: absolute;
    bottom: -2.2em;
    width: 7em;
    height: auto;
    animation: round2 5s infinite linear 0.75s;
  }

  @keyframes startround {
    0%   { filter: brightness(500%); box-shadow: none; }
    75%  { filter: brightness(500%); box-shadow: none; }
    100% { filter: brightness(100%); box-shadow: inset 0em 0.5em rgb(255,255,255,0.25), inset 0em -0.5em rgb(0,0,0,0.25); }
  }

  @keyframes round1 {
    0%   { left: -2em; opacity: 100%; transform: skewX(0deg)   rotate(0deg);   }
    30%  { left: -6em; opacity: 100%; transform: skewX(-25deg) rotate(25deg);  }
    31%  { left: -6em; opacity: 0%;   transform: skewX(-25deg) rotate(25deg);  }
    35%  { left:  7em; opacity: 0%;   transform: skewX(25deg)  rotate(-25deg); }
    45%  { left:  7em; opacity: 100%; transform: skewX(25deg)  rotate(-25deg); }
    100% { left: -2em; opacity: 100%; transform: skewX(0deg)   rotate(0deg);   }
  }

  @keyframes round2 {
    0%   { left:  5em; opacity: 100%; transform: skewX(0deg)   rotate(0deg);   }
    75%  { left: -7em; opacity: 100%; transform: skewX(-25deg) rotate(25deg);  }
    76%  { left: -7em; opacity: 0%;   transform: skewX(-25deg) rotate(25deg);  }
    77%  { left:  8em; opacity: 0%;   transform: skewX(25deg)  rotate(-25deg); }
    80%  { left:  8em; opacity: 100%; transform: skewX(25deg)  rotate(-25deg); }
    100% { left:  5em; opacity: 100%; transform: skewX(0deg)   rotate(0deg);   }
  }
`;

export default Loader;
"""
col.add(canvas.react(source=_earth_raw, name="earth_loader",
                     label="Uiverse earth loader (frame=False)",
                     frame=False, h=160, grabbable=False))

_count = 0
counter_panel = col.add(canvas.react(
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
    name="counter", label="React counter", w=240,
))

@counter_panel.on_message
def _(msg):
    global _count
    d = msg.get("d", 0)
    _count = 0 if d == "reset" else _count + d
    counter_panel.push(_count)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §3 Layout
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""# 3. Layout
`x`/`y` = canvas coords · `w`/`h` = pixels. Omit → auto-arrange.
Relative: `below=`, `right_of=`, `left_of=`, `above=` (+ `gap=`).
Containers: `canvas.column(x, y, w, gap)` · `canvas.row()` · `canvas.grid(cols=N)`.
When an `h="auto"` panel grows, the container repacks siblings automatically.
""", name="section_3"))

with col.row(gap=GAP):
    canvas.label("ga", "grid slot 1", w=200)
    canvas.label("gb", "grid slot 2", w=200)
    canvas.label("gc", "grid slot 3", w=200)

with col.row(gap=GAP):
    canvas.label("gd", "grid slot 4", w=200)
    canvas.label("ge", "grid slot 5", w=200)
    canvas.label("gf", "grid slot 6", w=200)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §4 Views & Navigation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""# 4. Views & Navigation
Pass `view=` to `serve()` or call `canvas.set_view()` live.
Keys: `x`, `y`, `zoom`, `locked`, `ui`, `grid`, `read_only`, `min_zoom`, `max_zoom`.
Scope to a role or single client with `roles=` / `client_id=`.
""", name="section_4"))

with col.row(gap=GAP):
    zoom_in_bt  = canvas.button("zoom_in",    text="Zoom in (1.5×)",  w=180, color=(255,0,0))
    zoom_out_bt = canvas.button("zoom_out",   text="Zoom out (0.5×)", w=180, color=(0,255,0))
    zoom_rst_bt = canvas.button("zoom_rst",   text="Zoom 100%",       w=180, color=(0,100,255))

layout_rst_bt = col.add(canvas.button("layout_rst", text="↺ Reset layout", w=180))

@zoom_in_bt.on_click
def _(): canvas.set_view(zoom=1.5)

@zoom_out_bt.on_click
def _(): canvas.set_view(zoom=0.5)

@zoom_rst_bt.on_click
def _(): canvas.set_view(zoom=1.0)

@layout_rst_bt.on_click
def _(): canvas.reset_layout()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §5 Serving & Sharing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""# 5. Serving & Sharing
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
""", name="section_5"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Beyond the five steps — live telemetry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""# Beyond the five steps
Live streaming telemetry, ML training dashboards, desktop packaging (`bake()`),
hot reloading, notebooks (`block=False`), canvas merging, and more.

## Live plot — `canvas.live_plot()`
`push({trace: y}, x=step)` appends one point per call. The server coalesces
frames a slow client can't keep up with; `.push([batch], x=[xs])` flushes
many points at once.
""", name="beyond"))

lp = col.add(canvas.live_plot("telemetry", traces=["sin", "cos"],
                               label="Live telemetry", h=220))

@canvas.background
def _():
    t = 0.0
    while True:
        lp.push({"sin": math.sin(t), "cos": math.cos(t)}, x=round(t, 2))
        t += 0.1
        time.sleep(0.05)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Inspector
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col.add(canvas.markdown("""## Inspector
A live variable/panel explorer. Switch between **panels** (component state +
geometry) and **globals** (script namespace). Also spawnable on demand via the
🔍 toolbar button.
""", name="insp_hdr"))

col.add(canvas.inspector(name="readme_inspector", label="Inspector",
                          source="components", refresh=2.0, h=280))

canvas.serve(hot_reload=True, namespace=globals(),
             view={"x": 375, "y": 230, "zoom": 1.8})
