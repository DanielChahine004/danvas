"""Histogram: a distribution that evolves over training — TensorBoard's HISTOGRAMS.

Call :meth:`add` each time you want to record a distribution (a layer's weights
or gradients once per epoch, say). The panel shows how that distribution shifts
across steps — as a density **heatmap** (step on x, value-bin on y) by default,
or translucent **overlay** lines layered front-to-back. Reuses ``Plot``'s Plotly
rendering path, so it needs ``plotly`` only when actually used, like ``Plot``.

    h = canvas.histogram("weights/layer1", bins=40)
    for epoch in range(epochs):
        h.add(model.layer1.weight.detach().numpy(), step=epoch)
"""

from collections import deque

from .plot import Plot


class Histogram(Plot):
    component = "Custom"  # reuses the Custom (pcHtml) shape, same as Plot
    default_w = 560
    default_h = 360

    def __init__(self, name="histogram", bins=30, mode="heatmap",
                 value_range=None, max_steps=200, label=None, w=None, h=None):
        super().__init__(name=name, label=label, w=w, h=h)
        if mode not in ("heatmap", "overlay"):
            raise ValueError(f"mode must be 'heatmap' or 'overlay', got {mode!r}")
        self._bins = bins
        self._mode = mode
        self._value_range = value_range
        self._edges = None  # fixed on the first add so every step shares bins
        self._records = deque(maxlen=max_steps)  # (step, density-counts)

    def add(self, values, step=None):
        """Record one distribution. ``step`` defaults to the record count."""
        import numpy as np

        values = np.asarray(values, dtype=float).ravel()
        with self._lock:
            if self._edges is None:
                lo, hi = self._value_range or (float(values.min()),
                                               float(values.max()))
                if hi <= lo:
                    hi = lo + 1.0
                self._edges = np.linspace(lo, hi, self._bins + 1)
            if step is None:
                step = len(self._records)
            counts, _ = np.histogram(values, bins=self._edges, density=True)
            self._records.append((step, counts))
            figure = self._figure()
        super().update(figure)

    def _figure(self):
        import numpy as np
        import plotly.graph_objects as go

        centers = (self._edges[:-1] + self._edges[1:]) / 2
        steps = [s for s, _ in self._records]
        if self._mode == "overlay":
            fig = go.Figure()
            n = len(self._records)
            for i, (s, counts) in enumerate(self._records):
                fig.add_trace(go.Scatter(
                    x=centers, y=counts, mode="lines", name=str(s),
                    line={"width": 1}, opacity=0.25 + 0.6 * (i + 1) / n,
                    showlegend=False,
                ))
            fig.update_xaxes(title_text="value")
        else:  # heatmap: step (x) vs value-bin (y), density as colour
            z = np.array([counts for _, counts in self._records]).T
            fig = go.Figure(go.Heatmap(
                x=steps, y=centers, z=z, colorscale="Blues", showscale=False,
            ))
            fig.update_xaxes(title_text="step")
            fig.update_yaxes(title_text="value")
        # No in-chart title — the panel's card header already shows the name.
        fig.update_layout(margin={"l": 45, "r": 15, "t": 15, "b": 35})
        return fig
