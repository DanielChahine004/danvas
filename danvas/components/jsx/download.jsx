
function Component({ canvas, props }) {
  const [busy, setBusy] = React.useState(false);
  async function go() {
    if (busy) return;
    setBusy(true);
    try {
      const r = await canvas.request({});
      if (r && r.url) {
        const a = document.createElement("a");
        a.href = r.url;
        if (r.filename) a.download = r.filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    } catch (e) {
      console.error("danvas download failed:", e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <style>{`__CSS__`}</style>
      <button className="pc-download" disabled={busy} onClick={go}>
        <span className="pc-download-ico">{"\u2913"}</span>
        {busy ? "Preparing\u2026" : props.text}
      </button>
    </>
  );
}
