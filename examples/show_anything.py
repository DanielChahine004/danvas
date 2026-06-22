"""canvas.show(value) — auto-panel for every major Python data type.

Each group demonstrates how show() picks the right component automatically.
Run:  python examples/show_anything.py
"""

import base64
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go

import danvas

canvas = danvas.Canvas()


# ── Section header ─────────────────────────────────────────────────────────────
header = canvas.show(
    "# `canvas.show()` — one call, any type\n"
    "Hover a panel header to see its component type. "
    "Each value below was passed to `show()` without choosing a component.\n\n"
    "**Click** any `{` or `[` in a JSON tree to collapse/expand it.",
    label="intro",
)

# ── Scalars → Label ────────────────────────────────────────────────────────────
s_int   = canvas.show(42,            label="int",   below=header)
s_float = canvas.show(math.pi,       label="float",   right_of=s_int)
s_bool  = canvas.show(True,          label="bool",   right_of=s_float)
s_none  = canvas.show(None,          label="None",   right_of=s_bool)
s_cx    = canvas.show(3 + 4j,        label="complex",   right_of=s_none)

# ── Strings ────────────────────────────────────────────────────────────────────
# Short one-liner → bold Label.
s_short = canvas.show("Hello, world!", label="str — short", below=s_int)

# Markdown → Markdown panel.
s_md = canvas.show(
    "## Markdown string\n"
    "Supports **bold**, `code`, *italics*, lists:\n\n"
    "- Mean: `μ = (1∕N) · Σ xᵢ`\n"
    "- Std:  `σ = √( (1∕N) · Σ (xᵢ−μ)² )`",
    label="str — markdown", below=s_short,
)

# HTML → rendered HTML panel.
s_html = canvas.show(
    "<div style='font:13px sans-serif;padding:4px'>"
    "<b style='color:#6366f1'>HTML string</b> — rendered directly.<br>"
    "<progress value='72' max='100' style='width:140px;margin-top:6px'></progress> 72%"
    "</div>",
    label="str — html", below=s_md,
)

# Web URL → clickable Markdown link.
s_url = canvas.show(
    "https://github.com/DanielChahine004/danvas",
    label="str — url", below=s_html,
)

# ── Dicts ──────────────────────────────────────────────────────────────────────
# Flat dict (all scalar values) → key/value Table.
d_flat = canvas.show(
    {"lr": 3e-4, "epochs": 40, "batch_size": 64, "dropout": 0.2, "optimizer": "adam"},
    label="dict — flat (key/value table)", below=s_url, editable=True,
)

# Nested/mixed dict → collapsible JSON tree.
d_nested = canvas.show(
    {
        "model": {"layers": [128, 64, 32], "activation": "relu"},
        "dataset": {"name": "cifar10", "split": 0.8},
        "tags": ["v2", "experiment"],
    },
    label="dict — nested (JSON tree)", below=d_flat,
)

# ── Lists ──────────────────────────────────────────────────────────────────────
# Flat list of scalars → single-column Table.
l_flat = canvas.show(
    [42.0, 890, 324, 3214, 214, 124, 12],
    label="list — scalars (table)", below=d_nested,
)

# List of dicts → Table (columns = union of keys).
l_records = canvas.show(
    [
        {"sensor": "temp",     "value": 21.4, "unit": "°C"},
        {"sensor": "humidity", "value": 48,   "unit": "%"},
        {"sensor": "pressure", "value": 1013, "unit": "hPa"},
    ],
    label="list — records (table)", below=l_flat,
)

# List of lists → Table with synthesized 0,1,2 headers.
l_matrix = canvas.show(
    [[1, 2, 3], [4, 5, 6], [7, 8, 9]],
    label="list — matrix (table)", below=l_records,
)

# Set → JSON tree.
l_set = canvas.show(
    {"alpha", "beta", "gamma", "delta"},
    label="set (JSON tree)", below=l_matrix,
)

