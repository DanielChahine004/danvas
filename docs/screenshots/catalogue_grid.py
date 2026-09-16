"""Catalogue for the README screenshot: the native panels laid out on a grid.

The examples/catalogue.py column is the interactive tour; this one is the same
set of panels (minus the ones needing a webcam, microphone, or the network)
arranged to fit one screen.
"""
import math
import random
import threading
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go

import danvas

canvas = danvas.Canvas()

ROSE, AMBER, YELLOW, TEAL, SKY, INDIGO = "#e05c7a", "#e0923a", "#c8b400", "#2aab8a", "#3a8fd4", "#6b6bd4"
VIOLET, PINK, CORAL, SAGE, SLATE, PLUM = "#a45cc8", "#d45aa0", "#e06050", "#5aab72", "#5a8aaa", "#8a5ab4"

W, GAP = 330, 24
COL = [40 + i * (W + GAP) for i in range(4)]


def at(col, y):
    return dict(x=COL[col], y=y, w=W)


# column 0: controls
lbl = canvas.label("lbl", value="Hello from label", label="Label", color=ROSE, **at(0, 40))
slider = canvas.slider("brightness", min=0, max=100, default=62, label="Slider", color=YELLOW, **at(0, 170))
tog = canvas.toggle(["Off", "Slow", "Fast"], name="speed", default="Slow", label="Toggle", color=TEAL, **at(0, 300))
btn = canvas.button("ping", text="Click me", label="Button", color=SKY, **at(0, 430))
tf = canvas.text_field("input", placeholder="Type something…", label="Text field", color=INDIGO, **at(0, 560))
md = canvas.markdown("**Markdown** — supports `code`, *italics*, lists, and more.",
                     name="md", label="Markdown", color=AMBER, **at(0, 690))

# column 1: data
tbl = canvas.table({"Name": ["Alice", "Bob", "Carol", "Dan"], "Score": [92, 85, 78, 88]},
                   name="scores", label="Table", color=VIOLET, **at(1, 40), h=300)
xs = list(range(30))
chart = canvas.plot("chart", label="Plot", color=PINK, **at(1, 370), h=260)
chart.update(go.Figure(go.Scatter(x=xs, y=[math.sin(x * 0.4) for x in xs], mode="lines+markers", name="sin")))
hm = canvas.heatmap("heat", label="Heatmap", color=SLATE, **at(1, 660), h=260)
yy, xx = np.mgrid[-2:2:60j, -2:2:60j]
hm.update(np.exp(-(xx**2 + yy**2)) * np.cos(3 * xx), cmap="viridis")

# column 2: streams + images
hist = canvas.histogram("hist", label="Histogram", color=CORAL, **at(2, 40), h=280)
for epoch in range(6):
    hist.add(np.random.normal(epoch * 0.3, 1, 300), step=epoch)
lp = canvas.live_plot("live", label="Live plot", color=SAGE, **at(2, 350), h=260)
fig, ax = plt.subplots(figsize=(4, 2.2))
ax.plot([0, 1, 2, 3, 4], [1, 4, 2, 5, 3], color="steelblue", marker="o")
ax.set_title("matplotlib figure")
fig.tight_layout()
img = canvas.image(fig, name="img", label="Image", color=SKY, **at(2, 640), h=280)

# column 3: files, chat, custom
dl = canvas.download("dl", source=b"hello from danvas\n", filename="hello.txt",
                     text="Download hello.txt", label="Download", color=SLATE, **at(3, 40))
up = canvas.upload("up", text="Choose a file", label="Upload", color=PLUM, **at(3, 170))
fb = canvas.file_browser("fb", root=".", label="File browser", color=ROSE, **at(3, 300), h=260)
chat = canvas.chat("room", label="Chat", color=TEAL, **at(3, 590), h=200)
cust = canvas.custom(
    html="<button onclick=\"this.textContent='Clicked ' + (++n)\">Click me</button>",
    css="button{font:14px sans-serif;padding:8px 16px;border-radius:6px;border:0;"
        "background:#6b6bd4;color:#fff;cursor:pointer}",
    js="var n=0;", name="cust", label="Custom (HTML/JS)", color=AMBER, **at(3, 820), h=100,
)


@slider.on_change
def _(v):
    lbl.update(f"brightness {v:.0f}")


@canvas.background
def tick():
    t = 0.0
    while True:
        lp.push({"signal": math.sin(t * 0.5) + random.uniform(-0.1, 0.1)})
        t += 0.064
        time.sleep(0.064)


canvas.serve(port=8000)
