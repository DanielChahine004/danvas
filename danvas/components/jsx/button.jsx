
function Component({ canvas, props }) {
  const _th = props._th || {};
  return (
    <>
      <style>{`__CSS__`}</style>
      <button className="pc-button" style={_th} onClick={() => canvas.send({})}>
        {props.text}
      </button>
    </>
  );
}
