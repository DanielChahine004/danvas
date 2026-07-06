
function Component({ canvas, value, props }) {
  const initial = value != null ? value
                : (props.value != null ? props.value : props.default);
  const [v, setV] = React.useState(initial);
  const [raw, setRaw] = React.useState(null);
  React.useEffect(() => { if (value != null) setV(value); }, [value]);
  const onRelease = props.on_release;
  const lo = Number(props.min), hi = Number(props.max), st = Number(props.step);
  const isFloat = String(props.step).indexOf(".") >= 0;
  const show = isFloat ? Number(v).toFixed(2) : String(v);

  function commit() {
    if (raw === null) return;
    const parsed = parseFloat(raw);
    const clamped = isNaN(parsed) ? v : Math.max(lo, Math.min(hi, parsed));
    const snapped = Math.round((clamped - lo) / st) * st + lo;
    const clean = Math.round(snapped * 1e10) / 1e10;
    setV(clean);
    setRaw(null);
    canvas.send({ value: clean });
  }

  const _th = props._th || {};
  return (
    <>
      <style>{`__CSS__`}</style>
      <div className="pc-slider" style={_th}>
        <input type="range" min={props.min} max={props.max} step={props.step}
          value={v}
          onPointerDown={(e) => { try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) {} }}
          onChange={(e) => {
            const n = Number(e.target.value);
            setV(n);
            if (!onRelease) canvas.send({ value: n });
          }}
          onPointerUp={onRelease
            ? (e) => canvas.send({ value: Number(e.target.value) })
            : undefined} />
        <input className="val"
          type="text"
          inputMode={isFloat ? "decimal" : "numeric"}
          value={raw !== null ? raw : show}
          onFocus={(e) => { setRaw(show); const t = e.target; requestAnimationFrame(() => t.select()); }}
          onChange={(e) => setRaw(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.target.blur(); return; }
            if (e.key === "ArrowUp" || e.key === "ArrowDown") {
              e.preventDefault();
              const delta = e.key === "ArrowUp" ? st : -st;
              const clamped = Math.max(lo, Math.min(hi, v + delta));
              const snapped = Math.round((clamped - lo) / st) * st + lo;
              const clean = Math.round(snapped * 1e10) / 1e10;
              setV(clean); setRaw(null); canvas.send({ value: clean });
            }
          }} />
      </div>
    </>
  );
}
