"""Catalogue: one column showcasing every native danvas component."""

import math
import random
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go

import danvas

canvas = danvas.Canvas()

# Palette — one colour per panel, cycling through a soft rainbow.
ROSE    = "#e05c7a"
AMBER   = "#e0923a"
YELLOW  = "#c8b400"
TEAL    = "#2aab8a"
SKY     = "#3a8fd4"
INDIGO  = "#6b6bd4"
VIOLET  = "#a45cc8"
PINK    = "#d45aa0"
CORAL   = "#e06050"
SAGE    = "#5aab72"
SLATE   = "#5a8aaa"
PLUM    = "#8a5ab4"

# ── Label ──────────────────────────────────────────────────────────────────────
lbl = canvas.label("lbl", value="Hello from label", label="Label", color=ROSE)

# ── Markdown ───────────────────────────────────────────────────────────────────
md = canvas.markdown(
    "**Markdown** — supports `code`, *italics*, lists, and more.",
    name="md", label="Markdown", color=AMBER, below=lbl,
)

# ── Slider ─────────────────────────────────────────────────────────────────────
slider = canvas.slider("brightness", min=0, max=100, default=50,
                       label="Slider", color=YELLOW, below=md)

# ── Toggle ─────────────────────────────────────────────────────────────────────
tog = canvas.toggle(["Off", "Slow", "Fast"], name="speed", default="Slow",
                    label="Toggle", color=TEAL, below=slider)

# ── Button ─────────────────────────────────────────────────────────────────────
btn = canvas.button("ping", text="Click me", label="Button", color=SKY, below=tog)

# ── Text field ─────────────────────────────────────────────────────────────────
tf = canvas.text_field("input", placeholder="Type something…",
                       label="Text field", color=INDIGO, below=btn)

# ── Table ──────────────────────────────────────────────────────────────────────
tbl = canvas.table(
    {"Name": ["Alice", "Bob", "Carol"], "Score": [92, 85, 78]},
    name="scores", label="Table", color=VIOLET, below=tf,
)

# ── Plot ───────────────────────────────────────────────────────────────────────
chart = canvas.plot("chart", label="Plot", color=PINK, below=tbl)
xs = list(range(20))
chart.update(go.Figure(go.Scatter(x=xs, y=[math.sin(x * 0.4) for x in xs],
                                  mode="lines+markers", name="sin")))

# ── Histogram ──────────────────────────────────────────────────────────────────
hist = canvas.histogram("hist", label="Histogram", color=CORAL, below=chart)
for epoch in range(5):
    hist.add(np.random.normal(epoch * 0.3, 1, 300), step=epoch)

# ── Live plot ──────────────────────────────────────────────────────────────────
lp = canvas.live_plot("live", label="Live plot", color=SAGE, below=hist)

# ── Image ──────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(4, 2))
ax.plot([0, 1, 2, 3], [1, 4, 2, 5], color="steelblue")
ax.set_title("matplotlib figure")
img = canvas.image(fig, name="img", label="Image", color=SKY, below=lp)

# ── Download ───────────────────────────────────────────────────────────────────
dl = canvas.download("dl", source=b"hello from danvas\n", filename="hello.txt",
                     text="Download hello.txt", label="Download", color=SLATE, below=img)

# ── Upload ─────────────────────────────────────────────────────────────────────
up = canvas.upload("up", text="Choose a file", label="Upload", color=PLUM, below=dl)

# ── File browser ───────────────────────────────────────────────────────────────
fb = canvas.file_browser("fb", root=".", label="File browser", color=ROSE, below=up)

# ── Webview ────────────────────────────────────────────────────────────────────
wv = canvas.webview("https://example.com", name="wv", label="Webview",
                    color=AMBER, below=fb)

# ── Inspector ──────────────────────────────────────────────────────────────────
ins = canvas.inspector(name="ins", label="Inspector", color=TEAL, below=wv)

# ── Live-plot feed ─────────────────────────────────────────────────────────────
@canvas.background
def tick():
    t = 0.0
    while True:
        lp.push({"signal": math.sin(t * 0.5 + random.uniform(-0.1, 0.1))})
        t += 0.1
        time.sleep(0.1)

canvas.serve(port=8001, tunnel=True)
