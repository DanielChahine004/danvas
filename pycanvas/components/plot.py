"""Plot: a convenience wrapper over Custom for interactive Plotly charts.

``update`` accepts either a Plotly figure (rendered with ``to_html``) or a
raw HTML string, then displays it in the same sandboxed iframe Custom uses.
"""

from .custom import Custom

_EMPTY = (
    "<body style='margin:0;font-family:system-ui;color:#888;"
    "display:flex;align-items:center;justify-content:center;height:100%'>"
    "no data yet</body>"
)

# Keep the chart filling the panel on resize. Plotly's ``responsive: true`` only
# listens to the iframe *window*'s resize, which doesn't reliably fire when the
# tldraw panel (and thus the iframe element) is resized — so a taller panel left
# the chart stuck at its load-time pixel height with empty space below. A
# ResizeObserver on the graph div catches every box change and re-fits the plot,
# the in-iframe analog of the native LivePlot path's observer. (Resizing the plot
# reflows the SVG inside the div, not the div's own 100%×100% box, so no loop.)
_RESIZE_SCRIPT = (
    "<script>(function(){"
    "var raf=null;"
    "function fit(d){if(raf)return;raf=requestAnimationFrame(function(){"
    "raf=null;if(window.Plotly)window.Plotly.Plots.resize(d);});}"
    "function arm(){"
    "var d=document.querySelector('.plotly-graph-div');"
    "if(!d||!window.Plotly||!window.ResizeObserver){return setTimeout(arm,50);}"
    "new ResizeObserver(function(){fit(d);}).observe(d);"
    "}"
    "arm();"
    "})();</script>"
)


class Plot(Custom):
    # Reuses the Custom (pcHtml) shape on the frontend.
    component = "Custom"
    default_w = 560
    default_h = 420

    def __init__(self, name="plot", label=None, w=None, h=None):
        super().__init__(html=_EMPTY, name=name, label=label, w=w, h=h)

    def update(self, figure):
        """Display a Plotly figure or an HTML string."""
        super().update(self._to_html(figure))

    def _wrap(self, html):
        """Lead the iframe document with a doctype so it renders in standards
        mode. Plotly's ``full_html`` omits the doctype and ``Custom._wrap`` then
        prepends the canvas helper script, so without this the document is
        quirks-mode — where percentage heights and box-sizing differ, leaving the
        chart sized oddly (a slight zoom/clip, most visible on a small or mobile
        panel). Inherited by :class:`~pycanvas.Histogram`."""
        return "<!DOCTYPE html>\n" + super()._wrap(html)

    @staticmethod
    def _to_html(figure):
        if isinstance(figure, str):
            return figure
        to_html = getattr(figure, "to_html", None)
        if callable(to_html):
            # full_html so Plotly's JS runs inside the sandboxed iframe.
            # ``responsive`` makes the chart track the iframe's size. The injected
            # CSS gives <html>/<body> a real height so the chart's ``height:100%``
            # resolves to the panel instead of collapsing to Plotly's fixed
            # default height (which left the chart clipped / "scaled weirdly",
            # worst on a small or mobile panel).
            html = figure.to_html(include_plotlyjs="cdn", full_html=True,
                                  config={"responsive": True})
            # ``body>div`` targets the unstyled wrapper Plotly's ``to_html`` puts
            # around the graph div: without giving *it* a height the graph div's
            # ``height:100%`` resolves against an auto-height parent and collapses
            # to Plotly's fixed default (~450px), so the chart never fills (or
            # tracks) the panel. Carrying the full-height chain html→body→wrapper→
            # graph div is what lets the chart size to the iframe.
            return html.replace(
                "</head>",
                "<style>html,body{height:100%;margin:0;overflow:hidden}"
                "body>div{height:100%}"
                ".plotly-graph-div{height:100%;width:100%}</style>"
                f"{_RESIZE_SCRIPT}</head>",
                1,
            )
        raise TypeError("Plot.update expects a Plotly figure or an HTML string")
