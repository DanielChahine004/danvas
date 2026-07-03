//! danvasd: the danvas standing broker (relay core).
//!
//! Speaks wire protocol v1 (../PROTOCOL.md) as a hub: dial-in sources
//! (`/ws?source=1&label=`) contribute panels; browsers (and peer subscribers)
//! receive the composed canvas; interactions route back to the owning source.
//! The design rule is the plan's "parse the envelope, not the world": frames
//! are `serde_json::Value`s — only `type`/`id`/`name`/`owner`/`start`/`end`
//! and the geometry keys are touched; everything else passes through, so new
//! panel types work through an old broker unchanged.
//!
//! Scope (phase 1, relay core): namespacing, caching + replay, fan-out,
//! input/set_props/layout route-back, subscribe/unsubscribe, retention
//! (default on: a dead source's panels freeze dimmed until its label
//! re-dials). Not yet: auth, drawings, offsets, ledger, static frontend —
//! tracked in ../docs/broker-plan.md phase 2+. The definition of done at
//! every step is ../tests/test_conformance.py (DANVAS_HUB_CMD).

use std::collections::{HashMap, HashSet};
use std::net::SocketAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, StatusCode, Uri};
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::Router;
use futures_util::{SinkExt, StreamExt};
use include_dir::{include_dir, Dir};
use serde_json::{json, Map, Value};
use tokio::sync::mpsc::{unbounded_channel, UnboundedSender};

const PROTOCOL_VERSION: u64 = 1;
const FREEZE_OPACITY: f64 = 0.45;

/// The same pre-built frontend the Python package ships, embedded at compile
/// time — a browser points straight at danvasd, no Python anywhere.
static DIST: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/../danvas/frontend/dist");

type Tx = UnboundedSender<String>;

#[derive(Default)]
struct Source {
    tag: String,
    live: bool,
    /// The 📍 origin this source is merged at: applied to content coming
    /// down, undone on interactions going back — the source never moves.
    offset: (f64, f64),
    tx: Option<Tx>,
    /// nsid -> register frame (insertion order preserved via Vec of keys).
    reg_order: Vec<String>,
    registers: HashMap<String, Value>,
    updates: HashMap<String, Map<String, Value>>,
    arrows: HashMap<String, Value>,
    /// namespaced record id -> the record's current ("after") state.
    drawings: HashMap<String, Value>,
}

/// Rewrite every record id (diff keys, records' own `id`, arrow bindings)
/// through `f`. `updated` values are `[before, after]` pairs.
fn remap_draw_diff(diff: &Value, f: &dyn Fn(&str) -> String) -> Value {
    let remap_record = |val: &Value| -> Value {
        let mut v = val.clone();
        if let Some(obj) = v.as_object_mut() {
            if let Some(Value::String(id)) = obj.get("id") {
                let nid = f(id);
                obj.insert("id".into(), Value::String(nid));
            }
            if let Some(Value::Object(props)) = obj.get_mut("props") {
                for key in ["bindStart", "bindEnd"] {
                    if let Some(Value::String(b)) = props.get(key) {
                        let nb = f(b);
                        props.insert(key.into(), Value::String(nb));
                    }
                }
            }
        }
        v
    };
    let mut out = Map::new();
    for bucket in ["added", "updated", "removed"] {
        let mut nb = Map::new();
        if let Some(Value::Object(b)) = diff.get(bucket) {
            for (rid, val) in b {
                let nv = match val {
                    Value::Array(pair) if pair.len() == 2 => Value::Array(
                        vec![remap_record(&pair[0]), remap_record(&pair[1])]),
                    other => remap_record(other),
                };
                nb.insert(f(rid), nv);
            }
        }
        out.insert(bucket.into(), Value::Object(nb));
    }
    Value::Object(out)
}

