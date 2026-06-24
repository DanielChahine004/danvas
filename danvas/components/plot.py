"""Plot: a React panel rendering an interactive Plotly chart.

``update`` accepts a Plotly figure object and renders it natively inside the
panel (no iframe, no CDN fetch — Plotly is bundled with the app). The figure
is stored in the panel's shape props so it replays when a client reconnects.
"""

from .react import React as _React

_SOURCE = '''
function Component({ canvas, props }) {
  const Plotly = libs.plotly;
  const nodeRef = React.useRef(null);
  const fig = props && props._fig;
  React.useEffect(() => {
    const node = nodeRef.current;
    if (!node) return;
    let raf = null;
    let ro = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => {
        if (raf) return;
        raf = requestAnimationFrame(() => { raf = null; if (nodeRef.current) Plotly.Plots.resize(nodeRef.current); });
      });
      ro.observe(node);
    }
    return () => {
      if (ro) ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
      if (nodeRef.current) Plotly.purge(nodeRef.current);
    };
  }, []);
  React.useEffect(() => {
    const node = nodeRef.current;
    if (!node || !fig) return;
    Plotly.react(node, fig.data || [], fig.layout || {}, { responsive: true, displayModeBar: false });
  });
  return (
    <div style={{ flex: 1, width: "100%", minHeight: 0, position: "relative" }}>
      {!fig && (
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--pc-muted, #9ca3af)", fontSize: 13 }}>
          no data yet
        </div>
      )}
      <div ref={nodeRef} style={{ width: "100%", height: "100%" }} />
    </div>
  );
}
'''


class Plot(_React):
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
