"""Plot: a React panel rendering an interactive Plotly chart.

``update`` accepts a Plotly figure object and renders it natively inside the
panel (no iframe, no CDN fetch — Plotly is bundled with the app). The figure
is stored in the panel's shape props so it replays when a client reconnects.
"""

from .react import React as _React

from . import _jsx

_SOURCE = _jsx.load("plot")


class Plot(_React):
    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {"_fig": "object -- a Plotly figure {data, layout}"},
        "updates": {"data_patch": "typically {_fig: <figure>}"},
        "events": [],
    }
    default_w = 560
    default_h = 420

    def __init__(self, name="plot", label=None, w=None, h=None, color=None):
        w = w if w is not None else 560
        h = h if h is not None else 420
        super().__init__(source=_SOURCE, scope=["plotly"], name=name, label=label,
                         w=w, h=h, color=color)

    def update(self, figure):
        """Display a Plotly figure (``plotly.graph_objects.Figure`` or similar).

        Stores the figure in the panel's props so it persists and replays when a
        client reconnects. The chart renders natively in the panel — no iframe.
        """
        fig_dict = self._to_dict(figure)
        _React.update(self, _fig=fig_dict)

    @staticmethod
    def _to_dict(figure):
        import json as _json
        # to_json() serializes numpy arrays; to_plotly_json() leaves them raw.
        to_json = getattr(figure, "to_json", None)
        if callable(to_json):
            return _json.loads(to_json())
        if isinstance(figure, dict):
            return figure
        raise TypeError(
            "Plot.update expects a Plotly figure (plotly.graph_objects.Figure) "
            "or a dict with 'data' and 'layout' keys. "
            "For raw HTML, use canvas.custom() instead.")
