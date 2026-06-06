"""LivePlot: efficient streaming line plot for live telemetry.

Unlike ``Plot`` (which reloads a Plotly iframe per update), ``LivePlot`` keeps a
rolling buffer of samples and pushes just the data arrays; the frontend applies
them with ``Plotly.react`` on a chart that stays mounted — smooth at high rates.

    plot = canvas.insert(pycanvas.LivePlot("servos", traces=["s1", "s2"]))
    plot.push({"s1": 90, "s2": 45})   # call repeatedly from your loop
"""

from collections import deque

from .base import BaseComponent

_DEFAULT_LAYOUT = {
    "margin": {"l": 40, "r": 15, "t": 15, "b": 30},
    "showlegend": True,
    "legend": {"orientation": "h"},
}


class LivePlot(BaseComponent):
    component = "LivePlot"

    def __init__(
        self,
        name="live plot",
        traces=None,
        max_points=300,
        mode="lines",
        layout=None,
        width=560,
        height=380,
        label=None,
    ):
        super().__init__(name=name, label=label, w=width, h=height)
        self._max = max_points
        self._mode = mode
        self._layout = layout or {}
        self._traces = []
        self._x = {}
        self._y = {}
        self._counter = 0
        for name in traces or []:
            self._ensure(name)

    def _ensure(self, name):
        if name not in self._y:
            self._traces.append(name)
            self._x[name] = deque(maxlen=self._max)
            self._y[name] = deque(maxlen=self._max)

    def push(self, sample, x=None):
        """Append one sample per trace, e.g. ``push({"s1": 90, "s2": 45})``.

        ``x`` defaults to an auto-incrementing sample index.
        """
        with self._lock:
            self._counter += 1
            xi = self._counter if x is None else x
            for name, yv in sample.items():
                self._ensure(name)
                self._x[name].append(xi)
                self._y[name].append(yv)
            payload = self._payload()
        self._send_update({"plot": payload})

    # Alias: every other component sends data via ``update()``. LivePlot's
    # natural verb is ``push`` (append one sample), but accept ``update`` too so
    # the API reads consistently across components.
    def update(self, sample, x=None):
        """Alias for :meth:`push` — append one sample per trace."""
        return self.push(sample, x)

    def clear(self):
        with self._lock:
            for name in self._traces:
                self._x[name].clear()
                self._y[name].clear()
            payload = self._payload()
        self._send_update({"plot": payload})

    def _payload(self):
        data = [
            {
                "x": list(self._x[name]),
                "y": list(self._y[name]),
                "name": name,
                "mode": self._mode,
                "type": "scatter",
            }
            for name in self._traces
        ]
        layout = {**_DEFAULT_LAYOUT, **self._layout}
        return {"data": data, "layout": layout}

    def state_payload(self):
        # Send the current buffer so a (re)connecting client renders at once.
        with self._lock:
            return {"plot": self._payload()}
