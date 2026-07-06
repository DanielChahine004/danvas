
function Component({ canvas, props }) {
  const sampleRate = (props && props.sampleRate) || 16000;
  const channels = (props && props.channels) || 1;
  const [on, setOn] = React.useState(false);
  const ctxRef = React.useRef(null);
  const nextRef = React.useRef(0);
  const onRef = React.useRef(false);
  React.useEffect(() => { onRef.current = on; }, [on]);
  React.useEffect(() => {
    const LEAD = 0.12;
    return canvas.onFrame((payload) => {
      const ctx = ctxRef.current;
      if (!onRef.current || !ctx || !(payload instanceof ArrayBuffer)) return;
      let n = payload.byteLength;
      n -= n % 2;
      const pcm = new Int16Array(payload, 0, n / 2);
      const frames = Math.floor(pcm.length / channels);
      if (!frames) return;
      const buf = ctx.createBuffer(channels, frames, sampleRate);
      for (let ch = 0; ch < channels; ch++) {
        const out = buf.getChannelData(ch);
        for (let i = 0; i < frames; i++) out[i] = pcm[i * channels + ch] / 32768;
      }
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      const now = ctx.currentTime;
      let start = nextRef.current;
      if (start < now + 0.01) start = now + LEAD;
      src.start(start);
      nextRef.current = start + buf.duration;
    });
  }, [sampleRate, channels]);
  React.useEffect(() => {
    return () => {
      const ctx = ctxRef.current;
      if (ctx) ctx.close().catch(() => {});
      ctxRef.current = null;
    };
  }, []);
  const toggle = async () => {
    if (!on) {
      let ctx = ctxRef.current;
      if (!ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        ctx = new AC({ sampleRate });
        ctxRef.current = ctx;
      }
      try { await ctx.resume(); } catch {}
      nextRef.current = ctx.currentTime + 0.12;
      setOn(true);
    } else {
      setOn(false);
      const ctx = ctxRef.current;
      if (ctx) ctx.suspend().catch(() => {});
    }
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: 8, padding: 12 }}>
      <button
        onClick={toggle}
        style={{
          alignSelf: "flex-start",
          padding: "6px 12px",
          border: "none",
          borderRadius: 6,
          fontSize: 14,
          fontWeight: 600,
          cursor: "pointer",
          background: on ? "var(--pc-accent)" : "var(--pc-off-bg, #e5e7eb)",
          color: on ? "var(--pc-accent-text, #fff)" : "var(--pc-off-text, #374151)",
        }}
      >
        {on ? "🔊 Audio on" : "🔈 Enable audio"}
      </button>
      <div style={{ fontSize: 12, color: "var(--pc-muted, #9ca3af)" }}>
        {sampleRate} Hz · {channels === 1 ? "mono" : channels + " ch"}
      </div>
    </div>
  );
}
