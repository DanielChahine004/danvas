"""LivePlot: efficient streaming line plot for live telemetry.

Unlike ``Plot`` (which reloads a Plotly iframe per update), ``LivePlot`` keeps a
rolling buffer of samples and pushes just the data arrays; the frontend applies
them with ``Plotly.react`` on a chart that stays mounted — smooth at high rates.

    plot = canvas.insert(pycanvas.LivePlot("servos", traces=["s1", "s2"]))
    plot.push({"s1": 90, "s2": 45})   # call repeatedly from your loop

Traces don't have to be declared up front: ``push`` a key it hasn't seen and a
new trace appears automatically, so ``traces=`` is just an optional way to fix
the legend order. ``smoothing`` adds a TensorBoard-style smoothed line over a
faint raw one.
"""

from collections import deque

from .base import BaseComponent

_DEFAULT_LAYOUT = {
    "margin": {"l": 40, "r": 15, "t": 15, "b": 30},
    "showlegend": True,
    "legend": {"orientation": "h"},
}

# Stable per-trace colours so a trace's raw (faint) and smoothed (bold) lines
# share a hue when ``smoothing`` is on. Plotly's own default qualitative set.
_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def _ema(values, weight):
    """Exponential moving average with debiasing (matches TensorBoard).

    ``weight`` in [0, 1): higher is smoother. The debias term divides out the
    cold-start bias toward zero so the first points aren't dragged down.
    """
    smoothed = []
    last = 0.0
    debias = 0.0
    for v in values:
        last = last * weight + (1 - weight) * v
        debias = debias * weight + (1 - weight)
        smoothed.append(last / debias if debias else v)
    return smoothed


