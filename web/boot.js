// Static danvas (web/boot.js): a GitHub repository's danvas app, built and run
// in the reader's browser. danvas's Python bridge and the app's Python run in
// Pyodide in a worker (worker.js), cut off from this page's storage and DOM;
// danvas's own frontend shows them through a socket that relays to the worker.
// This page does the GitHub requests, so a token never reaches the app's code.
//
//   ?repo=owner/name          the repository (remembered)
//   &ref=main                 branch, tag or commit (default: the repo's default branch)
//   &app=danvas.toml          the manifest's path in the repo
//   #token=...                a GitHub token for a private repo (kept for this tab only);
//                             "Sign in with GitHub" fills it in
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

const POLL_MS = 60_000;
const SKIP = /\.(pdf|zip|tar|gz|tgz|7z|exe|dll|so|dylib|mp4|mov|avi|mkv|iso)$/i;
const SKIP_DIRS = /(^|\/)(\.git|node_modules|__pycache__|\.venv|\.pixi|tests?|docs|examples)\//;
const MAX_FILE = 20 * 1024 * 1024;

const qs = new URLSearchParams(location.search);
const $msg = document.getElementById("dv-msg");
const $status = document.getElementById("dv-status");
const say = (t) => { if ($msg?.isConnected) $msg.textContent = t; };   // the boot screen, until the app shows
const status = (t) => { $status.hidden = !t; $status.textContent = t || ""; };
const DEBUG = qs.has("debug") ? (window.__dv = { sent: [], recv: [] }) : null;

function fail(err) {
  console.error(err);
  const boot = document.getElementById("dv-boot");
  if (!boot) { status("⚠ " + (err.message || err)); return; }
  boot.classList.add("err");
  say(String(err.message || err));
  if (!err.gh) return;
  // a GitHub refusal: offer to sign in, or to share the repo with the sign-in app
  const more = document.createElement("div");
  more.className = "dv-auth";
  if (!token || err.gh === 401) more.append(button("Sign in with GitHub", signIn));
  else more.innerHTML = `Private? <a href="${GH_APP}/installations/new" target="_blank" rel="noopener">Give the sign-in app access to it</a>, then reload. · `;
  if (token) more.append(button("Sign out", signOut, "dv-link"));
  boot.append(more);
}
function button(text, onclick, className = "") {
  return Object.assign(document.createElement("button"), { textContent: text, onclick, className, type: "button" });
}
function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

