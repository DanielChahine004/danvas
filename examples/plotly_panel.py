"""Interactive Plotly panel: a fully interactive chart inside a Custom panel.

Plotly's ``to_html()`` output runs its own JS in the sandboxed iframe, so the
chart stays zoomable / pannable / hoverable. A slider rebuilds the figure and
pushes new HTML; the chart itself remains interactive between updates.
"""

import numpy as np
import plotly.graph_objects as go

import pycanvas


def render_chart(points):
    x = np.linspace(0, 10, int(points))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=np.sin(x), mode="lines", name="sin"))
    fig.add_trace(go.Scatter(x=x, y=np.cos(x), mode="lines", name="cos"))
    fig.update_layout(
        title=f"Interactive Plotly ({int(points)} points)",
        margin=dict(l=40, r=20, t=40, b=30),
        autosize=True,
    )
    # include_plotlyjs='cdn' keeps the payload small; the iframe loads plotly
    # from the CDN and renders an interactive chart.
    return fig.to_html(include_plotlyjs="cdn", full_html=True)


canvas = pycanvas.Canvas()

resolution = canvas.insert(
    pycanvas.Slider("resolution", min=10, max=400, default=120)
)
chart = canvas.insert(
    pycanvas.Custom(html=render_chart(120), name="plotly", w=560, h=420)
)


@resolution.on_change
def on_res(value):
    chart.update(render_chart(value))


print("Hover / zoom / pan the chart directly; drag the slider to rebuild it.")
canvas.serve(port=8000)
