
function Component({ canvas, props }) {
  const inputRef = React.useRef(null);
  const [pct, setPct] = React.useState(null);   // null = idle; 0..100 = busy
  const [drag, setDrag] = React.useState(false);
  const [msg, setMsg] = React.useState("");

  // Track this viewer's server-assigned identity (id/name/color) so the upload
  // can be attributed to a person. The id is sent with the POST; Python resolves
  // it against the live roster (see Bridge.resolve_viewer).
  const viewerRef = React.useRef(null);
  React.useEffect(() => canvas.chat.identity((v) => { viewerRef.current = v; }), []);

  function uploadOne(file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let url = props.url + "?name=" + encodeURIComponent(file.name);
      const vid = viewerRef.current && viewerRef.current.id;
      if (vid) url += "&viewer=" + encodeURIComponent(vid);
      xhr.open("POST", url);
      if (file.type) xhr.setRequestHeader("Content-Type", file.type);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) setPct(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onload = () =>
        (xhr.status >= 200 && xhr.status < 300)
          ? resolve()
          : reject(new Error(xhr.responseText || ("HTTP " + xhr.status)));
      xhr.onerror = () => reject(new Error("network error"));
      xhr.send(file);
    });
  }

  async function onFiles(list) {
    const files = Array.from(list || []);
    if (!files.length || pct !== null) return;
    setMsg(""); setPct(0);
    try {
      for (const f of files) await uploadOne(f);
      setMsg(files.length === 1
        ? ("Uploaded " + files[0].name)
        : ("Uploaded " + files.length + " files"));
    } catch (e) {
      setMsg("Failed: " + ((e && e.message) || e));
    } finally {
      setPct(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <>
      <style>{`__CSS__`}</style>
      <div className={"pc-upload" + (drag ? " drag" : "")}
        onClick={() => { if (pct === null) inputRef.current && inputRef.current.click(); }}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); onFiles(e.dataTransfer.files); }}>
        <input ref={inputRef} type="file" style={{ display: "none" }}
          accept={props.accept || undefined} multiple={!!props.multiple}
          onChange={(e) => onFiles(e.target.files)} />
        {pct === null
          ? <div><span className="pc-upload-ico">{"\u2191"}</span>{props.text}</div>
          : <div className="pc-upload-prog"><div className="pc-upload-bar"
              style={{ width: pct + "%" }} /></div>}
        {msg ? <div className="pc-upload-msg">{msg}</div> : null}
      </div>
    </>
  );
}
