
function Component({ canvas }) {
  const canvasRef = React.useRef(null);
  const [live, setLive] = React.useState(false);
  React.useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    // canvas.paintFrame decodes frames off the main thread (createImageBitmap) and
    // blits them with the GPU-fast bitmaprenderer context, coalescing bursts so a
    // slow tab catches up to the newest frame rather than queuing stale ones.
    return canvas.paintFrame(el, { onActive: () => setLive(true) });
  }, [canvas]);
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
      background: "var(--pc-video-bg, #000)", borderRadius: 4, overflow: "hidden", height: "100%" }}>
      <canvas ref={canvasRef}
        style={{ width: "100%", height: "100%", objectFit: "contain",
          pointerEvents: "none", display: live ? "block" : "none" }} />
      {!live && <span style={{ color: "var(--pc-muted)", fontSize: 13 }}>no signal</span>}
    </div>
  );
}
