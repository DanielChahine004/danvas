// Static danvas (web/boot.js): a GitHub repository's danvas app, built and run
// in the reader's browser. danvas's Python bridge and the app's Python run in
// Pyodide in a worker (worker.js), cut off from this page's storage and DOM;
// danvas's own frontend shows them through a socket that relays to the worker.
// This page does the GitHub requests, so a token never reaches the app's code.
//
//   ?repo=owner/name          the repository (remembered)
//   &ref=main                 branch, tag or commit (default: the repo's default branch)
//   &app=danvas.toml          the manifest's path in the repo
//   &with=owner/name          a repo of plain files, run as that app's danvas.local.toml says
//   ?local=owner/name         files from the reader's own computer, run the same way
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
//   max_image = 1200                     # optional: shrink PNG/JPEG/WebP files wider or taller than
//                                        # this (pixels) as they are fetched, so the app, the page
//                                        # and a phone's memory carry what is shown, not the original
//                                        # (opaque PNGs are stored as JPEG data, keeping their name)
//
//   [[app.mount]]                        # optional: files from another repo, if this reader may read it
//   repo  = "owner/private-repo"
//   ref   = "main"                       # optional; default: its default branch
//   files = ["figures/"]                 # paths or folder/ prefixes in that repo
//   at    = "notes/remote"               # where they appear, relative to this repo
//
// A mount is fetched with the reader's own sign-in. A reader who cannot read it
// (signed out, or not a collaborator) gets the app without those files. A
// mount from another owner's repo is asked about first, once per pair, so a
// stranger's map cannot quietly read a signed-in reader's private repos.

const POLL_MS = 20_000;
const SKIP = /\.(pdf|zip|tar|gz|tgz|7z|exe|dll|so|dylib|mp4|mov|avi|mkv|iso)$/i;
const SKIP_DIRS = /(^|\/)(\.git|node_modules|__pycache__|\.venv|\.pixi|tests?|docs|examples)\//;
const MAX_FILE = 20 * 1024 * 1024;

const qs = new URLSearchParams(location.search);
let $msg = document.getElementById("dv-msg");
const $status = document.getElementById("dv-status");
const say = (t) => { if ($msg?.isConnected) $msg.textContent = t; };   // the boot screen, until the app shows
const status = (t) => { $status.hidden = !t; $status.textContent = t || ""; };
const DEBUG = qs.has("debug") ? (window.__dv = { sent: [], recv: [], memory: () => call("memory") }) : null;

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
// ?local=owner/name: the app comes from that repo, the files from the reader's own computer
const localApp = /^[\w.-]+\/[\w.-]+$/.test(qs.get("local") || "") ? qs.get("local") : null;
const repo = qs.get("repo") || (localApp ? "local:" + localApp : null);   // the bare link asks, offering recent repos
// ?repo=...&with=owner/name: a repo of plain files (notes synced from Overleaf, say)
// run by the app that owner/name's danvas.local.toml names, as ?local= would
const withApp = !localApp && /^[\w.-]+\/[\w.-]+$/.test(qs.get("with") || "") ? qs.get("with") : null;
const RECENT = "dv-recent";
function recent() { try { return JSON.parse(safeGet(RECENT) || "[]").filter((r) => typeof r === "string"); } catch { return []; } }
function remember(r) { safeSet(RECENT, JSON.stringify([r, ...recent().filter((x) => x !== r)].slice(0, 8))); }
const manifestPath = qs.get("app") || "danvas.toml";

// a live room: the host's tab runs the app, guests join it through the relay (rooms/)
const ROOMS = qs.get("rooms") || (/^(localhost|127\.0\.0\.1)$/.test(location.hostname) ? "ws://127.0.0.1:8788" : "wss://danvas-rooms.daniel-chahine004.workers.dev");
const live = /^[A-Za-z0-9_-]{8,64}$/.test(qs.get("live") || "") ? qs.get("live") : null;
const hostKey = `dv-host:${repo}:${manifestPath}`;
const guest = !!live && (() => { try { return sessionStorage.getItem(hostKey) !== live; } catch { return true; } })();

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
  if (entry.read) return entry.read();   // a file on the reader's own computer
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

const under = (p, specs) => specs.some((s) => s === "*" || p === s || p.startsWith(s.endsWith("/*") ? s.slice(0, -1) : s.endsWith("/") ? s : s + "/"));
const fetchable = (p, e) => !SKIP.test(p) && (e.size ?? 0) <= MAX_FILE;

// [app] max_image: big pictures are shrunk on the way in, one at a time (a phone
// decoding dozens at once would run out of memory doing it)
let maxImage = 0;
let shrinking = Promise.resolve();
const IMAGE = /\.(png|jpe?g|webp)$/i;

function shrink(data, path) {
  if (!maxImage || !IMAGE.test(path) || data.length < 100_000) return Promise.resolve(data);
  const job = shrinking.then(() => shrinkNow(data, path));
  shrinking = job.catch(() => {});
  return job;
}

async function shrinkNow(data, path) {
  const type = /\.png$/i.test(path) ? "image/png" : /\.webp$/i.test(path) ? "image/webp" : "image/jpeg";
  try {
    const bmp = await createImageBitmap(new Blob([data], { type }));
    const k = Math.min(1, maxImage / Math.max(bmp.width, bmp.height));
    // a PNG is re-encoded even at full size: photos saved as PNG are many times
    // their JPEG size (browsers read the bytes, not the name, so it stays .png)
    if (k === 1 && type !== "image/png") { bmp.close(); return data; }
    const w = Math.max(1, Math.round(bmp.width * k)), h = Math.max(1, Math.round(bmp.height * k));
    const c = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(w, h)
      : Object.assign(document.createElement("canvas"), { width: w, height: h });
    const g = c.getContext("2d");
    g.drawImage(bmp, 0, 0, w, h);
    bmp.close();
    let to = type;
    if (type === "image/png") {   // JPEG unless it has see-through pixels
      const px = g.getImageData(0, 0, w, h).data;
      let opaque = true;
      for (let i = 3; i < px.length; i += 4) if (px[i] < 255) { opaque = false; break; }
      if (opaque) to = "image/jpeg";
    }
    const out = c.convertToBlob ? await c.convertToBlob({ type: to, quality: 0.85 })
      : await new Promise((r) => c.toBlob(r, to, 0.85));
    c.width = c.height = 0;   // let go of the pixels now: iOS counts canvas memory strictly
    if (!out || out.type !== to) return data;   // e.g. no WebP encoder: keep the original
    const small = new Uint8Array(await out.arrayBuffer());
    return small.length < data.length ? small : data;
  } catch (e) {
    console.warn("[danvas-static] could not shrink", path, e);
    return data;
  }
}

