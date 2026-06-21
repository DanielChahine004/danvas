"""Upload: a button / drop-zone that sends a viewer's file up to Python.

The mirror image of :class:`~danvas.Download`. The browser holds a file the
*viewer* picked; getting its bytes to Python travels over **plain HTTP** (a POST
to ``/__upload__/<token>``), not the WebSocket — so a large file streams up with
no base64 bloat and none of the socket's frame/queue limits. The route sits
behind the same auth gate as the rest of the canvas.

Each panel mints an unguessable token at construction and registers it with the
bridge; the browser POSTs the raw file body to that token's URL. The server
hands the result to Python on the input-dispatch thread, firing
``@upload.on_upload`` with an :class:`UploadedFile`.

Two ways to receive the bytes, symmetric with Download:

  * **in memory** (default) — the callback's file carries ``.data`` (``bytes``);
  * **streamed to disk** — pass ``dest="some/dir"`` and each upload is written
    there (filename sandboxed inside ``dest``), the callback's file carries
    ``.path`` instead. Right for large files: constant server memory.

Rendered as a native React panel (like :class:`~danvas.Button`), so it follows
the canvas theme and needs no ``npm`` build.

    up = canvas.upload("upload", text="Upload CSV", accept=".csv")

    @up.on_upload
    def got(file, viewer):         # file: an UploadedFile; viewer: who sent it
        df = pandas.read_csv(io.BytesIO(file.data))
        table.update(df)
        print(viewer["name"], "uploaded", file.name)
"""

import os
import secrets

from .react import React


class UploadedFile:
    """One file uploaded from a viewer's browser.

    ``data`` holds the raw ``bytes`` for an in-memory upload (``dest`` unset);
    ``path`` holds the saved location when the panel streamed it to disk. Exactly
    one of the two is set — use :meth:`read` to get the bytes either way, or
    :meth:`save` to write them somewhere. ``content_type`` is the browser-reported
    MIME type (advisory — don't trust it for security decisions).
    """

    def __init__(self, name, size, content_type=None, data=None, path=None):
        self.name = name
        self.size = size
        self.content_type = content_type
        self.data = data    # bytes (in-memory mode), else None
        self.path = path    # saved path (dest mode), else None

    def read(self):
        """Return the file's bytes, whether it's in memory or on disk."""
        if self.data is not None:
            return self.data
        with open(self.path, "rb") as f:
            return f.read()

    def save(self, dest):
        """Write the file to ``dest`` (a directory or full path); return the path.

        If ``dest`` is a directory, the file keeps its uploaded name inside it.
        """
        target = os.path.join(dest, self.name) if os.path.isdir(dest) else dest
        with open(target, "wb") as f:
            f.write(self.read())
        return target

    def __repr__(self):
        where = f"path={self.path!r}" if self.path else f"{self.size}B in memory"
        return f"<UploadedFile {self.name!r} {where}>"


# Scoped under `.pc-upload`; a click-or-drop zone that follows the canvas theme,
# with a progress bar shown while a file streams up.
_UPLOAD_CSS = """
.pc-upload{box-sizing:border-box;width:100%;height:100%;padding:10px;
 display:flex;flex-direction:column;align-items:center;justify-content:center;
 gap:8px;text-align:center;
 font:600 13px system-ui,-apple-system,sans-serif;color:var(--pc-off-text);
 background:var(--pc-off-bg);
 border:1.5px dashed var(--pc-border);border-radius:8px;cursor:pointer;
 transition:background .12s,border-color .12s}
.pc-upload:hover,.pc-upload.drag{background:var(--pc-border-mid);
 border-color:var(--pc-accent)}
.pc-upload-ico{font-size:18px;line-height:1;margin-right:6px}
.pc-upload-prog{width:100%;height:8px;border-radius:4px;
 background:var(--pc-border);overflow:hidden}
.pc-upload-bar{height:100%;background:var(--pc-accent);transition:width .1s}
.pc-upload-msg{font-weight:400;font-size:12px;color:var(--pc-muted);
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
"""

# A hidden <input type=file> backs a click-or-drag zone. Each selected file is
# POSTed (raw body, name in the query) to props.url with an XHR so we can show
# real upload progress. ``props.text`` is the face, ``props.accept`` filters the
# picker, ``props.multiple`` allows multi-select (uploaded one request each).
_UPLOAD_SOURCE = """
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
          ? <div><span className="pc-upload-ico">{"\\u2191"}</span>{props.text}</div>
          : <div className="pc-upload-prog"><div className="pc-upload-bar"
              style={{ width: pct + "%" }} /></div>}
        {msg ? <div className="pc-upload-msg">{msg}</div> : null}
      </div>
    </>
  );
}
""".replace("__CSS__", _UPLOAD_CSS)


class Upload(React):
    """A button / drop-zone that receives a viewer's file into Python.

    Register a handler with :meth:`on_upload`; it fires with an
    :class:`UploadedFile` for each file the viewer picks (or drops). By default
    the bytes arrive in memory (``file.data``); pass ``dest=`` a directory and
    each upload is streamed to disk there instead (``file.path``), which keeps
    server memory flat for large files. ``accept`` filters the file picker (e.g.
    ``".csv"`` or ``"image/*"``); ``multiple=True`` allows selecting several at
    once (each fires the handler separately). ``max_size`` (bytes) rejects
    oversized uploads — set it on any public/tunneled canvas. ``.value`` reads
    the most recently uploaded file.
    """

    default_w = 240
    default_h = 120

    def __init__(self, name="upload", text=None, label=None, dest=None, accept=None,
                 multiple=False, max_size=None, color=None):
        caption = text if text is not None else (label if label is not None else name)
        # The token is the upload target; minted now so it's a stable prop, and
        # registered with the bridge in ``_bind`` once there is one.
        self._token = secrets.token_urlsafe(24)
        super().__init__(source=_UPLOAD_SOURCE, name=name, label=label,
                         props={"text": caption,
                                "url": f"/__upload__/{self._token}",
                                "accept": accept or "",
                                "multiple": bool(multiple)})
        self._init_color(color)
        self._dest = os.path.realpath(dest) if dest else None
        self._max_size = max_size
        self._upload_cbs = []
        if self._dest is not None:
            os.makedirs(self._dest, exist_ok=True)

    def _bind(self, component_id, bridge):
        super()._bind(component_id, bridge)
        bridge.register_upload(self._token, self)

    def on_upload(self, fn=None, *, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: handler fired with an :class:`UploadedFile` per upload.

        Accepts an optional second parameter (``def fn(file, viewer)``) to also
        receive the uploader's identity dict:

            {"role": "manager", "id": "a1b2c3d4", "name": "Fox", "color": "#ef4444"}

        ``role`` is the server-trusted access level (``None`` when no passwords are
        set) — gate permissions on this. ``id``/``name``/``color`` come from the
        live viewer roster (present when the uploader is connected) and are good
        for attribution/labelling, not authorization. See
        :meth:`~danvas.bridge.Bridge.resolve_viewer`.
        """
        def register(f):
            return self._register_callback(self._upload_cbs, f, threaded, dedicated, queue)
        return register(fn) if fn is not None else register

    def _receive_upload(self, info, viewer=None):
        """Wrap a server-built info dict into an :class:`UploadedFile` and fire.

        Called on the bridge's input-dispatch thread (never the event loop), so a
        slow handler can't stall rendering or other viewers.
        """
        file = UploadedFile(**info)
        with self._lock:
            self._value = file
        self._dispatch_callbacks(self._upload_cbs, (file,), viewer)