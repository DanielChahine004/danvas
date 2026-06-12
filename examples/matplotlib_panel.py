"""Matplotlib panel: render a figure to a PNG and show it in a Custom panel.

A slider controls the wave frequency; each change re-renders the figure and
pushes fresh HTML (an <img> with a base64 PNG) into the panel.
"""

import base64
import io

import matplotlib

matplotlib.use("Agg")  # headless backend; no GUI window
import matplotlib.pyplot as plt
import numpy as np

import pycanvas


def render_plot(freq):
    """Return an HTML <img> string holding a base64 PNG of the plot."""
    x = np.linspace(0, 2 * np.pi, 500)
    fig, ax = plt.subplots(figsize=(4.4, 3.0), dpi=100)
    ax.plot(x, np.sin(freq * x), color="#2563eb", lw=2)
    ax.set_title(f"sin({freq}·x)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return (
        '<body style="margin:0">'
        f'<img src="data:image/png;base64,{b64}" '
        'style="width:100%;height:auto;display:block" />'
        "</body>"
    )


canvas = pycanvas.Canvas()

freq = canvas.insert(pycanvas.Slider("frequency", min=1, max=10, default=3))
plot = canvas.insert(
    pycanvas.Custom(html=render_plot(3), name="matplotlib", w=460, h=380)
)


@freq.on_change
def on_freq(value):
    plot.update(render_plot(int(value)))


print("Drag the 'frequency' slider to re-render the matplotlib plot.")
canvas.serve(port=8000)
