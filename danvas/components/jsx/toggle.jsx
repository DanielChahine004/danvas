
function Component({ canvas, value, props }) {
  const initial = value != null ? value
                : (props.value != null ? props.value : props.options[0]);
  const [sel, setSel] = React.useState(initial);
  React.useEffect(() => { if (value != null) setSel(value); }, [value]);
  const _th = props._th || {};
  return (
    <>
      <style>{`__CSS__`}</style>
      <div className="pc-toggle" style={_th}>
        {props.options.map((opt) => (
          <button key={opt}
            className={opt === sel ? "sel" : ""}
            onClick={() => { setSel(opt); canvas.send({ value: opt }); }}>
            {opt}
          </button>
        ))}
      </div>
    </>
  );
}
