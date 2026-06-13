"""canvas.show(value) — let PyCanvas auto-pick the panel for any value.

You don't have to choose a component. ``show()`` inspects the value and inserts
the panel that renders it best — a DataFrame becomes a table, a figure an image,
Markdown text renders, a dict prints as JSON, and anything with a Jupyter rich
repr (``_repr_html_`` / ``_repr_png_``) shows its rich view. Works in plain
scripts (no IPython needed) and in notebooks alike.

Run:  python examples/show_anything.py
"""

import base64
import math

import pycanvas

canvas = pycanvas.Canvas()

# A string of Markdown -> rendered text. Equations are written in Unicode math
# (μ, σ², √, Σ, ·) so they render without a LaTeX/MathJax dependency — the
# Markdown panel converts Markdown to HTML, it doesn't typeset TeX.
canvas.show(
    "# Dashboard\n"
    "A few values, each **auto-rendered** — no component chosen by hand.\n\n"
    "## Formulas\n"
    "- Mean:  `μ = (1∕N) · Σ xᵢ`\n"
    "- Std dev:  `σ = √( (1∕N) · Σ (xᵢ − μ)² )`\n"
    "- Mass–energy:  `E = m · c²`\n"
    "- Gaussian:  `f(x) = (1 ∕ √(2πσ²)) · e^(−(x−μ)² ∕ 2σ²)`\n",
    label="notes", draggable=False, resizable=False, grabbable=True)

# Records (list of dicts) -> a Table.
canvas.show(
    [
        {"sensor": "temp", "value": 21.4, "unit": "C"},
        {"sensor": "humidity", "value": 48, "unit": "%"},
        {"sensor": "pressure", "value": 1013, "unit": "hPa"},
    ],
    label="readings",
)

# A matrix (list of rows) -> a Table with synthesized 0,1,2 headers.
canvas.show([[1, 2, 3], [4, 5, 6], [7, 8, 9]], label="matrix")

# A flat list of scalars -> pretty JSON (a list of *rows* would be a Table).
canvas.show([42.0, 890, 324, 3214, 214, 124, 12], label="samples")

# A nested structure -> pretty JSON.
canvas.show({"status": "ok", "uptime_s": 3600, "tags": ["a", "b"]},
            label="state")

# A set still renders as JSON (sorted into a list under the hood).
canvas.show({"alpha", "beta", "gamma"}, label="tags")

# Plain scalars -> Labels.
canvas.show(round(math.pi, 5), label="pi")
canvas.show(True, label="enabled")

# Any object with a Jupyter-style rich repr renders its HTML, exactly as a
# notebook would — here a tiny inline battery gauge, no dependencies.
class Battery:
    def __init__(self, pct):
        self.pct = pct

    def _repr_html_(self):
        return (
            "<div style='font:13px sans-serif'>"
            f"battery — {self.pct}%"
            "<div style='background:#e2e8f0;border-radius:6px;height:14px;"
            "width:160px;margin-top:4px'>"
            f"<div style='background:#22c55e;height:14px;border-radius:6px;"
            f"width:{self.pct * 1.6:.0f}px'></div></div></div>"
        )

canvas.show(Battery(72), label="power")

# An inline SVG string is detected as HTML and rendered as a vector graphic.
canvas.show(
    "<svg width='150' height='80' xmlns='http://www.w3.org/2000/svg'>"
    "<circle cx='40' cy='40' r='28' fill='#6366f1'/>"
    "<circle cx='90' cy='40' r='28' fill='#ec4899' fill-opacity='0.8'/>"
    "</svg>",
    label="logo")

# A bare web URL becomes a clickable link instead of dead text.
canvas.show("https://github.com/DanielChahine004/pycanvas/tree/main", label="repo")

# A self-contained data-URI image renders as the image. Base64-encode the SVG so
# its quotes/spaces can't collide with the src="" attribute it ends up in.
_pic_svg = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='150' height='80'>"
    "<rect width='150' height='80' fill='#0ea5e9'/>"
    "<text x='75' y='46' font-size='16' fill='white' "
    "text-anchor='middle' font-family='sans-serif'>image</text></svg>"
)
canvas.show(
    "data:image/svg+xml;base64," + base64.b64encode(_pic_svg.encode()).decode(),
    label="picture")

# Re-show under the same name to replace a panel in place (e.g. in a loop):
canvas.show("starting…", name="live", label="status")
canvas.show("ready ✔", name="live", label="status")  # replaces it

print("Each value was auto-rendered by canvas.show — no component chosen by hand.")
canvas.serve(port=8000, hot_reload=True)
