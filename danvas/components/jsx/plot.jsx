
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
    // Omitting displayModeBar gives Plotly's hover-reveal toolbar (zoom, pan,
    // box-zoom, autoscale, reset, download-PNG) — the analysis tools researchers
    // expect on a chart. displaylogo:false drops the Plotly link. Full button set
    // is kept: Plot renders arbitrary figures (incl. scatter, where box/lasso
    // select are meaningful).
    Plotly.react(node, fig.data || [], fig.layout || {}, { responsive: true, displaylogo: false });
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
