"""Build a file download + upload panel from scratch — no Download/Upload panel.

This is the "capability, not panel" boundary in action: the built-in Download and
Upload panels are thin recipes over two public Python endpoints —
``canvas.serve_bytes(...)`` (a transient, unguessable, auth-gated download URL) and
``canvas.receive_files(...)`` (a file-receiving endpoint with filename sandboxing).
Here we use those endpoints directly from a single ``react`` panel, proving a
hand-built file UI is a first-class peer of the shipped components.

    python examples/file_endpoints.py
"""

import csv
import io

import danvas

canvas = danvas.Canvas()
status = canvas.label("status", value="upload a CSV or download the sample", x=40, y=40)

# --- the upload endpoint: each received file fires this handler --------------
rows = []


def on_file(file, viewer):
    text = file.data.decode("utf-8", "replace")
    rows[:] = list(csv.DictReader(io.StringIO(text)))
    who = viewer.get("name") or "someone"
    status.update(f"{who} uploaded {file.name} — {len(rows)} rows")


upload_url = canvas.receive_files(on_file, max_size=5_000_000)

# --- the panel: a download button + an upload drop-zone, both over the URLs ---
panel = canvas.react(
    """
    function Component({ canvas, props }) {
      const [msg, setMsg] = React.useState("");
      async function download() {
        const { url } = await canvas.request({ event: "download" });
        window.location = url;                 // browser fetches the auth-gated URL
      }
      async function upload(file) {
        if (!file) return;
        setMsg("uploading " + file.name + "…");
        const r = await fetch(props.uploadUrl + "?name=" + encodeURIComponent(file.name),
                              { method: "POST", body: file });
        setMsg(r.ok ? "uploaded " + file.name : "failed: " + (await r.text()));
      }
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: 8 }}>
          <button onClick={download}>Download sample.csv</button>
          <label style={{ border: "1.5px dashed var(--pc-border)", borderRadius: 8,
                          padding: 16, textAlign: "center", cursor: "pointer",
                          color: "var(--pc-muted)" }}>
            Click to upload a CSV
            <input type="file" accept=".csv" style={{ display: "none" }}
                   onChange={(e) => upload(e.target.files[0])} />
          </label>
          {msg ? <div style={{ fontSize: 12, opacity: 0.7 }}>{msg}</div> : null}
        </div>
      );
    }
    """,
    name="files",
    props={"uploadUrl": upload_url},   # React panels inherit the canvas theme inline
    x=40, y=110, w=280,
)


@panel.on_request("download")
def _(_req):
    sample = b"name,score\nAda,99\nAlan,87\n"
    return {"url": canvas.serve_bytes(sample, filename="sample.csv")}


if __name__ == "__main__":
    canvas.serve(port=8000)