#[derive(Default)]
struct Hub {
    run_id: String,
    tag_seq: u64,
    /// browser conn id -> sender
    browsers: HashMap<u64, Tx>,
    /// source label -> Source (kept while retained-offline too)
    sources: HashMap<String, Source>,
    tag_to_label: HashMap<String, String>,
    /// composed panel id -> subscriber conn ids (browsers or sources)
    subs: HashMap<String, HashSet<u64>>,
    /// conn id -> sender for ANY connection (for subscription copies)
    conns: HashMap<u64, Tx>,
    /// Hub-native annotation ink (bare record ids): record id -> record.
    drawings: HashMap<String, Value>,
    /// --password gate: None = open. Sessions are opaque server-minted
    /// tokens carried in the pc_session cookie (PROTOCOL.md §transport).
    password: Option<String>,
    sessions: HashSet<String>,
}

impl Hub {
    fn authed(&self, headers: &HeaderMap) -> bool {
        if self.password.is_none() {
            return true;
        }
        let Some(cookies) = headers.get(header::COOKIE).and_then(|v| v.to_str().ok())
        else {
            return false;
        };
        cookies.split(';').any(|c| {
            c.trim()
                .strip_prefix("pc_session=")
                .map(|t| self.sessions.contains(t))
                .unwrap_or(false)
        })
    }
}

const LOGIN_PAGE: &str = r#"<!doctype html><html><head><title>danvas</title></head>
<body style="font-family:system-ui;background:#111;color:#eee;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0">
<form method="post" action="/__auth__" style="text-align:center">
<h2>This canvas is protected</h2>
<input type="password" name="password" autofocus
 style="padding:8px 12px;font-size:14px;border-radius:8px;border:1px solid #444;
 background:#1c1c1c;color:#eee">
<button type="submit" style="padding:8px 16px;font-size:14px;border-radius:8px;
 border:none;background:#2563eb;color:#fff;cursor:pointer">Enter</button>
</form></body></html>"#;

/// Minimal x-www-form-urlencoded "password" extraction (+ and %XX decoded).
fn form_password(body: &str) -> Option<String> {
    for pair in body.split('&') {
        let (k, v) = pair.split_once('=')?;
        if k != "password" {
            continue;
        }
        let mut out = Vec::new();
        let bytes = v.as_bytes();
        let mut i = 0;
        while i < bytes.len() {
            match bytes[i] {
                b'+' => out.push(b' '),
                b'%' if i + 2 < bytes.len() => {
                    let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).ok()?;
                    out.push(u8::from_str_radix(hex, 16).ok()?);
                    i += 2;
                }
                b => out.push(b),
            }
            i += 1;
        }
        return String::from_utf8(out).ok();
    }
    None
}

/// Fold an update payload into the replay cache the way the OWNER's own
/// reconnect replay would express it: geometry onto the cached register's top
/// level, a value `post` into the register's baked `props.data`, the rest
/// onto the accumulated updates. This is what makes a hub browser-refresh
/// equivalent to a direct source reconnect (transient channels like `post`
/// don't survive a fresh mount). The `props.data` peek is the one bounded
/// exception to "parse the envelope, not the world" — the built-in controls'
/// value convention.
fn fold_state(src: &mut Source, nsid: &str, payload: Map<String, Value>) {
    let mut rest = payload;
    if let Some(reg) = src.registers.get_mut(nsid) {
        if let Some(obj) = reg.as_object_mut() {
            for k in ["x", "y", "rotation", "opacity"] {
                if rest.get(k).map(|v| v.is_number()).unwrap_or(false) {
                    obj.insert(k.into(), rest.remove(k).unwrap());
                }
            }
            if let Some(post) = rest.get("post").cloned() {
                let folded = obj
                    .get_mut("props")
                    .and_then(Value::as_object_mut)
                    .and_then(|props| {
                        let data = props.get("data")?.as_str()?;
                        let mut blob: Value = serde_json::from_str(data).ok()?;
                        let b = blob.as_object_mut()?;
                        // The built-in controls' content keys — the one
                        // bounded convention the hub knows about panels.
                        let key = ["value", "text", "src"]
                            .into_iter()
                            .find(|k| b.contains_key(*k))?;
                        b.insert(key.into(), post.clone());
                        props.insert("data".into(), Value::String(blob.to_string()));
                        Some(())
                    })
                    .is_some();
                if folded {
                    rest.remove("post");
                }
            }
        }
    }
    if !rest.is_empty() {
        src.updates.entry(nsid.to_string()).or_default().extend(rest);
    }
}

