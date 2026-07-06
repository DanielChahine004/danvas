
function Component({ props }) {
  return (
    <>
      <style>{`__CSS__`}</style>
      <iframe className="pc-webview" src={props.url}
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
        allowFullScreen />
    </>
  );
}