// "Sign in with GitHub", for private repos: danvas's GitHub App and its
// sign-in worker (web/auth/), which sends the token back here in the URL
// fragment. The token is kept for this tab only and never passed to the app's code.
const AUTH = "https://danvas-auth.daniel-chahine004.workers.dev";
const GH_APP = "https://github.com/apps/danvas-daniel";
function keepToken(t, refresh, expiresIn) {
  try {
    sessionStorage.setItem("dv-token", t);
    sessionStorage.setItem("dv-refresh", refresh || "");
    sessionStorage.setItem("dv-expires", expiresIn > 0 ? String(Date.now() + expiresIn * 1000) : "");
  } catch { /* storage blocked: this page load only */ }
  token = t;
}
function ssGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
let token = ssGet("dv-token");
const frag = new URLSearchParams(location.hash.slice(1));
if (frag.get("token")) {
  keepToken(frag.get("token"), frag.get("refresh"), +frag.get("expires_in") || 0);
  history.replaceState(null, "", location.pathname + location.search);
}
function signIn() { location.href = `${AUTH}/login?back=${encodeURIComponent(location.href.split("#")[0])}`; }
function signOut() { for (const k of ["dv-token", "dv-refresh", "dv-expires"]) try { sessionStorage.removeItem(k); } catch { /* */ } location.reload(); }
/** Renew a sign-in token shortly before it expires (GitHub's last 8 hours). */
async function freshToken() {
  const exp = +ssGet("dv-expires") || 0, refresh = ssGet("dv-refresh");
  if (!token || !exp || !refresh || Date.now() < exp - 5 * 60_000) return;
  try {
    const res = await fetch(AUTH + "/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: refresh }) });
    if (res.ok) { const t = await res.json(); keepToken(t.access_token, t.refresh_token, t.expires_in); }
  } catch { /* offline: try again next time */ }
}
const repo = qs.get("repo");   // the bare link asks, offering recent repos
const RECENT = "dv-recent";
function recent() { try { return JSON.parse(safeGet(RECENT) || "[]").filter((r) => typeof r === "string"); } catch { return []; } }
function remember(r) { safeSet(RECENT, JSON.stringify([r, ...recent().filter((x) => x !== r)].slice(0, 8))); }
const manifestPath = qs.get("app") || "danvas.toml";

// --- GitHub (or the local dev server standing in for it) -------------------------

let devRepos = new Set();   // repos the dev server serves from local folders
const api = (r) => (devRepos.has(r) ? "/_gh/api" : "https://api.github.com");
const raw = (r) => (devRepos.has(r) ? "/_gh/raw" : "https://raw.githubusercontent.com");
const etags = {};

async function gh(r, path, { conditional = false } = {}) {
  await freshToken();
  const headers = { Accept: "application/vnd.github+json" };
  if (token && !devRepos.has(r)) headers.Authorization = "Bearer " + token;
  const key = r + path;
  if (conditional && etags[key]) headers["If-None-Match"] = etags[key];
  const res = await fetch(api(r) + `/repos/${r}` + path, { headers });
  if (res.status === 304) return null;
  if (!res.ok) {
    const hint = res.status === 404 ? (token ? "not found, or not shared with the sign-in app" : "not found; if it is private, sign in")
      : res.status === 403 || res.status === 429 ? (token ? "GitHub refused (rate limit?)" : "GitHub's limit for anonymous visitors; sign in, or try later")
      : res.status === 401 ? "the sign-in has expired" : res.statusText;
    throw Object.assign(new Error(`${r}${path.split("?")[0]}: ${res.status} ${hint}`), { gh: res.status });
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
async function sync(snap, specs, dest, have, label) {
  const todo = [...snap.files.values()].filter((e) => under(e.path, specs) && fetchable(e.path, e) && have.get(e.path) !== e.sha);
  let n = 0;
  await Promise.all(todo.map(async (e) => {
    const data = await blob(snap, e);
    await call("write", { path: `${dest}/${e.path}`, data: data.buffer }, [data.buffer]);
    have.set(e.path, e.sha);
    say(`${label} ${++n}/${todo.length}`);
  }));
  for (const p of [...have.keys()]) {
    if (under(p, specs) && !snap.files.has(p)) { await call("unlink", { path: `${dest}/${p}` }); have.delete(p); }
  }
  return todo.length;
}

// --- the worker ------------------------------------------------------------------------

const worker = new Worker(new URL("worker.js", import.meta.url), { type: "module" });
const pending = new Map();
const sockets = new Map();   // sid -> PageSocket
let nextId = 0;

/** Ask the worker to do `op`; resolves with its result, rejects with its error. */
function call(op, args = {}, transfer = []) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, op, ...args }, transfer);
  });
}
function tell(op, args = {}, transfer = []) { worker.postMessage({ op, ...args }, transfer); }

worker.onmessage = ({ data }) => {
  if (data.ev) { sockets.get(data.sid)?._event(data); return; }
  const p = pending.get(data.id);
  pending.delete(data.id);
  if (data.ok) p.resolve(data.result); else p.reject(new Error(data.error));
};
worker.onerror = (e) => fail(new Error("the Python worker failed: " + (e.message || "see the console")));

// --- the socket -----------------------------------------------------------------------
// danvas's frontend opens `new WebSocket(".../ws")`; it gets this instead, and
// every frame goes to danvas's Bridge in the worker.

