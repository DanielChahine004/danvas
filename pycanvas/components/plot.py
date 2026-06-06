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


class Plot(Custom):
    # Reuses the Custom (pcHtml) shape on the frontend.
    component = "Custom"

    def __init__(self, name="plot", label=None, width=560, height=420):
        super().__init__(html=_EMPTY, name=name, label=label, width=width,
                         height=height)

    def update(self, figure):
        """Display a Plotly figure or an HTML string."""
        super().update(self._to_html(figure))

    @staticmethod
    def _to_html(figure):
        if isinstance(figure, str):
            return figure
        to_html = getattr(figure, "to_html", None)
        if callable(to_html):
            # full_html so Plotly's JS runs inside the sandboxed iframe.
            return figure.to_html(include_plotlyjs="cdn", full_html=True)
        raise TypeError("Plot.update expects a Plotly figure or an HTML string")