/** Copy the files of `snap` matching `specs` into Pyodide at `dest`, skipping
 * ones already there at the same blob; delete ones that went away. */
async function sync(snap, specs, dest, have, label) {
  const todo = [...snap.files.values()].filter((e) => under(e.path, specs) && fetchable(e.path, e) && have.get(e.path) !== e.sha);
  let n = 0;
  await Promise.all(todo.map(async (e) => {
    const data = await shrink(await blob(snap, e), e.path);
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
  if (data.ev === "serial") { serialOp(data); return; }
  if (data.ev) { sockets.get(data.sid)?._event(data); return; }
  const p = pending.get(data.id);
  pending.delete(data.id);
  if (data.ok) p.resolve(data.result); else p.reject(new Error(data.error));
};
worker.onerror = (e) => fail(new Error("the Python worker failed: " + (e.message || "see the console")));

// --- serial ports (danvas.webserial) -------------------------------------------------
// The app's Python asks for a port; the page holds it, because the browser's
// port picker belongs to the page and needs a click. Bytes go to the worker as
// they arrive; what the app writes comes back here.

const serials = new Map();   // pid -> { port, reader, writer }

function toSerial(pid, kind, value, transfer = []) { tell("serial", { pid, kind, value }, transfer); }

async function serialOpen(pid, { baud, filters }) {
  if (!("serial" in navigator)) {
    toSerial(pid, "closed", "this browser has no Web Serial: open the link in Chrome or Edge on a computer");
    return;
  }
  const want = (p) => !filters?.length || filters.some((f) => {
    const i = p.getInfo();
    return (f.usbVendorId === undefined || f.usbVendorId === i.usbVendorId) && (f.usbProductId === undefined || f.usbProductId === i.usbProductId);
  });
  // a port the reader chose before on this site, if there is just one
  const known = (await navigator.serial.getPorts()).filter(want);
  let port = known.length === 1 ? known[0] : null;
  if (!port) {
    try {
      port = await navigator.serial.requestPort({ filters: filters ?? [] });
    } catch (e) {
      if (e.name === "NotFoundError") { toSerial(pid, "closed", "no device was chosen"); return; }
      // too long after a click for the browser to show its picker: ask for one
      port = await new Promise((resolve) => {
        const n = note("The app asks for a serial device.", ["Choose a device", async () => {
          n.remove();
          try { resolve(await navigator.serial.requestPort({ filters: filters ?? [] })); } catch { resolve(null); }
        }], () => resolve(null));
      });
      if (!port) { toSerial(pid, "closed", "no device was chosen"); return; }
    }
  }
  try {
    await port.open({ baudRate: baud });
  } catch (e) {
    // InvalidStateError: this page has it open already
    if (e.name !== "InvalidStateError") { toSerial(pid, "closed", `could not open the port: ${e.message}`); return; }
  }
  const s = { port, writer: port.writable.getWriter(), reader: null };
  serials.set(pid, s);
  const i = port.getInfo();
  toSerial(pid, "open", i.usbVendorId ? `USB ${i.usbVendorId.toString(16).padStart(4, "0")}:${(i.usbProductId ?? 0).toString(16).padStart(4, "0")}` : "serial port");
  let why = "closed";
  try {
    while (port.readable && serials.get(pid) === s) {
      s.reader = port.readable.getReader();
      try {
        for (;;) {
          const { value, done } = await s.reader.read();
          if (done) break;
          const b = value.slice().buffer;
          toSerial(pid, "data", b, [b]);
        }
      } catch (e) {
        why = e.message;   // a framing or parity error, or the device unplugged
      } finally {
        s.reader.releaseLock();
      }
      if (serials.get(pid) !== s) break;
    }
  } finally {
    if (serials.get(pid) === s) serials.delete(pid);
    try { s.writer.releaseLock(); } catch { /* gone */ }
    try { await port.close(); } catch { /* gone */ }
    toSerial(pid, "closed", why);
  }
}

async function serialClose(pid) {
  const s = serials.get(pid);
  if (!s) return;
  serials.delete(pid);
  try { await s.reader?.cancel(); } catch { /* gone */ }
}

function serialOp({ pid, op, ...args }) {
  if (op === "open") serialOpen(pid, args).catch((e) => toSerial(pid, "closed", String(e?.message || e)));
  else if (op === "write") serials.get(pid)?.writer.write(args.data).catch(() => serialClose(pid));
  else if (op === "close") serialClose(pid);
}

/** A notice at the top with an action button and ×; returns its element. */
function note(text, act, onDismiss) {
  const box = document.getElementById("dv-notes");
  const div = document.createElement("div");
  div.append(text + " ");
  if (act) div.append(Object.assign(document.createElement("button"), { className: "dv-act", type: "button", textContent: act[0], onclick: act[1] }));
  div.append(Object.assign(document.createElement("button"), { className: "dv-x", type: "button", textContent: "×", title: "Dismiss",
    onclick: () => { div.remove(); onDismiss?.(); } }));
  box?.append(div);
  return div;
}

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
  const u = new URL(url, location.href);
  if (!/\/ws$/.test(u.pathname)) return new NativeWS(url, protocols);
  if (!guest) return new PageSocket(url);
  // a guest: danvas's own socket, to the host through the relay, with its viewer identity
  const ws = new NativeWS(`${ROOMS}/join?room=${live}&${u.search.slice(1)}`);
  ws.addEventListener("close", (e) => guestClosed(e.code));
  return ws;
};
Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });

// --- live rooms --------------------------------------------------------------------

const TEXT = 0, BYTES = 1, CLOSE = 2;
let lastGuestNote = 0;

function guestClosed(code) {
  // danvas's frontend retries on its own; say why while it does
  const why = code === 4404 ? "No one is hosting this room right now. It reconnects when the host is back."
    : code === 4410 ? "The host left. It reconnects if they host again."
    : code === 4429 ? "This room is full." : null;
  if (!why || Date.now() - lastGuestNote < 30_000) return;
  lastGuestNote = Date.now();
  const n = note(why);
  setTimeout(() => n.remove(), 12_000);
}