let started = false;
const NativeWS = window.WebSocket;
let nextSid = 0;

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
    if (!started) { setTimeout(() => this._open(), 100); return; }
    this.sid = nextSid++;
    sockets.set(this.sid, this);
    tell("connect", { sid: this.sid });
    this.readyState = 1;
    this._fire("open");
  }
  _event({ ev, text, bytes, code }) {
    if (ev === "closed") { this._close(code); return; }
    if (this.readyState !== 1) return;
    if (ev === "deliver") {
      if (DEBUG) DEBUG.recv.push(text.slice(0, 300));
      this._fire("message", { data: text });
    } else {
      this._fire("message", { data: this.binaryType === "arraybuffer" ? bytes : new Blob([bytes]) });
    }
  }
  send(data) {
    if (this.readyState !== 1) return;
    if (DEBUG) DEBUG.sent.push(typeof data === "string" ? data.slice(0, 300) : "<bytes>");
    if (typeof data === "string") tell("send", { sid: this.sid, text: data });
    else if (data instanceof Blob) data.arrayBuffer().then((b) => tell("send", { sid: this.sid, bytes: b }, [b]));
    else {
      const b = (data.buffer ?? data).slice(data.byteOffset ?? 0, (data.byteOffset ?? 0) + data.byteLength);
      tell("send", { sid: this.sid, bytes: b }, [b]);
    }
  }
  close(code = 1000) { if (this.sid !== undefined) tell("drop", { sid: this.sid }); this._close(code); }
  _close(code) {
    if (this.readyState === 3) return;
    this.readyState = 3;
    sockets.delete(this.sid);
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
  boot.innerHTML = `<div class="dv-title">Static danvas</div>
    <div>Open a GitHub repository's danvas app. The repository needs a
      <a href="https://github.com/DanielChahine004/danvas/blob/main/web/README.md" target="_blank" rel="noopener">danvas.toml</a>.</div>
    <form><input name="r" placeholder="owner/name" autocomplete="off" autofocus><button>Open</button></form>
    <div class="dv-recent"></div>
    <div class="dv-auth"></div>`;
  const auth = boot.querySelector(".dv-auth");
  if (token) auth.append("Signed in to GitHub · ", button("sign out", signOut, "dv-link"));
  else auth.append("Private repo? ", button("Sign in with GitHub", signIn, "dv-link"));
  const list = boot.querySelector(".dv-recent");
  const rs = recent();
  if (rs.length) list.append(Object.assign(document.createElement("div"), { textContent: "Recent", className: "dv-label" }));
  for (const r of rs) {
    const a = document.createElement("a");
    a.href = "?repo=" + encodeURIComponent(r);
    a.textContent = r;
    list.append(a);
  }
  const input = boot.querySelector("input");
  input.oninput = () => input.setCustomValidity("");
  boot.querySelector("form").onsubmit = (e) => {
    e.preventDefault();
    // a pasted GitHub URL works too
    const r = input.value.trim().replace(/^https?:\/\/github\.com\//, "").replace(/(\.git|\/)+$/, "");
    if (/^[\w.-]+\/[\w.-]+$/.test(r)) { location.search = "?repo=" + encodeURIComponent(r); return; }
    input.setCustomValidity("owner/name, e.g. DanielChahine004/paper-notes");
    input.reportValidity();
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
  document.title = `${repo.split("/")[1]} · danvas`;
  try { devRepos = new Set(await (await fetch("/_gh/repos.json")).json()); } catch { /* not the dev server */ }

  say("Loading Python…");
  const pyReady = (async () => {
    const runtime = await (await fetch("runtime.zip")).arrayBuffer();
    await call("init", { runtime }, [runtime]);
  })();

  say(`Reading ${repo}…`);
  let snap = await snapshot(repo, qs.get("ref"));
  if (!snap.files.has(manifestPath)) throw new Error(`${repo} has no ${manifestPath}: it is what tells this page which danvas app to run`);
  const manifestText = new TextDecoder().decode(await blob(snap, snap.files.get(manifestPath)));
  await pyReady;
  const manifest = await call("manifest", { text: manifestText });
  const m = manifest.app ?? manifest;

  // the code: other repos (github:), PyPI packages, and folders of this repo (paths)
  const pip = new Set(m.packages ?? []);
  const pyproject = async (s, path) => {
    const e = s.files.get(path);
    if (e) for (const d of await call("requirements", { text: new TextDecoder().decode(await blob(s, e)) })) pip.add(d);
  };
  for (const spec of m.requires ?? []) {
    if (!spec.startsWith("github:")) { pip.add(spec); continue; }
    const req = parseGithubReq(spec);
    say(`Fetching ${req.repo}…`);
    const s = await snapshot(req.repo, req.ref);
    const sub = req.sub ?? ([...s.files.keys()].some((p) => p.startsWith("src/") && p.endsWith(".py")) ? "src" : "");
    const dest = `/pkgs/${req.repo.replace("/", "__")}`;
    const code = [...s.files.keys()].filter((p) => !SKIP_DIRS.test(p) && (!sub || p.startsWith(sub + "/")));
    await sync(s, code, dest, new Map(), `Fetching ${req.repo}…`);
    await call("syspath", { path: sub ? `${dest}/${sub}` : dest });
    await pyproject(s, "pyproject.toml");
  }
  const paths = m.paths ?? [];
  const have = new Map();
  const cwd = m.cwd ? m.cwd.replace(/\/?$/, "/") : "";
  let specs = [manifestPath, ...paths, ...(m.files ?? []).map((f) => cwd + f)];
  if ((m.entry ?? "app.py").endsWith(".py")) specs.push(cwd + (m.entry ?? "app.py"));
  await sync(snap, specs, "/repo", have, `Reading ${repo}…`);
  for (const p of paths) {
    await call("syspath", { path: "/repo/" + p.replace(/\/$/, "") });
    const parent = p.replace(/\/$/, "").split("/").slice(0, -1).join("/");
    await pyproject(snap, p.replace(/\/$/, "") + "/pyproject.toml");
    if (parent) await pyproject(snap, parent + "/pyproject.toml");
  }
  if (pip.size) {
    say(`Installing ${[...pip].join(", ")}…`);
    await call("pip", { names: [...pip] });
  }

  // what the app itself says it reads
  const extra = await call("extra_files", { manifest });
  specs = [...specs, ...extra];
  await sync(snap, specs, "/repo", have, `Reading ${repo}…`);

  say("Building the app…");
  console.log("[danvas-static]", await call("start", { manifest }));
  started = true;

  // this viewer's drawings: the worker holds them, this page keeps them
  const drawKey = `dv-drawings:${repo}:${manifestPath}`;
  await call("load_drawings", { text: safeGet(drawKey) || "{}" });
  let saved = "";
  const save = async () => { const s = await call("drawings"); if (s !== saved) { safeSet(drawKey, s); saved = s; } };
  setInterval(save, 3000);
  addEventListener("pagehide", save);

  remember(repo);
  document.getElementById("dv-boot")?.remove();
  const link = document.getElementById("dv-repo-link");
  link.querySelector("b").textContent = repo;
  link.hidden = false;
  await loadDanvasFrontend();
  console.log(`[danvas-static] ready ${Math.round(performance.now())} ms after navigation`);

  // follow the repository
  const poll = devRepos.has(repo) ? 2000 : POLL_MS;
  setInterval(async () => {
    try {
      const s = await snapshot(repo, snap.ref, true);
      if (!s || s.sha === snap.sha) return;
      if (s.files.get(manifestPath)?.sha !== snap.files.get(manifestPath)?.sha) { await save(); location.reload(); return; }
      snap = s;
      status("Updating…");
      const n = await sync(snap, specs, "/repo", have, "Updating…");
      if (n) console.log("[danvas-static] reloaded:", await call("reload"));
      status("");
    } catch (e) { console.warn("[danvas-static] poll:", e); status(""); }
  }, poll);
}

main().catch(fail);
