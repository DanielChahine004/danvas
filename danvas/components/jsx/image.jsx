
function Component({ props }) {
  return (
    <div className="pc-img">
      <style>{`__CSS__`}</style>
      {props.src
        ? <img src={props.src} alt="" style={{ objectFit: props.fit || "contain" }} />
        : null}
    </div>
  );
}