/// Shift a frame's top-level or payload x/y by (dx, dy) where present.
fn shift_xy(obj: &mut Map<String, Value>, dx: f64, dy: f64) {
    for (key, d) in [("x", dx), ("y", dy)] {
        if let Some(v) = obj.get(key).and_then(Value::as_f64) {
            obj.insert(key.into(), json!(v + d));
        }
    }
}

impl Hub {
    fn fanout_browsers(&self, text: &str) {
        for tx in self.browsers.values() {
            let _ = tx.send(text.to_string());
        }
    }

    fn cached_frames(src: &Source) -> Vec<Value> {
        let mut out = Vec::new();
        for id in &src.reg_order {
            if let Some(reg) = src.registers.get(id) {
                out.push(reg.clone());
            }
        }
        for (id, payload) in &src.updates {
            out.push(json!({"type": "update", "id": id, "payload": payload}));
        }
        for arrow in src.arrows.values() {
            out.push(arrow.clone());
        }
        if !src.drawings.is_empty() {
            out.push(json!({"type": "draw", "diff": {
                "added": src.drawings.clone().into_iter()
                    .collect::<Map<String, Value>>(),
                "updated": {}, "removed": {}}}));
        }
        if !src.live {
            out.extend(Self::freeze_frames(src));
        }
        out
    }

    fn freeze_frames(src: &Source) -> Vec<Value> {
        src.reg_order
            .iter()
            .map(|id| {
                json!({"type": "update", "id": id,
                       "payload": {"operable": false, "opacity": FREEZE_OPACITY}})
            })
            .collect()
    }

    /// The merge-panel roster: one entry per source, live or retained-offline.
    fn roster_frame(&self) -> Value {
        let sources: Vec<Value> = self
            .sources
            .iter()
            .map(|(label, s)| {
                json!({"sid": s.tag, "label": label,
                       "uri": format!("dialin:{label}"),
                       "status": if s.live { "live" } else { "offline" },
                       "offset": [s.offset.0, s.offset.1]})
            })
            .collect();
        json!({"type": "merge_sources", "sources": sources})
    }

    fn teardown_frames(src: &Source) -> Vec<Value> {
        let mut out: Vec<Value> = src
            .reg_order
            .iter()
            .chain(src.arrows.keys())
            .map(|id| json!({"type": "remove", "id": id}))
            .collect();
        if !src.drawings.is_empty() {
            // Ink lives under its own ids, not shape ids — removed via a diff.
            let removed: Map<String, Value> = src
                .drawings
                .keys()
                .map(|k| (k.clone(), json!({})))
                .collect();
            out.push(json!({"type": "draw", "diff":
                {"added": {}, "updated": {}, "removed": removed}}));
        }
        out
    }
}

fn now_hex() -> String {
    let ns = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("{ns:x}")
}

static CONN_SEQ: AtomicU64 = AtomicU64::new(1);

#[tokio::main]
async fn main() {
    let mut port: u16 = 8080;
    let mut password: Option<String> = None;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--port" => {
                if let Some(p) = args.next().and_then(|v| v.parse().ok()) {
                    port = p;
                }
            }
            "--password" => password = args.next().filter(|s| !s.is_empty()),
            _ => {}
        }
    }
    let hub = Arc::new(Mutex::new(Hub {
        run_id: now_hex(),
        password,
        ..Default::default()
    }));
    let app = Router::new()
        .route("/ws", get(ws_handler))
        .route("/__auth__", post(auth_handler))
        .fallback(get(static_handler))
        .with_state(hub);
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    println!("[danvasd] serving ws://{addr}/ws");
    axum::serve(listener, app).await.expect("serve");
}

