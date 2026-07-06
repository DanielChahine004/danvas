
function Component({ canvas, value, props }) {
  const initial = value != null ? value : (props.value != null ? props.value : "");
  const [text, setText] = React.useState(initial);
  React.useEffect(() => { if (value != null) setText(value); }, [value]);
  function commit(v) { canvas.send({ value: v }); }
  const _th = props._th || {};
  if (props.multiline) {
    return (
      <>
        <style>{`__CSS__`}</style>
        <div className="pc-field" style={_th}>
          <textarea placeholder={props.placeholder || ""}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onBlur={(e) => commit(e.target.value)} />
        </div>
      </>
    );
  }
  return (
    <>
      <style>{`__CSS__`}</style>
      <div className="pc-field" style={_th}>
        <input type="text" placeholder={props.placeholder || ""}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { commit(e.target.value); e.target.blur(); }
          }} />
      </div>
    </>
  );
}