const tagged = (kind, g, body) => {
  const out = new Uint8Array(body.length + 5);
  out[0] = kind;
  new DataView(out.buffer).setUint32(1, g);
  out.set(body, 5);
  return out;
};

/** Host this tab's canvas in a room: each guest becomes one more viewer of the app. */
function hostRoom(room, onState) {
  let ws = null, wait = 500, stopped = false;
  const guests = new Map();   // guest number -> sid
  const enc = new TextEncoder();
  const drop = (g) => {
    const sid = guests.get(g);
    if (sid === undefined) return;
    guests.delete(g);
    sockets.delete(sid);
    tell("drop", { sid });
    onState(guests.size);
  };
  const open = () => {
    ws = new NativeWS(`${ROOMS}/host?room=${room}`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { wait = 500; onState(0); };
    ws.onmessage = ({ data }) => {
      if (typeof data === "string") {
        const m = JSON.parse(data);
        if (m.join != null) {
          const g = m.join, sid = nextSid++;
          guests.set(g, sid);
          // the worker's replies to this viewer go back to the relay, tagged for it
          sockets.set(sid, { _event: ({ ev, text, bytes }) => {
            if (ws.readyState !== 1) return;
            if (ev === "deliver") ws.send(tagged(TEXT, g, enc.encode(text)));
            else if (ev === "deliver_bytes") ws.send(tagged(BYTES, g, new Uint8Array(bytes)));
            else if (ev === "closed") { ws.send(tagged(CLOSE, g, new Uint8Array())); drop(g); }
          } });
          tell("connect", { sid, query: m.query ?? {}, ua: m.ua ?? "" });
          onState(guests.size);
        } else if (m.leave != null) drop(m.leave);
        return;
      }
      const b = new Uint8Array(data);
      const g = new DataView(b.buffer).getUint32(1);
      const sid = guests.get(g);
      if (sid === undefined) return;
      if (b[0] === TEXT) tell("send", { sid, text: new TextDecoder().decode(b.subarray(5)) });
      else if (b[0] === BYTES) { const body = b.slice(5).buffer; tell("send", { sid, bytes: body }, [body]); }
    };
    ws.onclose = () => {
      for (const g of [...guests.keys()]) drop(g);
      if (stopped) return;
      onState(null);   // reconnecting
      setTimeout(open, wait);
      wait = Math.min(wait * 2, 10_000);
    };
  };
  open();
  return { stop() { stopped = true; for (const g of [...guests.keys()]) drop(g); ws?.close(1000); } };
}

/** The Share live control, at the lower right above the repo link. */
// what this viewer added to the canvas (saved in this browser), and a way to clear it
let mine = 0;
let clearMine = null;
let renderBar = () => {};
function setMine(n) { if (n !== mine) { mine = n; renderBar(); } }

function shareControl() {
  const box = document.getElementById("dv-live");
  if (!box) return;
  box.hidden = false;
  let hosting = null, room = null;
  window.liveBusy = () => !!hosting;   // live.js: a new version waits while this tab hosts a room
  const tidy = (u) => u.toString().replace(/%2F/gi, "/");   // owner/name reads better unescaped
  const linkFor = (r) => { const u = new URL(location.href); u.searchParams.set("live", r); u.hash = ""; return tidy(u); };
  const render = (n) => {
    box.replaceChildren();
    if (!hosting) {
      box.append(button("Share live", start, "dv-live-btn"));
      box.title = "Let others join this canvas: they see what you see, and their drawings and moves reach you";
      if (mine && clearMine) {
        const c = button("clear mine", () => clearMine(), "dv-live-act");
        c.title = "What's added to this canvas is kept in this browser only. Remove it all";
        box.append(" · ", c);
      }
      return;
    }
    const dot = Object.assign(document.createElement("span"), { className: n === null ? "dv-dot off" : "dv-dot" });
    box.append(dot, n === null ? "connecting… " : `live · ${n} guest${n === 1 ? "" : "s"} · `);
    box.append(button("copy link", async (e) => {
      const t = e.target;
      try { await navigator.clipboard.writeText(linkFor(room)); t.textContent = "copied"; } catch { prompt("The room's link:", linkFor(room)); }
      setTimeout(() => (t.textContent = "copy link"), 1500);
    }, "dv-live-act"), " · ", button("stop", stop, "dv-live-act"));
    box.title = "Anyone with the link can join while this tab is open";
  };
  function start(again) {
    room = typeof again === "string" ? again : [...crypto.getRandomValues(new Uint8Array(10))].map((b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");
    try { sessionStorage.setItem(hostKey, room); } catch { /* still works, but not across a reload */ }
    history.replaceState(null, "", linkFor(room));
    hosting = hostRoom(room, render);
    render(null);
  }
  function stop() {
    hosting?.stop();
    hosting = null;
    try { sessionStorage.removeItem(hostKey); } catch { /* fine */ }
    const u = new URL(location.href); u.searchParams.delete("live"); history.replaceState(null, "", tidy(u));
    render();
  }
  renderBar = () => { if (!hosting) render(); };
  if (live) start(live); else render();   // a host that reloads keeps its room
}

/** A guest: no Python here; danvas's frontend connects to the host's tab. */
async function joinRoom() {
  document.title = `${repo ? repo.split("/")[1] + " · " : ""}live · danvas`;
  document.getElementById("dv-boot")?.remove();
  const link = document.getElementById("dv-repo-link");
  if (repo) { link.querySelector("b").textContent = repo; link.hidden = false; }
  const box = document.getElementById("dv-live");
  if (box) {
    box.hidden = false;
    box.title = "Another person's tab runs this canvas; you are a viewer of it";
    box.append(Object.assign(document.createElement("span"), { className: "dv-dot" }), "joined a live room");
  }
  await loadDanvasFrontend();
}

// --- files on the reader's own computer (?local=owner/name) ------------------------
// The app's repo holds danvas.local.toml: the manifest to run ([app]) and, under
// [local], what to make of the files a reader opens:
//
//   [local]
//   main = ["main.tex", "*.tex"]      # the opened files' main file: first pattern that matches, shallowest path
//   [local.write]                     # files added when the opened ones lack them; {main} is the main file
//   "notesmap.toml" = '''[source]
//   path = "{main}"'''
//   [local.starter]                   # "Start new notes": files copied from the app's repo into a folder
//   "main.tex" = "src/notesmap/templates/main.tex"
//
// A danvas.toml among the opened files is used instead of [app]. Nothing is
// uploaded: the files go from the disk into this tab's Python. Chrome and Edge
// can keep a chosen folder (or file), so the map follows each save and the
// folder can be reopened next visit; other browsers read a snapshot, and
// dropping the files again updates it.

const LOCAL_SKIP_DIR = /^(\.|node_modules$|__pycache__$|venv$)/;   // hidden folders (.git, .venv, ...) too
const LOCAL_MAX_FILES = 3000;
const LOCAL_POLL_MS = 1500;
const canPickDir = typeof window.showDirectoryPicker === "function";
const canPickFile = typeof window.showOpenFilePicker === "function";
const enc = new TextEncoder();

/** IndexedDB, for the folder handle Chrome lets a page keep between visits. */
function idb(mode, fn) {
  return new Promise((resolve) => {
    try {
      const open = indexedDB.open("dv-local", 1);
      open.onupgradeneeded = () => open.result.createObjectStore("handles");
      open.onerror = () => resolve(null);
      open.onsuccess = () => {
        const tx = open.result.transaction("handles", mode);
        const req = fn(tx.objectStore("handles"));
        tx.oncomplete = () => resolve(req?.result ?? null);
        tx.onerror = () => resolve(null);
      };
    } catch { resolve(null); }
  });
}
const keepHandle = (h) => idb("readwrite", (s) => s.put(h, localApp));
const keptHandle = () => idb("readonly", (s) => s.get(localApp));

function tooMany(name) {
  return new Error(`"${name}" holds more than ${LOCAL_MAX_FILES} files: choose the folder that holds your notes, not one above it`);
}

/** Every file under a directory handle: [[path, FileSystemFileHandle], ...]. */
async function walkDir(dir, prefix = "", out = []) {
  for await (const [name, h] of dir.entries()) {
    if (h.kind === "directory") {
      if (!LOCAL_SKIP_DIR.test(name)) await walkDir(h, prefix + name + "/", out);
    } else out.push([prefix + name, h]);
    if (out.length > LOCAL_MAX_FILES) throw tooMany(dir.name);
  }
  return out;
}

/** Files from a drop or an <input>, as [[path, File], ...] relative to the chosen folder. */
async function droppedFiles(entries, fileList) {
  const out = [];
  const readEntry = async (entry, prefix) => {
    if (entry.isFile) {
      out.push([prefix + entry.name, await new Promise((ok, no) => entry.file(ok, no))]);
    } else if (entry.isDirectory && !LOCAL_SKIP_DIR.test(entry.name)) {
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise((ok, no) => reader.readEntries(ok, no));
        if (!batch.length) break;
        for (const e of batch) await readEntry(e, prefix + entry.name + "/");
      }
    }
    if (out.length > LOCAL_MAX_FILES) throw tooMany(entry.name);
  };
  if (entries) {
    // one folder dropped: paths start inside it
    if (entries.length === 1 && entries[0].isDirectory) {
      const reader = entries[0].createReader();
      for (;;) {
        const batch = await new Promise((ok, no) => reader.readEntries(ok, no));
        if (!batch.length) break;
        for (const e of batch) await readEntry(e, "");
      }
    } else for (const e of entries) await readEntry(e, "");
    return out;
  }
  for (const f of fileList) {
    const rel = f.webkitRelativePath ? f.webkitRelativePath.split("/").slice(1).join("/") : f.name;
    if (!rel.split("/").slice(0, -1).some((d) => LOCAL_SKIP_DIR.test(d))) out.push([rel, f]);
    if (out.length > LOCAL_MAX_FILES) throw tooMany(f.webkitRelativePath?.split("/")[0] || "that folder");
  }
  return out;
}

/** What a drop holds, taken at once: the browser empties dataTransfer after the
 * handler's first await. Chrome/Edge give handles (watchable); all give entries. */
function grabDrop(dt) {
  const items = [...dt.items].filter((i) => i.kind === "file");
  return {
    any: items.length > 0,
    handles: items[0]?.getAsFileSystemHandle ? Promise.all(items.map((i) => i.getAsFileSystemHandle())) : Promise.resolve([]),
    entries: items.map((i) => i.webkitGetAsEntry?.()).filter(Boolean),
  };
}

/** Open what a drop held: a watched folder or file where the browser allows it, else a snapshot. */
async function openDrop(drop, name) {
  const handles = await drop.handles.catch(() => []);
  if (handles.length === 1 && handles[0]?.kind === "directory") return pickedDir(handles[0]);
  if (handles.length && handles.every((h) => h?.kind === "file")) return pickedFiles(handles);
  const files = await droppedFiles(drop.entries, null);
  const one = drop.entries.length === 1 ? drop.entries[0].name : null;
  return files.length ? pickedSnapshot(one || name, files) : null;
}

/** What the reader opened: {name, watch, list()} where list() gives [[path, File], ...] now. */
function pickedDir(dir) {
  return { name: dir.name, watch: true, handle: dir,
    list: async () => Promise.all((await walkDir(dir)).map(async ([p, h]) => [p, await h.getFile()])) };
}
function pickedFiles(handles) {
  return { name: handles[0].name, watch: true, handle: handles.length === 1 ? handles[0] : null,
    list: async () => Promise.all(handles.map(async (h) => [h.name, await h.getFile()])) };
}
function pickedSnapshot(name, files) {
  return { name, watch: false, list: async () => files };
}

const globRe = (g) => new RegExp("^" + g.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, "[^/]*").replace(/\?/g, ".") + "$", "i");

/** The opened files as a snapshot (like a repo's), with [local.write]'s files added where missing. */
async function localSnapshot(picked, conf) {
  const files = new Map();
  for (const [p, f] of await picked.list()) {
    files.set(p, { path: p, size: f.size, sha: `${f.size}:${f.lastModified}`,
      read: async () => new Uint8Array(await f.arrayBuffer()) });
  }
  const main = addDefaults(files, conf);
  const sha = [...files.values()].map((e) => `${e.path}:${e.sha}`).sort().join("\n");
  return { repo, ref: null, sha, files, name: picked.name, main };
}

/** [local]'s defaults, into a snapshot's files: finds the main file ([local] main)
 * and adds each [local.write] file the files lack. Returns the main file's path. */
function addDefaults(files, conf) {
  const paths = [...files.keys()].filter((p) => !files.get(p).written)
    .sort((a, b) => a.split("/").length - b.split("/").length || a.localeCompare(b));
  let main = "";
  for (const g of conf?.main ?? []) {
    const re = globRe(g);
    main = paths.find((p) => re.test(p.split("/").pop())) || "";
    if (main) break;
  }
  for (const [p, text] of Object.entries(conf?.write ?? {})) {
    if ((files.has(p) && !files.get(p).written) || typeof text !== "string") continue;
    const data = enc.encode(text.replaceAll("{main}", main));
    files.set(p, { path: p, size: data.length, sha: "w:" + main, written: true, read: async () => data });
  }
  return main;
}

/** An app repo's danvas.local.toml (text). */
async function localPreset(app = localApp, ref = qs.get("ref") || "HEAD") {
  const res = await fetch(`${raw(app)}/${app}/${ref}/danvas.local.toml`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${app} has no danvas.local.toml (${res.status}): it is what says how to run plain files with it`);
  return res.text();
}

/** "Start new notes": copy [local.starter] from the app's repo into a folder the reader chooses. */
async function writeStarter(dir, conf) {
  const ref = qs.get("ref") || "HEAD";
  for (const [to, from] of Object.entries(conf?.starter ?? {})) {
    if (typeof from !== "string" || to.split("/").includes("..")) continue;
    let exists = true;
    try { await (await dirFor(dir, to, false)).getFileHandle(to.split("/").pop()); } catch { exists = false; }
    if (exists) continue;   // never overwrite the reader's own file
    const res = await fetch(`${raw(localApp)}/${localApp}/${ref}/${from}`, { cache: "no-cache" });
    if (!res.ok) throw new Error(`could not fetch ${from} from ${localApp}: ${res.status}`);
    const fh = await (await dirFor(dir, to, true)).getFileHandle(to.split("/").pop(), { create: true });
    const w = await fh.createWritable();
    await w.write(await res.arrayBuffer());
    await w.close();
  }
}
async function dirFor(dir, path, create) {
  for (const part of path.split("/").slice(0, -1)) dir = await dir.getDirectoryHandle(part, { create });
  return dir;
}

/** The opening screen: choose a folder or file, drop one, reopen last time's, or
 * start new notes. Resolves with what was opened. Python loads meanwhile. */
function askLocal(confReady) {
  const appName = localApp.split("/")[1];
  const boot = document.getElementById("dv-boot");
  boot.classList.add("ask");
  boot.innerHTML = `<div class="dv-title">Open your ${appName} notes</div>
    <div>Choose the folder that holds your notes (the <code>.tex</code> file and its images), or just the
      <code>.tex</code> file. Nothing is uploaded: the files stay on this computer and are read by this tab only.</div>
    <div class="dv-acts dv-pick"></div>
    <div class="dv-drop">or drop the folder or file here</div>
    <div class="dv-again"></div>
    <div class="dv-auth"></div>
    <form class="dv-gh"><span>Notes on GitHub (synced from Overleaf, say)?</span>
      <input name="r" placeholder="owner/name" autocomplete="off"><button class="dv-second">Open</button></form>
    <div class="dv-why"></div>`;
  const gh = boot.querySelector(".dv-gh");
  gh.onsubmit = (e) => {
    e.preventDefault();
    const r = gh.r.value.trim().replace(/^https?:\/\/github\.com\//, "").replace(/(\.git|\/)+$/, "");
    if (/^[\w.-]+\/[\w.-]+$/.test(r)) { location.href = `?repo=${r}&with=${localApp}`; return; }
    gh.r.setCustomValidity("owner/name, as in github.com/owner/name");
    gh.r.reportValidity();
  };
  gh.r.oninput = () => gh.r.setCustomValidity("");
  const acts = boot.querySelector(".dv-pick");
  const why = boot.querySelector(".dv-why");
  const oops = (e) => { if (e?.name !== "AbortError") { console.error(e); why.textContent = "⚠ " + (e.message || e); } };

  return new Promise((resolve) => {
    let done = false;
    const finish = (picked) => {
      if (done || !picked) return;
      done = true;
      if (picked.handle) keepHandle(picked.handle);
      resolve(picked);
    };
    const input = (dir) => {
      const el = Object.assign(document.createElement("input"), { type: "file", multiple: !dir });
      if (dir) el.webkitdirectory = true;
      else el.accept = ".tex,.md,.toml";
      el.onchange = async () => {
        try {
          const files = await droppedFiles(null, el.files);
          const name = dir ? (el.files[0]?.webkitRelativePath.split("/")[0] || "notes") : el.files[0]?.name;
          if (files.length) finish(pickedSnapshot(name, files));
        } catch (e) { oops(e); }
      };
      el.click();
    };
    acts.append(button("Choose folder", async () => {
      try {
        if (canPickDir) finish(pickedDir(await showDirectoryPicker({ id: "dv-notes", mode: "read" })));
        else input(true);
      } catch (e) { oops(e); }
    }), button("Choose .tex file", async () => {
      try {
        if (canPickFile) finish(pickedFiles(await showOpenFilePicker({ id: "dv-notes", multiple: true,
          types: [{ description: "Notes", accept: { "text/plain": [".tex", ".md", ".toml"] } }] })));
        else input(false);
      } catch (e) { oops(e); }
    }, "dv-second"));

    // drop: a folder or files from the desktop
    const drop = boot.querySelector(".dv-drop");
    boot.ondragover = (e) => { e.preventDefault(); drop.classList.add("on"); };
    boot.ondragleave = () => drop.classList.remove("on");
    boot.ondrop = async (e) => {
      e.preventDefault();
      drop.classList.remove("on");
      const d = grabDrop(e.dataTransfer);
      try { if (d.any) finish(await openDrop(d, "notes")); } catch (err) { oops(err); }
    };

    // last time's folder (Chrome/Edge): one click, as the browser asks again
    keptHandle().then((h) => {
      if (!h || done) return;
      boot.querySelector(".dv-again").append(button(`Reopen “${h.name}”`, async () => {
        try {
          if ((await h.requestPermission({ mode: "read" })) !== "granted") return;
          finish(h.kind === "directory" ? pickedDir(h) : pickedFiles([h]));
        } catch (e) { oops(e); }
      }));
    });

    // new notes from the app's starter files, written into a folder the reader picks
    if (canPickDir) {
      const auth = boot.querySelector(".dv-auth");
      confReady.then((conf) => {
        if (done || !conf?.starter || !Object.keys(conf.starter).length) return;
        auth.append("No notes yet? ", button("Start new notes in a folder", async () => {
          try {
            const dir = await showDirectoryPicker({ id: "dv-notes", mode: "readwrite" });
            why.textContent = "Writing the starter notes…";
            await writeStarter(dir, conf);
            why.textContent = "";
            finish(pickedDir(dir));
          } catch (e) { oops(e); }
        }, "dv-link"));
      }).catch(() => {});
    }
  });
}

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
    const [rr, w] = r.split(" with ");
    a.href = "?repo=" + rr + (w ? "&with=" + w : "");
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

const owner = (r) => r.split("/")[0].toLowerCase();

/** Copy a [[app.mount]]'s files into the app's folder, if this reader may read
 * that repo; otherwise leave them out, and return a notice saying why. */
async function mount(mt) {
  const at = String(mt.at ?? "").replace(/^\/+|\/+$/g, "");
  if (!/^[\w.-]+\/[\w.-]+$/.test(mt.repo ?? "") || !at || at.split("/").includes("..") || !Array.isArray(mt.files)) {
    console.warn("[danvas-static] mount skipped: needs repo, files and at (inside this repo)", mt);
    return null;
  }
  const askKey = `dv-mount-ok:${repo}>${mt.repo}`;
  if (token && owner(mt.repo) !== owner(repo)) {
    let ok = safeGet(askKey);
    if (ok !== "yes" && ok !== "no") {
      ok = confirm(`${repo} asks to read files from ${mt.repo} with your GitHub sign-in (${mt.files.join(", ")}).\n\nAllow?`) ? "yes" : "no";
      safeSet(askKey, ok);
    }
    if (ok === "no") {
      console.info(`[danvas-static] mount of ${mt.repo} declined`);
      return { id: `declined:${mt.repo}`, text: `You chose not to let this map read ${mt.repo}, so some of it is left out.`,
        act: ["Allow", () => { safeSet(askKey, "yes"); location.reload(); }] };
    }
  }
  try {
    say(`Reading ${mt.repo}…`);
    const s = await snapshot(mt.repo, mt.ref);
    const n = await sync(s, mt.files, `/repo/${at}`, new Map(), `Reading ${mt.repo}…`);
    console.info(`[danvas-static] mounted ${n} file(s) of ${mt.repo} at ${at}/`);
    return null;
  } catch (e) {
    if (!e.gh) throw e;
    console.info(`[danvas-static] ${mt.repo} not mounted (${e.gh}): ${token ? "no access" : "sign in to see it"}`);
    return whyNotMounted(mt.repo, e.gh);
  }
}

/** A notice for a mount GitHub refused: signed out, expired, rate-limited, the
 * sign-in app not given the repo, or the reader not a collaborator. GitHub says
 * 404 for both of the last two; the reader's installations of the app tell them apart. */
async function whyNotMounted(r, status) {
  const id = `${status}:${r}`;
  if (!token) return { id, text: `Some of this map comes from ${r}, which is private or missing. If you have access, sign in to see it.`, act: ["Sign in with GitHub", signIn] };
  if (status === 401) return { id, text: "Your GitHub sign-in has expired, so some of this map is left out.", act: ["Sign in again", signIn] };
  if (status === 403 || status === 429) return { id, text: `GitHub refused to send ${r} just now (rate limit?), so some of this map is left out. Try reloading later.` };
  let me = "", insts = [];
  try {
    const h = { Accept: "application/vnd.github+json", Authorization: "Bearer " + token };
    me = (await (await fetch("https://api.github.com/user", { headers: h })).json()).login || "";
    insts = (await (await fetch("https://api.github.com/user/installations", { headers: h })).json()).installations || [];
  } catch { /* offline: fall through to the general wording */ }
  const inst = insts.find((i) => (i.account?.login || "").toLowerCase() === owner(r));
  const who = me ? `Signed in as ${me}. ` : "";
  if (me && me.toLowerCase() === owner(r)) {
    return inst
      ? { id, text: `${who}${r} isn't among the repositories this map's sign-in app may read, so some of the map is left out.`,
          link: ["Give it access", `https://github.com/settings/installations/${inst.id}`] }
      : { id, text: `${who}The sign-in app isn't installed on your account, so ${r} is left out of this map.`,
          link: ["Install it on that repository", `${GH_APP}/installations/new`] };
  }
  return { id, text: `${who}You don't have access to ${r}, or its owner hasn't given this map's sign-in app access to it, so some of the map is left out.`,
    act: ["Sign in as someone else", signOut] };
}

/** Notices above the repo link; each can be dismissed, and stays dismissed for this map. */
function showNotices(list) {
  const box = document.getElementById("dv-notes");
  if (!box) return;
  for (const n of list) {
    const key = `dv-note-off:${repo}:${n.id}`;   // `once` notes come back next load
    if (!n.once && safeGet(key)) continue;
    const div = document.createElement("div");
    div.id = `dv-${n.id}`;
    if (n.autohide) setTimeout(() => div.remove(), n.autohide);
    div.append(n.text + " ");
    if (n.act) div.append(Object.assign(document.createElement("button"), { className: "dv-act", type: "button", textContent: n.act[0], onclick: n.act[1] }));
    if (n.link) div.append(Object.assign(document.createElement("a"), { href: n.link[1], target: "_blank", rel: "noopener", textContent: n.link[0] }), ", then reload.");
    div.append(Object.assign(document.createElement("button"), { className: "dv-x", type: "button", textContent: "×", title: "Dismiss",
      onclick: () => { if (!n.once) safeSet(key, "1"); div.remove(); } }));
    box.append(div);
  }
}

// --- loads that do not finish -------------------------------------------------------
// A phone that runs out of memory reloads the tab, which loads it again, and
// again. Each load writes how far it got; a load that finds the last two ended
// partway stops there and says where, instead of looping.

const lite = qs.has("lite");
const bootKey = `dv-boot:${repo}:${manifestPath}${lite ? ":lite" : ""}`;
const STAGES = { python: "loading Python", files: "reading the files", packages: "installing packages",
  build: "building the app", draw: "drawing the canvas" };
let tries = 1;
function stage(name) { safeSet(bootKey, JSON.stringify({ stage: name, t: Date.now(), tries })); }

function crashLoop() {
  let last = null;
  try { last = JSON.parse(safeGet(bootKey) || "null"); } catch { /* none */ }
  const recent = last && last.stage !== "ok" && Date.now() - last.t < 10 * 60_000;
  tries = recent ? (last.tries || 1) + 1 : 1;
  return recent && tries >= 3 ? last : null;
}

function stoppedScreen(last) {
  const boot = document.getElementById("dv-boot");
  boot.classList.add("ask");
  const where = STAGES[last.stage] || last.stage;
  boot.innerHTML = `<div class="dv-title">This page keeps closing</div>
    <div>The last ${last.tries} tries stopped while <b>${where}</b>. That usually means this
      device ran out of memory for the page, and the browser reloaded it.</div>
    <div class="dv-acts"></div>`;
  const acts = boot.querySelector(".dv-acts");
  const go = (url) => { try { localStorage.removeItem(bootKey); } catch { /* fine */ } location.href = url; };
  if (!lite) {
    const u = new URL(location.href); u.searchParams.set("lite", "1");
    acts.append(button("Load without pictures", () => go(u.toString().replace(/%2F/gi, "/"))), " ");
  }
  acts.append(button("Try again", () => go(location.href), lite ? "" : "dv-link"));
}

async function main() {
  if (guest) return joinRoom();
  if (!repo) return askRepo();
  const last = crashLoop();
  if (last) return stoppedScreen(last);
  if (lite) document.head.append(Object.assign(document.createElement("style"), { textContent: "img, image { display: none !important; }" }));
  document.title = `${(localApp || repo).split("/")[1]} · danvas`;
  try { devRepos = new Set(await (await fetch("/_gh/repos.json")).json()); } catch { /* not the dev server */ }

  stage("python");
  const pyReady = (async () => {
    const runtime = await (await fetch("runtime.zip")).arrayBuffer();
    await call("init", { runtime }, [runtime]);
  })();
  pyReady.catch(() => {});   // reported where it is awaited

  // the files: a GitHub repo, or (?local=) what the reader opens from their own computer
  let snap, manifestText, picked = null, localConf = null;
  if (localApp) {
    const presetText = localPreset();
    presetText.catch(() => {});
    const confReady = (async () => { const t = await presetText; await pyReady; return (await call("manifest", { text: t })).local ?? {}; })();
    confReady.catch(() => {});
    picked = await askLocal(confReady);
    const boot = document.getElementById("dv-boot");
    boot.classList.remove("ask");
    boot.innerHTML = `<div class="ring"></div><div id="dv-msg"></div>`;
    $msg = document.getElementById("dv-msg");
    stage("files");
    say("Loading Python…");
    localConf = await confReady;
    say(`Reading ${picked.name}…`);
    snap = await localSnapshot(picked, localConf);
    manifestText = snap.files.has(manifestPath) ? new TextDecoder().decode(await blob(snap, snap.files.get(manifestPath))) : await presetText;
  } else {
    say("Loading Python…");
    stage("files");
    say(`Reading ${repo}…`);
    snap = await snapshot(repo, qs.get("ref"));
    if (withApp) {
      // the repo's own danvas.toml wins; else the app's, with its defaults added
      const presetText = await localPreset(withApp, "HEAD");
      await pyReady;
      localConf = (await call("manifest", { text: presetText })).local ?? {};
      addDefaults(snap.files, localConf);
      manifestText = snap.files.has(manifestPath) ? new TextDecoder().decode(await blob(snap, snap.files.get(manifestPath))) : presetText;
    } else {
      if (!snap.files.has(manifestPath)) throw new Error(`${repo} has no ${manifestPath}: it is what tells this page which danvas app to run`);
      manifestText = new TextDecoder().decode(await blob(snap, snap.files.get(manifestPath)));
    }
  }
  const label = picked ? picked.name : repo;
  await pyReady;
  const manifest = await call("manifest", { text: manifestText });
  const m = manifest.app ?? manifest;
  maxImage = Number.isFinite(m.max_image) && m.max_image > 0 ? m.max_image : 0;

  // the code: other repos (github:), PyPI packages, and folders of this repo (paths)
  const pip = new Set(m.packages ?? []);
  const pyproject = async (s, path) => {
    const e = s.files.get(path);
    if (e) for (const d of await call("requirements", { text: new TextDecoder().decode(await blob(s, e)) })) pip.add(d);
  };
  const deps = [];   // each github: requirement, followed like this repo (below)
  for (const spec of m.requires ?? []) {
    if (!spec.startsWith("github:")) { pip.add(spec); continue; }
    const req = parseGithubReq(spec);
    say(`Fetching ${req.repo}…`);
    const s = await snapshot(req.repo, req.ref);
    const sub = req.sub ?? ([...s.files.keys()].some((p) => p.startsWith("src/") && p.endsWith(".py")) ? "src" : "");
    const dest = `/pkgs/${req.repo.replace("/", "__")}`;
    const code = [...s.files.keys()].filter((p) => !SKIP_DIRS.test(p) && (!sub || p.startsWith(sub + "/")));
    const dep = { req, sub, dest, snap: s, have: new Map() };
    deps.push(dep);
    await sync(s, code, dest, dep.have, `Fetching ${req.repo}…`);
    await call("syspath", { path: sub ? `${dest}/${sub}` : dest });
    await pyproject(s, "pyproject.toml");
  }
  const paths = m.paths ?? [];
  const have = new Map();
  const cwd = m.cwd ? m.cwd.replace(/\/?$/, "/") : "";
  // opened from this computer: every file the reader chose (they chose them for this)
  let specs = picked ? ["*"] : [manifestPath, ...paths, ...(m.files ?? []).map((f) => cwd + f)];
  if (!picked && (m.entry ?? "app.py").endsWith(".py")) specs.push(cwd + (m.entry ?? "app.py"));
  await sync(snap, specs, "/repo", have, `Reading ${label}…`);
  for (const p of paths) {
    await call("syspath", { path: "/repo/" + p.replace(/\/$/, "") });
    const parent = p.replace(/\/$/, "").split("/").slice(0, -1).join("/");
    await pyproject(snap, p.replace(/\/$/, "") + "/pyproject.toml");
    if (parent) await pyproject(snap, parent + "/pyproject.toml");
  }
  if (pip.size) {
    stage("packages");
    say(`Installing ${[...pip].join(", ")}…`);
    await call("pip", { names: [...pip] });
  }

  // what the app itself says it reads
  const notices = [];
  if (!picked) {
    const extra = await call("extra_files", { manifest });
    specs = [...specs, ...extra];
    await sync(snap, specs, "/repo", have, `Reading ${repo}…`);
    for (const mt of m.mount ?? []) { const n = await mount(mt); if (n) notices.push(n); }
  } else if ((m.mount ?? []).length) console.info("[danvas-static] [[app.mount]] is skipped for files opened from this computer");

  stage("build");
  say("Building the app…");
  console.log("[danvas-static]", await call("start", { manifest }));
  started = true;

  // this viewer's drawings: the worker holds them, this page keeps them
  const drawKey = `dv-drawings:${repo}${picked ? ":" + picked.name : ""}:${manifestPath}`;
  const restored = await call("load_drawings", { text: safeGet(drawKey) || "{}" });
  let saved = "";
  const save = async () => {
    const s = await call("drawings");
    if (s !== saved) { safeSet(drawKey, s); saved = s; setMine(await call("user_drawings")); }
  };
  setInterval(save, 3000);
  addEventListener("pagehide", save);
  clearMine = async () => {
    if (!confirm("Remove everything added to this canvas in this browser (drawings, shapes, text, notes)? This can't be undone.")) return;
    await call("clear_drawings");
    await save();
    document.getElementById("dv-restored")?.remove();
  };
  if (restored) notices.push({ id: "restored", once: true, autohide: 20_000,
    text: `What was added to this canvas here last time is back (${restored} item${restored === 1 ? "" : "s"}). ` +
      `Drawings, shapes, text and notes are kept in this browser only: nobody else sees them, and ${picked ? "your files aren't" : "the repository isn't"} changed.`,
    act: ["Clear it", () => clearMine()] });
  if (picked) notices.push({ id: "local", once: true, autohide: 15_000,
    text: picked.watch ? `Showing “${picked.name}” from this computer. Edit and save it in any editor: the map follows within a couple of seconds.`
      : `Showing “${picked.name}” from this computer. This browser can't watch it for changes: after saving, drop it on the map again.` });
  setMine(restored);

  if (!picked) remember(withApp ? `${repo} with ${withApp}` : repo);
  stage("draw");
  document.getElementById("dv-boot")?.remove();
  const link = document.getElementById("dv-repo-link");
  link.querySelector("b").textContent = picked ? picked.name : repo;
  if (picked) { link.href = "?local=" + encodeURIComponent(localApp).replace(/%2F/gi, "/"); link.title = "Open other notes"; }
  link.hidden = false;
  await loadDanvasFrontend();
  showNotices(notices);
  shareControl();
  console.log(`[danvas-static] ready ${Math.round(performance.now())} ms after navigation`);
  // still here a little after drawing: this load made it
  setTimeout(() => { tries = 0; stage("ok"); }, 8000);

  // follow the code: a github: requirement with new code (a pinned tag or commit never moves)
  const fresh = async (n, code) => { if (n || code) console.log("[danvas-static] reloaded:", await call("reload", { code: code > 0 })); status(""); };
  if (deps.length) setInterval(async () => {
    try {
      let code = 0;
      for (const d of deps) {
        const s = await snapshot(d.req.repo, d.snap.ref, true);
        if (!s || s.sha === d.snap.sha) continue;
        if (s.files.get("pyproject.toml")?.sha !== d.snap.files.get("pyproject.toml")?.sha) { await save(); location.reload(); return; }   // new dependencies: start over
        d.snap = s;
        status("Updating…");
        code += await sync(s, [...s.files.keys()].filter((p) => !SKIP_DIRS.test(p) && (!d.sub || p.startsWith(d.sub + "/"))), d.dest, d.have, "Updating…");
      }
      await fresh(0, code);
    } catch (e) { console.warn("[danvas-static] poll:", e); status(""); }
  }, devRepos.size ? 2000 : POLL_MS);

  // follow the files: the reader's own (each save, or each drop), or the repository
  let busy = false;
  const update = async (next) => {
    if (busy) return;
    busy = true;
    try {
      const s = await next();
      if (s && s.sha !== snap.sha) {
        if (!picked && s.files.get(manifestPath)?.sha !== snap.files.get(manifestPath)?.sha) { await save(); location.reload(); return; }
        snap = s;
        status("Updating…");
        await fresh(await sync(snap, specs, "/repo", have, "Updating…"), 0);
      }
    } catch (e) { console.warn("[danvas-static] poll:", e); status(""); }
    finally { busy = false; }
  };
  if (picked) {
    if (picked.watch) setInterval(() => update(() => localSnapshot(picked, localConf)), LOCAL_POLL_MS);
    // dropping files on the map again replaces them (the only way to update in browsers that cannot watch)
    addEventListener("dragover", (e) => { if ([...e.dataTransfer.items].some((i) => i.kind === "file")) e.preventDefault(); }, true);
    addEventListener("drop", async (e) => {
      const d = grabDrop(e.dataTransfer);
      if (!d.any) return;
      e.preventDefault();
      e.stopPropagation();
      try {
        const next = await openDrop(d, picked.name);
        if (!next) return;
        picked = next;
        if (picked.handle) keepHandle(picked.handle);
        update(() => localSnapshot(picked, localConf));
      } catch (err) { console.warn("[danvas-static] drop:", err); status("⚠ " + (err.message || err)); }
    }, true);
  } else {
    const next = async () => {
      const s = await snapshot(repo, snap.ref, true);
      if (s && withApp) addDefaults(s.files, localConf);
      return s;
    };
    setInterval(() => update(next), devRepos.has(repo) ? 2000 : POLL_MS);
  }
}

main().catch(fail);