async fn auth_handler(State(hub): State<Arc<Mutex<Hub>>>, body: String) -> impl IntoResponse {
    let mut h = hub.lock().unwrap();
    let ok = match (&h.password, form_password(&body)) {
        (Some(pw), Some(given)) => &given == pw,
        (None, _) => true, // open hub: the login is a no-op redirect
        _ => false,
    };
    if !ok {
        return (StatusCode::UNAUTHORIZED,
                [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                LOGIN_PAGE).into_response();
    }
    let token = format!("{}{}", now_hex(), CONN_SEQ.fetch_add(1, Ordering::Relaxed));
    h.sessions.insert(token.clone());
    (StatusCode::SEE_OTHER,
     [(header::LOCATION, "/".to_string()),
      (header::SET_COOKIE,
       format!("pc_session={token}; Path=/; SameSite=Lax; HttpOnly"))],
     "").into_response()
}

async fn static_handler(
    State(hub): State<Arc<Mutex<Hub>>>,
    headers: HeaderMap,
    uri: Uri,
) -> impl IntoResponse {
    if !hub.lock().unwrap().authed(&headers) {
        return (StatusCode::UNAUTHORIZED,
                [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                LOGIN_PAGE.as_bytes()).into_response();
    }
    let path = uri.path().trim_start_matches('/');
    let path = if path.is_empty() { "index.html" } else { path };
    // Unknown paths fall back to the SPA index, matching the Python server.
    let file = DIST.get_file(path).or_else(|| DIST.get_file("index.html"));
    match file {
        Some(f) => {
            let mime = match path.rsplit_once('.').map(|(_, e)| e) {
                Some("html") | None => "text/html; charset=utf-8",
                Some("js") => "text/javascript",
                Some("css") => "text/css",
                Some("svg") => "image/svg+xml",
                Some("png") => "image/png",
                Some("json") => "application/json",
                Some("woff2") => "font/woff2",
                Some("ico") => "image/x-icon",
                _ => "application/octet-stream",
            };
            ([(header::CONTENT_TYPE, mime)], f.contents()).into_response()
        }
        None => (StatusCode::NOT_FOUND, "frontend not built").into_response(),
    }
}

async fn ws_handler(
    ws: WebSocketUpgrade,
    Query(q): Query<HashMap<String, String>>,
    headers: HeaderMap,
    State(hub): State<Arc<Mutex<Hub>>>,
) -> impl IntoResponse {
    if !hub.lock().unwrap().authed(&headers) {
        return (StatusCode::UNAUTHORIZED, "login required").into_response();
    }
    ws.on_upgrade(move |socket| handle(socket, q, hub)).into_response()
}

async fn handle(socket: WebSocket, q: HashMap<String, String>, hub: Arc<Mutex<Hub>>) {
    let conn_id = CONN_SEQ.fetch_add(1, Ordering::Relaxed);
    let is_source = q.get("source").map(|v| !v.is_empty()).unwrap_or(false);
    let label = q
        .get("label")
        .cloned()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("source{conn_id}"));

    let (mut sink, mut stream) = socket.split();
    let (tx, mut rx) = unbounded_channel::<String>();
    let writer = tokio::spawn(async move {
        while let Some(text) = rx.recv().await {
            if sink.send(Message::Text(text)).await.is_err() {
                break;
            }
        }
    });

    // Welcome first, always — the client's version check reads it.
    let welcome = {
        let h = hub.lock().unwrap();
        json!({
            "type": "welcome",
            "protocol": PROTOCOL_VERSION,
            "you": {"id": format!("v{conn_id}"), "name": label, "color": "#3b82f6",
                     "device": "desktop", "role": null},
            "runId": h.run_id,
            "mergeHost": true,
            "uiInspector": false, "uiGraveyard": false, "uiHosting": false,
        })
    };
    let _ = tx.send(welcome.to_string());

    if is_source {
        attach_source(&hub, &label, conn_id, tx.clone());
    } else {
        // Browser (or observing peer): replay every source's composed state.
        let frames: Vec<String> = {
            let mut h = hub.lock().unwrap();
            h.browsers.insert(conn_id, tx.clone());
            h.conns.insert(conn_id, tx.clone());
            let mut out: Vec<String> = h
                .sources
                .values()
                .flat_map(|s| Hub::cached_frames(s))
                .map(|f| f.to_string())
                .collect();
            if !h.drawings.is_empty() {
                out.push(json!({"type": "draw", "diff": {
                    "added": h.drawings.clone().into_iter()
                        .collect::<Map<String, Value>>(),
                    "updated": {}, "removed": {}}}).to_string());
            }
            if !h.sources.is_empty() {
                out.push(h.roster_frame().to_string());
            }
            out
        };
        for f in frames {
            let _ = tx.send(f);
        }
    }

    while let Some(Ok(msg)) = stream.next().await {
        let text = match msg {
            Message::Text(t) => t,
            Message::Close(_) => break,
            _ => continue,
        };
        let Ok(frame) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        if is_source {
            source_frame(&hub, &label, conn_id, frame);
        } else {
            client_frame(&hub, conn_id, frame);
        }
    }

    // -- disconnect ----------------------------------------------------------
    {
        let mut h = hub.lock().unwrap();
        h.browsers.remove(&conn_id);
        h.conns.remove(&conn_id);
        for subs in h.subs.values_mut() {
            subs.remove(&conn_id);
        }
        if is_source {
            // Retention (default on): keep the caches, freeze the panels.
            let mut went_offline = false;
            let frames: Vec<String> = if let Some(src) = h.sources.get_mut(&label) {
                if src.tx.as_ref().map(|t| t.same_channel(&tx)).unwrap_or(false) {
                    src.live = false;
                    src.tx = None;
                    went_offline = true;
                    Hub::freeze_frames(src).iter().map(|f| f.to_string()).collect()
                } else {
                    Vec::new() // an older life; the label was already re-taken
                }
            } else {
                Vec::new()
            };
            for f in &frames {
                h.fanout_browsers(f);
            }
            if went_offline {
                let roster = h.roster_frame().to_string();
                h.fanout_browsers(&roster);
            }
        }
    }
    writer.abort();
}

fn attach_source(hub: &Arc<Mutex<Hub>>, label: &str, conn_id: u64, tx: Tx) {
    let mut h = hub.lock().unwrap();
    h.conns.insert(conn_id, tx.clone());
    if !h.sources.contains_key(label) {
        let tag = format!("s{}", h.tag_seq);
        h.tag_seq += 1;
        h.tag_to_label.insert(tag.clone(), label.to_string());
        h.sources.insert(
            label.to_string(),
            Source { tag, ..Default::default() },
        );
    }
    // Same label re-dialing = the source's next life: stale frames out first
    // (ids are minted per run on the source side).
    let teardown: Vec<String> = {
        let src = h.sources.get_mut(label).unwrap();
        let frames = Hub::teardown_frames(src).iter().map(|f| f.to_string()).collect();
        src.reg_order.clear();
        src.registers.clear();
        src.updates.clear();
        src.arrows.clear();
        src.drawings.clear();
        src.live = true;
        src.tx = Some(tx);
        frames
    };
    for f in &teardown {
        h.fanout_browsers(f);
    }
    let roster = h.roster_frame().to_string();
    h.fanout_browsers(&roster);
}

/// A frame FROM a dial-in source: its canvas content, namespaced + cached +
/// fanned out. Anything else (heartbeat, petitions on others' panels) falls
/// through to the shared client path.
fn source_frame(hub: &Arc<Mutex<Hub>>, label: &str, conn_id: u64, mut frame: Value) {
    let kind = frame.get("type").and_then(Value::as_str).unwrap_or("").to_string();
    if kind == "draw" {
        // The source's own ink: namespace every record, fold into the replay
        // cache (updated pairs keep the "after" state), fan out.
        let mut h = hub.lock().unwrap();
        let Some(src) = h.sources.get(label) else { return };
        let tag = src.tag.clone();
        let ns_diff = remap_draw_diff(
            frame.get("diff").unwrap_or(&Value::Null),
            &|r: &str| format!("{tag}:{r}"),
        );
        let src = h.sources.get_mut(label).unwrap();
        if let Some(Value::Object(a)) = ns_diff.get("added") {
            for (k, v) in a {
                src.drawings.insert(k.clone(), v.clone());
            }
        }
        if let Some(Value::Object(u)) = ns_diff.get("updated") {
            for (k, v) in u {
                let after = match v {
                    Value::Array(p) if p.len() == 2 => p[1].clone(),
                    other => other.clone(),
                };
                src.drawings.insert(k.clone(), after);
            }
        }
        if let Some(Value::Object(r)) = ns_diff.get("removed") {
            for k in r.keys() {
                src.drawings.remove(k);
            }
        }
        let text = json!({"type": "draw", "diff": ns_diff}).to_string();
        h.fanout_browsers(&text);
        return;
    }
    if !matches!(kind.as_str(), "register" | "update" | "remove" | "arrow") {
        client_frame(hub, conn_id, frame);
        return;
    }
    let mut h = hub.lock().unwrap();
    let Some(src) = h.sources.get(label) else { return };
    let tag = src.tag.clone();
    let ns = |id: &str| format!("{tag}:{id}");
    let raw_id = frame.get("id").and_then(Value::as_str).unwrap_or("").to_string();
    let nsid = ns(&raw_id);
    let obj = frame.as_object_mut().unwrap();
    obj.insert("id".into(), Value::String(nsid.clone()));
    match kind.as_str() {
        "register" => {
            // Re-stamp ownership: on the composed canvas the owner is the
            // source, by its label — whatever it says about itself.
            obj.insert("owner".into(), Value::String(label.to_string()));
            let src = h.sources.get_mut(label).unwrap();
            let (ox, oy) = src.offset;
            shift_xy(frame.as_object_mut().unwrap(), ox, oy);
            let src = h.sources.get_mut(label).unwrap();
            if !src.registers.contains_key(&nsid) {
                src.reg_order.push(nsid.clone());
            }
            src.registers.insert(nsid, frame.clone());
        }
        "update" => {
            let (ox, oy) = h.sources.get(label).map(|s| s.offset).unwrap_or((0.0, 0.0));
            let mut payload = frame
                .get("payload")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            shift_xy(&mut payload, ox, oy);
            frame
                .as_object_mut()
                .unwrap()
                .insert("payload".into(), Value::Object(payload.clone()));
            let src = h.sources.get_mut(label).unwrap();
            fold_state(src, &nsid, payload);
        }
        "remove" => {
            let src = h.sources.get_mut(label).unwrap();
            src.reg_order.retain(|i| i != &nsid);
            src.registers.remove(&nsid);
            src.updates.remove(&nsid);
            src.arrows.remove(&nsid);
        }
        "arrow" => {
            // Endpoints: the sender's own panels get its namespace; a
            // reference to a panel it can SEE but doesn't own (an already-
            // composed id) passes through — cross-source arrows.
            for key in ["start", "end"] {
                if let Some(Value::String(r)) = frame.get(key) {
                    let composed = compose_endpoint(&h, &tag, r);
                    frame.as_object_mut().unwrap().insert(key.into(), Value::String(composed));
                }
            }
            let src = h.sources.get_mut(label).unwrap();
            src.arrows.insert(nsid, frame.clone());
        }
        _ => {}
    }
    let text = frame.to_string();
    h.fanout_browsers(&text);
}

fn compose_endpoint(h: &Hub, own_tag: &str, r: &str) -> String {
    if let Some((tag, rest)) = r.split_once(':') {
        if !rest.is_empty() && h.tag_to_label.contains_key(tag) {
            return r.to_string(); // another source's composed id: untouched
        }
    }
    format!("{own_tag}:{r}")
}

/// A frame from a browser (or a source acting as a peer): petitions on
/// composed panels route to the owner; subscriptions live at the hub.
fn client_frame(hub: &Arc<Mutex<Hub>>, conn_id: u64, frame: Value) {
    let kind = frame.get("type").and_then(Value::as_str).unwrap_or("");
    if kind == "draw" {
        // A viewer's ink edit: records under a source's namespace route back
        // to that owner (stripped); bare records are hub-native annotation,
        // relayed to the other browsers.
        let Some(diff) = frame.get("diff") else { return };
        let mut h = hub.lock().unwrap();
        let mut per_dest: HashMap<Option<String>, Map<String, Value>> = HashMap::new();
        for bucket in ["added", "updated", "removed"] {
            if let Some(Value::Object(b)) = diff.get(bucket) {
                for (rid, val) in b {
                    let dest = rid
                        .split_once(':')
                        .filter(|(t, _)| h.tag_to_label.contains_key(*t))
                        .map(|(t, _)| t.to_string());
                    let entry = per_dest.entry(dest).or_insert_with(|| {
                        let mut m = Map::new();
                        for bk in ["added", "updated", "removed"] {
                            m.insert(bk.into(), json!({}));
                        }
                        m
                    });
                    entry
                        .get_mut(bucket)
                        .and_then(Value::as_object_mut)
                        .unwrap()
                        .insert(rid.clone(), val.clone());
                }
            }
        }
        for (dest, sub) in per_dest {
            match dest {
                Some(tag) => {
                    let stripped = remap_draw_diff(&Value::Object(sub), &|r: &str| {
                        r.split_once(':')
                            .map(|(_, rest)| rest.to_string())
                            .unwrap_or_else(|| r.to_string())
                    });
                    if let Some(label) = h.tag_to_label.get(&tag) {
                        if let Some(src) = h.sources.get(label) {
                            if let Some(tx) = &src.tx {
                                let _ = tx.send(
                                    json!({"type": "draw", "diff": stripped})
                                        .to_string(),
                                );
                            }
                        }
                    }
                }
                None => {
                    // Hub-native annotation: store for replay, relay to the
                    // other viewers.
                    if let Some(Value::Object(a)) = sub.get("added") {
                        for (k, v) in a {
                            h.drawings.insert(k.clone(), v.clone());
                        }
                    }
                    if let Some(Value::Object(u)) = sub.get("updated") {
                        for (k, v) in u {
                            let after = match v {
                                Value::Array(p) if p.len() == 2 => p[1].clone(),
                                other => other.clone(),
                            };
                            h.drawings.insert(k.clone(), after);
                        }
                    }
                    if let Some(Value::Object(r)) = sub.get("removed") {
                        for k in r.keys() {
                            h.drawings.remove(k);
                        }
                    }
                    let text =
                        json!({"type": "draw", "diff": Value::Object(sub)}).to_string();
                    for (bid, btx) in &h.browsers {
                        if *bid != conn_id {
                            let _ = btx.send(text.clone());
                        }
                    }
                }
            }
        }
        return;
    }
    if kind == "merge_offset" {
        // The 📍 origin drag: translate a source's whole block, hub-wide.
        // Cache shifts so replay lands at the new origin; live updates nudge
        // every open browser; the roster reports the offset.
        let sid = frame.get("sid").and_then(Value::as_str).unwrap_or("").to_string();
        let nx = frame.get("x").and_then(Value::as_f64).unwrap_or(0.0);
        let ny = frame.get("y").and_then(Value::as_f64).unwrap_or(0.0);
        let mut h = hub.lock().unwrap();
        let Some(label) = h.tag_to_label.get(&sid).cloned() else { return };
        let updates: Vec<String> = {
            let src = h.sources.get_mut(&label).unwrap();
            let (dx, dy) = (nx - src.offset.0, ny - src.offset.1);
            if dx == 0.0 && dy == 0.0 {
                return;
            }
            src.offset = (nx, ny);
            let mut out = Vec::new();
            for (id, reg) in src.registers.iter_mut() {
                if let Some(obj) = reg.as_object_mut() {
                    shift_xy(obj, dx, dy);
                    if let (Some(x), Some(y)) = (obj.get("x"), obj.get("y")) {
                        out.push(json!({"type": "update", "id": id,
                                        "payload": {"x": x, "y": y}}).to_string());
                    }
                }
            }
            for payload in src.updates.values_mut() {
                shift_xy(payload, dx, dy);
            }
            out
        };
        for u in &updates {
            h.fanout_browsers(u);
        }
        let roster = h.roster_frame().to_string();
        h.fanout_browsers(&roster);
        return;
    }
    let Some(cid) = frame.get("id").and_then(Value::as_str).map(String::from) else {
        return; // heartbeat / chat / plumbing: nothing to route in phase 1
    };
    match kind {
        "subscribe" => {
            let mut h = hub.lock().unwrap();
            h.subs.entry(cid).or_default().insert(conn_id);
        }
        "unsubscribe" => {
            let mut h = hub.lock().unwrap();
            if let Some(s) = h.subs.get_mut(&cid) {
                s.remove(&conn_id);
            }
        }
        "input" | "set_props" | "layout" | "request" => {
            let Some((tag, rest)) = cid.split_once(':') else { return };
            let mut h = hub.lock().unwrap();
            let Some(label) = h.tag_to_label.get(tag).cloned() else { return };
            let offset = h.sources.get(&label).map(|s| s.offset).unwrap_or((0.0, 0.0));
            if let Some(src) = h.sources.get(&label) {
                if let Some(tx) = &src.tx {
                    let mut out = frame.clone();
                    let obj = out.as_object_mut().unwrap();
                    obj.insert("id".into(), Value::String(rest.to_string()));
                    // merged-view coords -> the source's own coords
                    if kind == "layout" {
                        shift_xy(obj, -offset.0, -offset.1);
                    } else if kind == "set_props" {
                        if let Some(Value::Object(p)) = obj.get_mut("props") {
                            shift_xy(p, -offset.0, -offset.1);
                        }
                    }
                    let _ = tx.send(out.to_string());
                }
            }
            if kind == "layout" {
                // The owner doesn't echo layout back; the hub folds the
                // (merged-view) geometry into its replay cache and keeps the
                // OTHER browsers in step — same division of labour as the
                // Python hub.
                let mut geom = Map::new();
                for key in ["x", "y", "w", "h", "rotation"] {
                    if let Some(v) = frame.get(key) {
                        if !v.is_null() {
                            geom.insert(key.into(), v.clone());
                        }
                    }
                }
                if !geom.is_empty() {
                    if let Some(src) = h.sources.get_mut(&label) {
                        fold_state(src, &cid, geom.clone());
                    }
                    let text = json!({"type": "update", "id": cid,
                                      "payload": geom}).to_string();
                    for (bid, btx) in &h.browsers {
                        if *bid != conn_id {
                            let _ = btx.send(text.clone());
                        }
                    }
                }
            }
            if kind == "input" {
                // Event subscription fan-out (composed id; originator excluded).
                if let Some(sub_ids) = h.subs.get(&cid) {
                    let copy = json!({"type": "input", "id": cid,
                                      "payload": frame.get("payload").cloned()
                                                      .unwrap_or(Value::Null)});
                    let text = copy.to_string();
                    for sid in sub_ids {
                        if *sid != conn_id {
                            if let Some(tx) = h.conns.get(sid) {
                                let _ = tx.send(text.clone());
                            }
                        }
                    }
                }
            }
        }
        _ => {}
    }
}