class LivePlot(BaseComponent):
    component = "LivePlot"
    default_w = 560
    default_h = 380

    def __init__(
        self,
        name="live plot",
        traces=None,
        max_points=300,
        mode="lines",
        layout=None,
        smoothing=0.0,
        w=None,
        h=None,
        label=None,
    ):
        if not 0 <= smoothing < 1:
            raise ValueError(f"smoothing must be in [0, 1), got {smoothing!r}")
        size = {k: v for k, v in (("w", w), ("h", h)) if v is not None}
        super().__init__(name=name, label=label, **size)
        self._max = max_points
        self._mode = mode
        self._layout = layout or {}
        self._smoothing = smoothing
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

        A key not seen before starts a new trace on the fly. ``x`` defaults to an
        auto-incrementing sample index (pass it to use a real step/epoch number).

        On the wire this streams just the new point(s) as an ``extend`` delta —
        the frontend appends them with ``Plotly.extendTraces`` rather than
        re-diffing the whole figure — so a long run stays O(1) per push instead
        of re-sending the entire rolling buffer every time. The full buffer is
        still kept server-side and replayed in one shot to any (re)connecting
        client (:meth:`state_payload`). Two cases fall back to a full ``plot``
        frame because a delta can't express them: a brand-new trace (the figure
        gains a curve) and the ``"latest"`` queue policy (which drops stale
        pending frames, so it needs whole-snapshot replace semantics — an append
        delta would lose the dropped points; the default ``"fifo"`` delivers
        every point in order, where the delta is both safe and the whole point).
        """
        with self._lock:
            self._counter += 1
            xi = self._counter if x is None else x
            new_trace = any(name not in self._y for name in sample)
            for name, yv in sample.items():
                self._ensure(name)
                self._x[name].append(xi)
                self._y[name].append(yv)
            if new_trace or self._queue == "latest":
                payload = {"plot": self._payload()}
            else:
                payload = {"plot_extend": self._extend_payload(sample, xi)}
        self._stream(payload)

    def _stream(self, payload):
        """Send one stream frame with backpressure that fits a live plot.

        The default ``"fifo"`` *coalesces* under load (see
        ``Bridge.broadcast_conflated`` ``coalesce=``): when the producer outruns
        the client's redraw rate, pending points fold into a single catch-up
        frame instead of queuing — so the curve stays complete *and* the UI stays
        live, rather than lagging further behind every push. ``"latest"`` keeps
        the older drop-stale behaviour (only the newest whole-figure snapshot
        survives), for a gauge-style plot where intermediate frames don't matter.
        """
        if self._bridge is None:
            return
        msg = {"type": "update", "id": self.id, "payload": payload}
        self._bridge.broadcast_conflated(
            self.id, msg=msg, coalesce=(self._queue != "latest"))

    def _extend_payload(self, sample, xi):
        """The ``extend`` delta for the points just appended: the Plotly trace
        index(es) to grow and the new x/y for each.

        Mirrors the trace order :meth:`_payload` builds, so the frontend's trace
        indices line up: without smoothing, logical trace *i* is Plotly trace
        *i*; with smoothing each logical trace is two Plotly traces (faint raw at
        ``2i``, bold smoothed at ``2i+1``), so a point extends both — the raw with
        its value, the smoothed with the latest EMA over the current window
        (recomputed like ``_payload`` so a streaming client and a reconnecting one
        agree). ``max`` lets the frontend cap each trace to the same rolling
        window as the server's deques.
        """
        indices, xs_out, ys_out = [], [], []
        for name in sample:
            i = self._traces.index(name)
            if self._smoothing > 0:
                indices.append(2 * i)
                xs_out.append([xi])
                ys_out.append([self._y[name][-1]])
                indices.append(2 * i + 1)
                xs_out.append([xi])
                ys_out.append([_ema(list(self._y[name]), self._smoothing)[-1]])
            else:
                indices.append(i)
                xs_out.append([xi])
                ys_out.append([self._y[name][-1]])
        return {"indices": indices, "x": xs_out, "y": ys_out, "max": self._max}

    # Alias: every other component sends data via ``update()``. LivePlot's
    # natural verb is ``push`` (append one sample), but accept ``update`` too so
    # the API reads consistently across components.
    def update(self, sample, x=None):
        """Alias for :meth:`push` — append one sample per trace."""
        return self.push(sample, x)

    @property
    def smoothing(self):
        """EMA weight for the smoothed overlay (0 disables); settable live."""
        return self._smoothing

    @smoothing.setter
    def smoothing(self, weight):
        if not 0 <= weight < 1:
            raise ValueError(f"smoothing must be in [0, 1), got {weight!r}")
        with self._lock:
            self._smoothing = weight
            payload = self._payload()
        self._stream({"plot": payload})

    def clear(self):
        with self._lock:
            for name in self._traces:
                self._x[name].clear()
                self._y[name].clear()
            payload = self._payload()
        self._stream({"plot": payload})

    def _payload(self):
        data = []
        for i, name in enumerate(self._traces):
            xs = list(self._x[name])
            ys = list(self._y[name])
            if self._smoothing > 0:
                # Faint raw line + bold smoothed line, sharing a palette colour —
                # the TensorBoard scalar look. Both built server-side, so the
                # frontend stays a dumb Plotly.react sink (no rebuild needed).
                color = _PALETTE[i % len(_PALETTE)]
                data.append({
                    "x": xs, "y": ys, "name": name, "mode": self._mode,
                    "type": "scatter", "line": {"color": color, "width": 1},
                    "opacity": 0.3, "showlegend": False, "hoverinfo": "skip",
                })
                data.append({
                    "x": xs, "y": _ema(ys, self._smoothing), "name": name,
                    "mode": self._mode, "type": "scatter",
                    "line": {"color": color, "width": 2},
                })
            else:
                data.append({
                    "x": xs, "y": ys, "name": name,
                    "mode": self._mode, "type": "scatter",
                })
        layout = {**_DEFAULT_LAYOUT, **self._layout}
        # A title needs head-room, or Plotly draws it on top of the plot. The
        # tight default top margin suits the usual title-less plot (the panel's
        # card header captions it), so only reserve the space when a title is set.
        if layout.get("title"):
            margin = dict(layout.get("margin", {}))
            margin["t"] = max(margin.get("t", 0), 40)
            layout["margin"] = margin
        return {"data": data, "layout": layout}

    def state_payload(self):
        # Send the current buffer so a (re)connecting client renders at once.
        with self._lock:
            return {"plot": self._payload()}
