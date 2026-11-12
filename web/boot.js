// Static danvas (web/boot.js): a GitHub repository's danvas app, built and run
// in the reader's browser. danvas's Python bridge and the app's Python run in Pyodide; danvas's
// own frontend shows them through an in-page socket instead of a WebSocket.
//
//   ?repo=owner/name          the repository (remembered)
//   &ref=main                 branch, tag or commit (default: the repo's default branch)
//   &app=danvas.toml          the manifest's path in the repo
//   #token=...                a GitHub token for a private repo (kept for this tab only)
//   &debug                    keep every frame in window.__dv
//
// The repo's danvas.toml:
//
//   [app]
//   entry    = "notesmap.static:app"    # "module:function", or "app.py" (a script that calls canvas.serve())
//   requires = ["github:owner/pkg@tag"]  # code fetched from another repo; other names install from PyPI
//   paths    = ["tools/pkg/src"]         # or code in this repo, put on sys.path
//   packages = ["numpy"]                 # Pyodide/PyPI packages
//   files    = ["notesmap.toml"]         # what the app reads; the entry module's files() can add more
//   cwd      = ""                        # folder the app runs in, relative to the repo

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/";
const POLL_MS = 60_000;
const SKIP = /\.(pdf|zip|tar|gz|tgz|7z|exe|dll|so|dylib|mp4|mov|avi|mkv|iso)$/i;
const SKIP_DIRS = /(^|\/)(\.git|node_modules|__pycache__|\.venv|\.pixi|tests?|docs|examples)\//;
const MAX_FILE = 20 * 1024 * 1024;

const qs = new URLSearchParams(location.search);
const $msg = document.getElementById("dv-msg");
const $status = document.getElementById("dv-status");
const say = (t) => { if ($msg) $msg.textContent = t; };
const status = (t) => { $status.hidden = !t; $status.textContent = t || ""; };
const DEBUG = qs.has("debug") ? (window.__dv = { sent: [], recv: [] }) : null;

function fail(err) {
  console.error(err);
  const boot = document.getElementById("dv-boot");
  if (!boot) { status("⚠ " + (err.message || err)); return; }
  boot.classList.add("err");
  say(String(err.message || err));
}
function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

const frag = new URLSearchParams(location.hash.slice(1));
if (frag.get("token")) { sessionStorage.setItem("dv-token", frag.get("token")); history.replaceState(null, "", location.pathname + location.search); }
const token = sessionStorage.getItem("dv-token");
const repo = qs.get("repo") || safeGet("dv-repo");
const manifestPath = qs.get("app") || "danvas.toml";

// --- GitHub (or the local dev server standing in for it) -------------------------

let devRepos = new Set();   // repos the dev server serves from local folders
const api = (r) => (devRepos.has(r) ? "/_gh/api" : "https://api.github.com");
const raw = (r) => (devRepos.has(r) ? "/_gh/raw" : "https://raw.githubusercontent.com");
const etags = {};

async function gh(r, path, { conditional = false } = {}) {
  const headers = { Accept: "application/vnd.github+json" };
  if (token && !devRepos.has(r)) headers.Authorization = "Bearer " + token;
  const key = r + path;
  if (conditional && etags[key]) headers["If-None-Match"] = etags[key];
  const res = await fetch(api(r) + `/repos/${r}` + path, { headers });
  if (res.status === 304) return null;
  if (!res.ok) {
    const hint = res.status === 404 ? (token ? "not found, or this token cannot read it" : "not found (a private repo needs #token=…)")
      : res.status === 403 ? "GitHub's rate limit for anonymous visitors; try again later or add #token=…" : res.statusText;
    throw new Error(`${r}${path.split("?")[0]}: ${res.status} ${hint}`);
  }
  if (res.headers.get("ETag")) etags[key] = res.headers.get("ETag");
  return res.json();
}

/** A repository at a ref: its commit sha and every file (path -> {path, sha, size}). */
async function snapshot(r, ref, conditional = false) {
  if (!ref) ref = (await gh(r, "")).default_branch;
  const c = await gh(r, `/commits/${encodeURIComponent(ref)}`, { conditional });
  if (!c) return null;
  const t = await gh(r, `/git/trees/${c.commit.tree.sha}?recursive=1`);
  return { repo: r, ref, sha: c.sha, files: new Map(t.tree.filter((e) => e.type === "blob").map((e) => [e.path, e])) };
}

