
function Component({ value, props }) {
  const text = value != null ? value : (props.text != null ? props.text : "");
  const _th = props._th || {};
  return (
    <>
      <style>{`__CSS__`}</style>
      <div className="pc-label" style={_th}>{text}</div>
    </>
  );
}
