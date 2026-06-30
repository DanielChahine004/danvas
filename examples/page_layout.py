"""Document-page layout: the same canvas engine, scrolled like a web page.

``serve(view={"navigation": "scroll_y"})`` turns the infinite plane into a vertical
scroll feed at a locked zoom; ``below=`` chains panels into one centred column that
fits the viewport width (scaling down on a narrow phone, capped at 1:1 on a wide
screen) and re-centres on resize. On touch it scrolls like a web page — drag from
non-interactive content (text, padding, gaps) to scroll; touch a control (slider,
plot, table filter, chat field) first to interact with it. Pinch zooms in, bounded
to the column.

So you get a structured, scrollable, multi-section page without leaving danvas —
and can drop the same panels onto the infinite canvas whenever you need to.
"""
import math

import plotly.graph_objects as go

import danvas

canvas = danvas.Canvas()

W = 420  # content-column width (page units; CSS px at the locked zoom)

title = canvas.markdown(
    "# danvas as a page\n"
    "This is the **same engine** as the infinite canvas — just "
    "`navigation=\"scroll_y\"`. Scroll vertically like a document; panels stack in a "
    "centred column.",
    name="title", w=W,
)
intro = canvas.markdown(
    "## Section 1 — controls\n"
    "Inputs sync across viewers in real time (open this on two devices).",
    name="intro", w=W, below=title,
)
gain = canvas.slider("gain", min=0, max=100, default=40, label="Gain", w=W, below=intro)
status = canvas.label("status", "drag the slider →", label="Status", w=W, below=gain)

plot_hdr = canvas.markdown("## Section 2 — a chart", name="plot_hdr", w=W, below=status)
chart = canvas.plot("chart", label="signal", w=W, below=plot_hdr)
xs = list(range(40))
chart.update(go.Figure(go.Scatter(x=xs, y=[math.sin(x * 0.3) for x in xs],
                                   mode="lines", name="sin")))

tbl_hdr = canvas.markdown("## Section 3 — a table", name="tbl_hdr", w=W, below=chart)
table = canvas.table(
    {"Metric": ["loss", "acc", "lr"], "Value": [0.21, 0.94, 0.001]},
    name="metrics", w=W, below=tbl_hdr,
)

chat_hdr = canvas.markdown("## Section 4 — chat", name="chat_hdr", w=W, below=table)
chat = canvas.chat("room", label="Chat", w=W, below=chat_hdr)


@gain.on_change
def _(v):
    status.update(f"gain = {v:.0f}")


canvas.serve(port=8000, view={"navigation": "scroll_y"})