async function blob(snap, entry) {
  if (!token || devRepos.has(snap.repo)) {
    const res = await fetch(`${raw(snap.repo)}/${snap.repo}/${snap.sha}/${entry.path.split("/").map(encodeURIComponent).join("/")}`);
    if (!res.ok) throw new Error(`${entry.path}: ${res.status}`);
    return new Uint8Array(await res.arrayBuffer());
  }
  const res = await fetch(`https://api.github.com/repos/${snap.repo}/git/blobs/${entry.sha}`,
    { headers: { Accept: "application/vnd.github.raw+json", Authorization: "Bearer " + token } });
  if (!res.ok) throw new Error(`${entry.path}: ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

const under = (p, specs) => specs.some((s) => p === s || p.startsWith(s.endsWith("/") ? s : s + "/"));
const fetchable = (p, e) => !SKIP.test(p) && (e.size ?? 0) <= MAX_FILE;

/** Copy the files of `snap` matching `specs` into Pyodide at `dest`, skipping
 * ones already there at the same blob; delete ones that went away. */
async function sync(py, snap, specs, dest, have, label) {
  const todo = [...snap.files.values()].filter((e) => under(e.path, specs) && fetchable(e.path, e) && have.get(e.path) !== e.sha);
  let n = 0;
  await Promise.all(todo.map(async (e) => {
    const data = await blob(snap, e);
    const file = `${dest}/${e.path}`;
    py.FS.mkdirTree(file.split("/").slice(0, -1).join("/"));
    py.FS.writeFile(file, data);
    have.set(e.path, e.sha);
    say(`${label} ${++n}/${todo.length}`);
  }));
  for (const p of [...have.keys()]) {
    if (under(p, specs) && !snap.files.has(p)) { try { py.FS.unlink(`${dest}/${p}`); } catch { /* gone */ } have.delete(p); }
  }
  return todo.length;
}

// --- the in-page socket -------------------------------------------------------------
// danvas's frontend opens `new WebSocket(".../ws")`; it gets this instead.

let app = null;   // the Python App
const NativeWS = window.WebSocket;

class PageSocket extends EventTarget {
  constructor(url) {
    super();
    Object.assign(this, { url: String(url), readyState: 0, binaryType: "blob", protocol: "", extensions: "", bufferedAmount: 0 });
    this.onopen = this.onmessage = this.onclose = this.onerror = null;
    setTimeout(() => this._open(), 0);
  }
  _fire(type, init) {
    const ev = type === "message" ? new MessageEvent("message", init) : type === "close" ? new CloseEvent("close", init) : new Event(type);
    this["on" + type]?.(ev);
    this.dispatchEvent(ev);
  }
  _open() {
    this.py = app.connect({
      deliver: (text) => {
        if (DEBUG) DEBUG.recv.push(text.slice(0, 300));
        if (this.readyState === 1) queueMicrotask(() => this._fire("message", { data: text }));
      },
      deliver_bytes: (u8) => {
        if (this.readyState !== 1) return;
        const buf = u8.slice().buffer;
        queueMicrotask(() => this._fire("message", { data: this.binaryType === "arraybuffer" ? buf : new Blob([buf]) }));
      },
      closed: (code) => this._close(code),
    });
    this.readyState = 1;
    this._fire("open");
  }
  send(data) {
    if (this.readyState !== 1) return;
    if (DEBUG) DEBUG.sent.push(typeof data === "string" ? data.slice(0, 300) : "<bytes>");
    if (typeof data === "string") this.py.push(data);
    else if (data instanceof Blob) data.arrayBuffer().then((b) => this.py.push_bytes(new Uint8Array(b)));
    else this.py.push_bytes(new Uint8Array(data.buffer ?? data, data.byteOffset ?? 0, data.byteLength));
  }
  close(code = 1000) { this.py?.drop(); this._close(code); }
  _close(code) {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this._fire("close", { code, wasClean: true });
  }
}
window.WebSocket = function (url, protocols) {
  return /\/ws$/.test(new URL(url, location.href).pathname) ? new PageSocket(url) : new NativeWS(url, protocols);
};
Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });

// --- boot ------------------------------------------------------------------------

function askRepo() {
  const boot = document.getElementById("dv-boot");
  boot.classList.add("ask");
  boot.innerHTML = `<div>Which repository holds the danvas app?</div>
    <form><input name="r" placeholder="owner/name" autofocus><button>Open</button></form>`;
  boot.querySelector("form").onsubmit = (e) => {
    e.preventDefault();
    const r = e.target.r.value.trim();
    if (r) location.search = "?repo=" + encodeURIComponent(r);
  };
}

async function loadDanvasFrontend() {
  const doc = new DOMParser().parseFromString(await (await fetch("danvas/index.html")).text(), "text/html");
  for (const l of doc.querySelectorAll('link[rel="stylesheet"]')) {
    document.head.append(Object.assign(document.createElement("link"), { rel: "stylesheet", href: "danvas/" + l.getAttribute("href").replace(/^\.\//, "") }));
  }
  for (const s of doc.querySelectorAll("script[src]")) {
    document.head.append(Object.assign(document.createElement("script"), { type: "module", src: "danvas/" + s.getAttribute("src").replace(/^\.\//, "") }));
  }
}

/** `github:owner/name@ref` or `github:owner/name@ref/sub/dir`. */
function parseGithubReq(spec) {
  const m = /^github:([^/@\s]+\/[^/@\s]+)(?:@([^/\s]+))?(?:\/(.+))?$/.exec(spec);
  if (!m) throw new Error(`bad requirement ${spec}: expected github:owner/name@ref[/subdir]`);
  return { repo: m[1], ref: m[2], sub: m[3] };
}

async function main() {
  if (!repo) return askRepo();
  safeSet("dv-repo", repo);
  document.title = `${repo.split("/")[1]} · danvas`;
  try { devRepos = new Set(await (await fetch("/_gh/repos.json")).json()); } catch { /* not the dev server */ }

  say("Loading Python…");
  const pyReady = (async () => {
    const { loadPyodide } = await import(PYODIDE + "pyodide.mjs");
    const py = await loadPyodide({ indexURL: PYODIDE });
    await py.loadPackage(["micropip", "sqlite3"]);   // sqlite3: danvas's ledger
    py.unpackArchive(await (await fetch("runtime.zip")).arrayBuffer(), "zip", { extractDir: "/lib/danvas" });
    py.runPython("import sys; sys.path.insert(0, '/lib/danvas')");
    return py;
  })();

  say(`Reading ${repo}…`);
  let snap = await snapshot(repo, qs.get("ref"));
  if (!snap.files.has(manifestPath)) throw new Error(`${repo} has no ${manifestPath}: it is what tells this page which danvas app to run`);
  const manifestText = new TextDecoder().decode(await blob(snap, snap.files.get(manifestPath)));
  const py = await pyReady;
  const ds = py.pyimport("danvas.static");
  const manifest = ds.read_manifest(manifestText);
  const m = (manifest.get("app") ?? manifest).toJs({ dict_converter: Object.fromEntries });
  py.FS.mkdirTree("/repo");

  // the code: other repos (github:), PyPI packages, and folders of this repo (paths)
  const pip = new Set(m.packages ?? []);
  const pyproject = async (s, path) => {
    const e = s.files.get(path);
    if (e) for (const d of ds.requirements(new TextDecoder().decode(await blob(s, e))).toJs()) pip.add(d);
  };
  for (const spec of m.requires ?? []) {
    if (!spec.startsWith("github:")) { pip.add(spec); continue; }
    const req = parseGithubReq(spec);
    say(`Fetching ${req.repo}…`);
    const s = await snapshot(req.repo, req.ref);
    const sub = req.sub ?? ([...s.files.keys()].some((p) => p.startsWith("src/") && p.endsWith(".py")) ? "src" : "");
    const dest = `/pkgs/${req.repo.replace("/", "__")}`;
    const code = [...s.files.keys()].filter((p) => !SKIP_DIRS.test(p) && (!sub || p.startsWith(sub + "/")));
    await sync(py, s, code, dest, new Map(), `Fetching ${req.repo}…`);
    py.runPython(`import sys; sys.path.insert(0, ${JSON.stringify(sub ? `${dest}/${sub}` : dest)})`);
    await pyproject(s, "pyproject.toml");
  }
  const paths = m.paths ?? [];
  const have = new Map();
  const cwd = m.cwd ? m.cwd.replace(/\/?$/, "/") : "";
  let specs = [manifestPath, ...paths, ...(m.files ?? []).map((f) => cwd + f)];
  if ((m.entry ?? "app.py").endsWith(".py")) specs.push(cwd + (m.entry ?? "app.py"));
  await sync(py, snap, specs, "/repo", have, `Reading ${repo}…`);
  for (const p of paths) {
    py.runPython(`import sys; sys.path.insert(0, ${JSON.stringify("/repo/" + p.replace(/\/$/, ""))})`);
    const parent = p.replace(/\/$/, "").split("/").slice(0, -1).join("/");
    await pyproject(snap, p.replace(/\/$/, "") + "/pyproject.toml");
    if (parent) await pyproject(snap, parent + "/pyproject.toml");
  }
  if (pip.size) {
    say(`Installing ${[...pip].join(", ")}…`);
    const micropip = py.pyimport("micropip");
    await micropip.install([...pip]);
  }

  // what the app itself says it reads
  const extra = ds.extra_files(manifest, "/repo").toJs();
  specs = [...specs, ...extra];
  await sync(py, snap, specs, "/repo", have, `Reading ${repo}…`);

  say("Building the app…");
  app = ds.App(manifest, "/repo");
  console.log("[danvas-static]", app.start());

  const drawKey = `dv-drawings:${repo}:${manifestPath}`;
  app.load_drawings(safeGet(drawKey) || "{}");
  let saved = "";
  const save = () => { const s = app.drawings_json(); if (s !== saved) { safeSet(drawKey, s); saved = s; } };
  setInterval(save, 3000);
  addEventListener("pagehide", save);

  await loadDanvasFrontend();
  console.log(`[danvas-static] ready ${Math.round(performance.now())} ms after navigation`);

  // follow the repository
  const poll = devRepos.has(repo) ? 2000 : POLL_MS;
  setInterval(async () => {
    try {
      const s = await snapshot(repo, snap.ref, true);
      if (!s || s.sha === snap.sha) return;
      if (s.files.get(manifestPath)?.sha !== snap.files.get(manifestPath)?.sha) { save(); location.reload(); return; }
      snap = s;
      status("Updating…");
      const n = await sync(py, snap, specs, "/repo", have, "Updating…");
      if (n) console.log("[danvas-static] reloaded:", app.reload());
      status("");
    } catch (e) { console.warn("[danvas-static] poll:", e); status(""); }
  }, poll);
}

main().catch(fail);