# ── Bytes (image magic bytes) → Image ──────────────────────────────────────────
_svg = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='80'>"
    "<rect width='200' height='80' rx='10' fill='#0ea5e9'/>"
    "<text x='100' y='47' font-size='16' fill='white' "
    "text-anchor='middle' font-family='sans-serif'>bytes → image</text></svg>"
)
b_img = canvas.show(
    "data:image/svg+xml;base64," + base64.b64encode(_svg.encode()).decode(),
    label="str — data URI image", below=l_set,
)

# ── NumPy ──────────────────────────────────────────────────────────────────────
# 1-D array → single-column Table.
np_1d = canvas.show(
    np.array([1.1, 2.2, 3.3, 4.4, 5.5]),
    label="ndarray — 1-D (table)", below=b_img,
)

# 2-D float array → row-matrix Table.
np_2d = canvas.show(
    np.random.default_rng(0).standard_normal((6, 4)).round(3),
    label="ndarray — 2-D float (table)", below=np_1d,
)

# 2-D uint8 array → greyscale Image.
rng = np.random.default_rng(1)
np_img = canvas.show(
    rng.integers(0, 255, (64, 128), dtype=np.uint8),
    label="ndarray — uint8 (image)", below=np_2d,
)

# 3-D RGB array → colour Image.
xs = np.linspace(0, 2 * math.pi, 128)
r = (np.sin(xs) * 127 + 128).astype(np.uint8)
g = (np.cos(xs) * 127 + 128).astype(np.uint8)
b = np.zeros(128, dtype=np.uint8)
np_rgb = canvas.show(
    np.stack([r, g, b], axis=1)[np.newaxis, :, :].repeat(64, axis=0),
    label="ndarray — RGB (image)", below=np_img,
)

# ── Matplotlib figure → Image ──────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(5, 2.5))
t = np.linspace(0, 4 * math.pi, 300)
ax.plot(t, np.sin(t), label="sin")
ax.plot(t, np.cos(t), label="cos", linestyle="--")
ax.legend(fontsize=8)
ax.set_title("Matplotlib figure → Image")
mpl_panel = canvas.show(fig, label="matplotlib figure", below=np_rgb)

# ── Plotly figure → Plot ───────────────────────────────────────────────────────
xs = list(range(20))
plotly_fig = go.Figure([
    go.Scatter(x=xs, y=[math.sin(x * 0.4) for x in xs], mode="lines+markers", name="sin"),
    go.Scatter(x=xs, y=[math.cos(x * 0.4) for x in xs], mode="lines", name="cos"),
])
plotly_fig.update_layout(title="Plotly figure → Plot", margin=dict(l=30, r=10, t=40, b=30))
plotly_panel = canvas.show(plotly_fig, label="plotly figure", below=mpl_panel)

# ── Custom _repr_html_ → HTML panel ────────────────────────────────────────────
class Gauge:
    def __init__(self, label, value, total=100, color="#22c55e"):
        self.label, self.value, self.total, self.color = label, value, total, color

    def _repr_html_(self):
        pct = self.value / self.total * 100
        return (
            f"<div style='font:13px sans-serif;padding:4px'>"
            f"<b>{self.label}</b> — {self.value}/{self.total}"
            f"<div style='background:#e2e8f0;border-radius:6px;height:14px;"
            f"width:220px;margin-top:4px'>"
            f"<div style='background:{self.color};height:14px;border-radius:6px;"
            f"width:{pct * 2.2:.0f}px'></div></div></div>"
        )

repr_panel = canvas.show(
    Gauge("accuracy", 87, color="#6366f1"),
    label="_repr_html_ object", below=plotly_panel,
)

# ── Re-show: replace a panel in place under the same name ─────────────────────
canvas.show("starting…",  name="live_demo", label="live update")
canvas.show("ready ✔",    name="live_demo", label="live update")  # replaces above

print("canvas.show() routed each value to its best panel — no component chosen by hand.")
canvas.serve(port=8000, hot_reload=True)
