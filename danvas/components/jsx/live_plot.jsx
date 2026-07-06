
function Component({ canvas }) {
  const Plotly = libs.plotly;
  const plotRef = React.useRef(null);
  React.useEffect(() => {
    const node = plotRef.current;
    if (!node || !Plotly) return;
    let pendingFull = null, pendingExt = null, raf = null, initialized = false;
    const mergeExt = (acc, e) => {
      if (!acc) return { indices: e.indices.slice(), x: e.x.map(a => a.slice()), y: e.y.map(a => a.slice()), max: e.max };
      const pos = new Map(acc.indices.map((ti, k) => [ti, k]));
      e.indices.forEach((ti, j) => {
        if (pos.has(ti)) { const k = pos.get(ti); acc.x[k] = acc.x[k].concat(e.x[j]); acc.y[k] = acc.y[k].concat(e.y[j]); }
        else { pos.set(ti, acc.indices.length); acc.indices.push(ti); acc.x.push(e.x[j].slice()); acc.y.push(e.y[j].slice()); }
      });
      acc.max = e.max;
      return acc;
    };
    const adapt = () => {
      if (!initialized) return;
      const w = node.clientWidth || 0, h = node.clientHeight || 0;
      if (!w || !h) return;
      const small = w < 340 || h < 200;
      Plotly.relayout(node, {
        margin: small ? { l: 30, r: 10, t: 10, b: 24 } : { l: 40, r: 15, t: 15, b: 30 },
        showlegend: !small,
        "font.size": small ? 9 : 12,
      }).catch(() => {});
    };
    const flush = () => {
      raf = null;
      if (!node) return;
      if (pendingFull) {
        const p = pendingFull; pendingFull = null; pendingExt = null;
        // Hover-reveal toolbar (zoom/pan/box-zoom/autoscale/reset/download-PNG)
        // so a live curve can be inspected and saved; displaylogo:false drops the
        // Plotly link, and lasso/box-select are removed since they do nothing
        // useful on a line/telemetry stream.
        Plotly.react(node, (p.data || []).map(t => ({ ...t, x: [...(t.x || [])], y: [...(t.y || [])] })), p.layout || {}, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'] })
          .then(() => { initialized = true; adapt(); })
          .catch(() => {});
        return;
      }
      if (pendingExt && initialized) {
        const e = pendingExt; pendingExt = null;
        Plotly.extendTraces(node, { x: e.x, y: e.y }, e.indices, e.max);
      }
    };
    const unsub = canvas.onFrame((plot) => {
      if (plot && plot.__extend) pendingExt = mergeExt(pendingExt, plot.__extend);
      else { pendingFull = plot; pendingExt = null; }
      if (raf == null) raf = requestAnimationFrame(flush);
    });
    let resizeRaf = null, ro = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => {
        if (resizeRaf != null) return;
        resizeRaf = requestAnimationFrame(() => { resizeRaf = null; Plotly.Plots.resize(node); adapt(); });
      });
      ro.observe(node);
    }
    return () => {
      unsub();
      if (raf != null) cancelAnimationFrame(raf);
      if (resizeRaf != null) cancelAnimationFrame(resizeRaf);
      if (ro) ro.disconnect();
      Plotly.purge(node);
    };
  }, []);
  return <div ref={plotRef} style={{ flex: 1, width: "100%", minHeight: 0 }} />;
}
