// Static danvas's worker: Pyodide, danvas's Python and the app run here, off
// the page. A worker has no DOM, no localStorage or sessionStorage, and does
// no GitHub requests, so the app's code (which can reach JavaScript through
// Pyodide's `js` module) cannot read the page's saved data or a GitHub token,
// and a slow handler cannot freeze the canvas.
//
// The page talks to it by message:
//   {id, op, ...args}            -> {id, ok, result} | {id, ok: false, error}
//   ops: init, write, unlink, syspath, manifest, requirements, pip,
//        extra_files, start, reload, drawings, load_drawings,
//        connect {sid}, send {sid, text | bytes}, drop {sid}
//   events to the page: {ev: "deliver", sid, text} | {ev: "deliver_bytes", sid, bytes}
//                       | {ev: "closed", sid, code}

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/";
let py, ds, app;
const sockets = new Map();   // sid -> the Python socket

function mkdirs(path) {
  const dir = path.split("/").slice(0, -1).join("/");
  if (dir) py.FS.mkdirTree(dir);
}
const plain = (x) => (x && typeof x.toJs === "function" ? x.toJs({ dict_converter: Object.fromEntries }) : x);

const ops = {
  async init({ runtime }) {
    const { loadPyodide } = await import(PYODIDE + "pyodide.mjs");
    py = await loadPyodide({ indexURL: PYODIDE });
    await py.loadPackage(["micropip", "sqlite3"]);   // sqlite3: danvas's ledger
    py.unpackArchive(runtime, "zip", { extractDir: "/lib/danvas" });
    py.runPython("import sys; sys.path.insert(0, '/lib/danvas')");
    ds = py.pyimport("danvas.static");
    py.FS.mkdirTree("/repo");
  },
  write({ path, data }) { mkdirs(path); py.FS.writeFile(path, new Uint8Array(data)); },
  unlink({ path }) { try { py.FS.unlink(path); } catch { /* gone */ } },
  syspath({ path }) { py.runPython(`import sys\nif ${JSON.stringify(path)} not in sys.path: sys.path.insert(0, ${JSON.stringify(path)})`); },
  manifest({ text }) { return plain(ds.read_manifest(text)); },
  requirements({ text }) { return plain(ds.requirements(text)); },
  async pip({ names }) { await py.pyimport("micropip").install(names); },
  extra_files({ manifest }) { return plain(ds.extra_files(py.toPy(manifest), "/repo")); },
  start({ manifest }) {
    app = ds.App(py.toPy(manifest), "/repo");
    return app.start();
  },
  reload() { return app.reload(); },
  drawings() { return app.drawings_json(); },
  load_drawings({ text }) { app.load_drawings(text); },
  connect({ sid, query, ua }) {
    sockets.set(sid, app.connect({
      deliver: (text) => postMessage({ ev: "deliver", sid, text }),
      deliver_bytes: (u8) => { const bytes = u8.slice().buffer; postMessage({ ev: "deliver_bytes", sid, bytes }, [bytes]); },
      closed: (code) => { sockets.delete(sid); postMessage({ ev: "closed", sid, code }); },
    }, query ? py.toPy(query) : null, ua ?? null));
  },
  send({ sid, text, bytes }) {
    const s = sockets.get(sid);
    if (!s) return;
    if (text !== undefined) s.push(text);
    else s.push_bytes(new Uint8Array(bytes));
  },
  drop({ sid }) { sockets.get(sid)?.drop(); sockets.delete(sid); },
  // a serial port the page holds for danvas.webserial: kind "data" | "open" | "closed"
  serial({ pid, kind, value }) {
    py.pyimport("danvas.webserial")._from_page(pid, kind, value instanceof ArrayBuffer ? new Uint8Array(value) : value);
  },
};

// one message at a time, in order: a frame sent while a file is still being
// written waits for it, as it would on a single socket
let queue = Promise.resolve();
onmessage = ({ data: { id, op, ...args } }) => {
  queue = queue.then(async () => {
    try {
      const result = await ops[op](args);
      if (id !== undefined) postMessage({ id, ok: true, result: result === undefined ? null : plain(result) });
    } catch (e) {
      if (id !== undefined) postMessage({ id, ok: false, error: String(e?.message || e) });
      else console.error(e);
    }
  });
};
