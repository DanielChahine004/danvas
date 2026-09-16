
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
    // Plotly's default margins (80/80/100/80) eat a panel-sized chart: in a
    // 300x220 panel they leave a 140x40 plot area. Default to tight margins
    // (a title still gets headroom); a figure that sets layout.margin wins.
    const layout = Object.assign({}, fig.layout || {});
    if (!layout.margin) {
      const titled = layout.title && (typeof layout.title === "string" || layout.title.text);
      layout.margin = { l: 48, r: 16, t: titled ? 40 : 16, b: 40 };
    }
    Plotly.react(node, fig.data || [], layout, { responsive: true, displaylogo: false });
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
