//! # danvas-source — the Rust SDK for danvas
//!
//! Dial into a running danvas canvas (any hub — `danvasd` or a Python
//! `canvas.serve()`) **as a source**: register native panels, react to what
//! browsers do, edit *anyone's* panels, and read the whole canvas. This is the
//! Rust transliteration of `danvas/source.py` (the executable reference) against
//! wire protocol v1 (`PROTOCOL.md`) — feature-for-feature, so a Rust process is
//! a first-class peer on a shared canvas alongside Python or a browser.
//!
//! ```no_run
//! use danvas_source::Client;
//!
//! let c = Client::connect("127.0.0.1:8080", "rig").unwrap();
//! // a NATIVE slider from the shared template asset:
//! c.slider("servo", 0.0, 180.0, 90.0).at(40.0, 40.0).show();
//! c.on_input("servo", |p| println!("browser set {p}"));
//! c.update("servo", "post", serde_json::json!(120));   // stream state
//!
//! // the shared plane — edit and observe panels OTHER processes own:
//! if let Some(id) = c.find("py_panel") { c.set_props(&id, [("max", 90.into())]); }
//! c.subscribe("py_button", |p| println!("reacted in Rust to {p}"));
//! std::thread::park();
//! ```
//!
//! The connection runs on a background thread with its own tokio runtime;
//! every method here is sync and non-blocking. Handlers run on one ordered
//! dispatch thread (a slow handler delays later events, not the socket).

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{json, Map, Value};
use tokio::sync::mpsc::{unbounded_channel, UnboundedSender};

/// The language-neutral native-panel templates (same asset the Python SDK
/// reads) — embedded so a built binary needs no external file.
const TEMPLATES: &str = include_str!("../../danvas/templates/components.json");

type Handler = std::sync::Arc<dyn Fn(&Value) + Send + Sync + 'static>;

#[derive(Default)]
struct State {
    // Everything declared here, for replay on (re)connect.
    reg_order: Vec<String>,
    registers: HashMap<String, Value>,
    updates: HashMap<String, Map<String, Value>>,
    subs: Vec<String>,
    // Callbacks, keyed by panel id.
    on_input: HashMap<String, Vec<Handler>>,
    on_layout: HashMap<String, Vec<Handler>>,
    frame_taps: Vec<Handler>,
    // Local mirror of the whole canvas we joined (id -> entry). Eventually
    // consistent, folded from the hub's register/update stream.
    panels: HashMap<String, PanelEntry>,
}

/// A panel on the canvas as this connection sees it (its own or a peer's).
#[derive(Clone, Debug, Default)]
pub struct PanelEntry {
    pub component: String,
    pub name: Option<String>,
    pub owner: Option<String>,
    pub props: Map<String, Value>,
    pub state: Map<String, Value>,
}

/// A live connection to a canvas as a source. Cheap to clone (an `Arc`).
#[derive(Clone)]
pub struct Client {
    inner: Arc<Inner>,
}

struct Inner {
    label: String,
    templates: Value,
    state: Mutex<State>,
    tx: UnboundedSender<Out>,       // frames -> the socket task
    dispatch: UnboundedSender<Value>, // inbound frames -> the handler thread
    connected: Arc<AtomicBool>,
}

enum Out {
    Text(String),
    // The writer already relays binary media envelopes; a send path (images/
    // video/files) is the SDK's next capability. Kept so that lands as a pure
    // addition, not a signature change.
    #[allow(dead_code)]
    Binary(Vec<u8>),
}

/// Builder for a native built-in panel (from the template asset).
pub struct PanelBuilder<'a> {
    client: &'a Client,
    id: String,
    kind: &'static str,
    data: Map<String, Value>,
    place: Map<String, Value>,
}

impl<'a> PanelBuilder<'a> {
    /// Override a data field (min/max/value/text/options/...).
    pub fn set(mut self, key: &str, value: Value) -> Self {
        self.data.insert(key.into(), value);
        self
    }
    /// Place the panel.
    pub fn at(mut self, x: f64, y: f64) -> Self {
        self.place.insert("x".into(), json!(x));
        self.place.insert("y".into(), json!(y));
        self
    }
    pub fn size(mut self, w: f64, h: f64) -> Self {
        self.place.insert("w".into(), json!(w));
        self.place.insert("h".into(), json!(h));
        self
    }
    /// Register it on the canvas.
    pub fn show(self) {
        self.client.register_template_raw(&self.id, self.kind, self.data, self.place);
    }
}

impl Client {
    /// Dial into a canvas at `url` (bare port / host:port / full ws url) with
    /// a source `label`. Returns once connected, or an error on timeout.
    pub fn connect(url: &str, label: &str) -> Result<Client, String> {
        Self::connect_opts(url, label, None)
    }

    /// As [`connect`], with a `password` for a protected canvas (runs the
    /// `/__auth__` flow and dials with the session cookie).
    pub fn connect_opts(url: &str, label: &str, password: Option<&str>)
        -> Result<Client, String>
    {
        let ws_uri = normalize_ws(url);
        let templates: Value = serde_json::from_str(TEMPLATES)
            .map_err(|e| format!("bad templates asset: {e}"))?;
        let (tx, rx) = unbounded_channel::<Out>();
        let (dtx, drx) = unbounded_channel::<Value>();
        let connected = Arc::new(AtomicBool::new(false));
        let inner = Arc::new(Inner {
            label: label.to_string(),
            templates,
            state: Mutex::new(State::default()),
            tx,
            dispatch: dtx,
            connected: connected.clone(),
        });
        let client = Client { inner: inner.clone() };

        // Ordered handler dispatch thread (off the socket, like source.py).
        {
            let c = client.clone();
            std::thread::spawn(move || {
                let mut drx = drx;
                while let Some(frame) = drx.blocking_recv() {
                    c.handle(&frame);
                }
            });
        }

        // Socket thread: its own single-thread tokio runtime running the
        // connect/replay/heartbeat/read loop.
        let password = password.map(|s| s.to_string());
        let ready = Arc::new(AtomicBool::new(false));
        {
            let inner = inner.clone();
            let ready = ready.clone();
            std::thread::spawn(move || {
                let rt = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("tokio runtime");
                rt.block_on(session_loop(inner, rx, ws_uri, password, ready));
            });
        }

        // Wait (up to 10s) for the first connection.
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !connected.load(Ordering::SeqCst) {
            if std::time::Instant::now() > deadline {
                return Err("could not reach the hub".into());
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        Ok(client)
    }

    // -- authoring native panels ---------------------------------------------
    fn tpl_builder(&self, id: &str, kind: &'static str) -> PanelBuilder<'_> {
        let data = self.inner.templates["templates"][kind]["data"]
            .as_object().cloned().unwrap_or_default();
        PanelBuilder { client: self, id: id.into(), kind, data, place: Map::new() }
    }

    /// A native slider. `.at(x,y).set("step",..).show()`.
    pub fn slider(&self, id: &str, min: f64, max: f64, value: f64) -> PanelBuilder<'_> {
        self.tpl_builder(id, "slider")
            .set("min", json!(min)).set("max", json!(max)).set("value", json!(value))
    }
    /// A native text label.
    pub fn label(&self, id: &str, text: &str) -> PanelBuilder<'_> {
        self.tpl_builder(id, "label").set("text", json!(text))
    }
    /// A native button.
    pub fn button(&self, id: &str) -> PanelBuilder<'_> {
        self.tpl_builder(id, "button")
    }
    /// Any templated built-in (slider/label/button/toggle/text_field/markdown).
    pub fn panel(&self, id: &str, kind: &str) -> PanelBuilder<'_> {
        // kind must be &'static in the builder; leak-free via a match.
        let k: &'static str = match kind {
            "slider" => "slider", "label" => "label", "button" => "button",
            "toggle" => "toggle", "text_field" => "text_field",
            "markdown" => "markdown", _ => "label",
        };
        self.tpl_builder(id, k)
    }

    fn register_template_raw(&self, id: &str, kind: &str, mut data: Map<String, Value>,
                             place: Map<String, Value>) {
        let tpl = &self.inner.templates["templates"][kind];
        let mut props = tpl["props"].as_object().cloned().unwrap_or_default();
        // template defaults are already in `data` via the builder; ensure any
        // missing default is present.
        if let Some(defaults) = tpl["data"].as_object() {
            for (k, v) in defaults {
                data.entry(k.clone()).or_insert_with(|| v.clone());
            }
        }
        props.insert("data".into(), json!(serde_json::to_string(&data).unwrap()));
        props.insert("label".into(), json!(id));
        if let Some(w) = place.get("w") { props.insert("w".into(), w.clone()); }
        if let Some(h) = place.get("h") { props.insert("h".into(), h.clone()); }
        let mut msg = json!({
            "type": "register", "id": id, "name": id,
            "component": tpl["component"], "props": props,
        });
        for k in ["x", "y"] {
            if let Some(v) = place.get(k) {
                msg.as_object_mut().unwrap().insert(k.into(), v.clone());
            }
        }
        self.record_register(id, &msg);
        self.send(&msg);
    }

    /// Register a raw panel (arbitrary `component`/`props`) — the escape hatch
    /// under the template helpers.
    pub fn register(&self, id: &str, component: &str, props: Value) {
        let msg = json!({"type": "register", "id": id, "name": id,
                         "component": component, "props": props});
        self.record_register(id, &msg);
        self.send(&msg);
    }

    /// Stream new state for a panel this source owns (e.g. `update("s","post",v)`).
    pub fn update(&self, id: &str, key: &str, value: Value) {
        {
            let mut st = self.inner.state.lock().unwrap();
            st.updates.entry(id.into()).or_default().insert(key.into(), value.clone());
        }
        self.send(&json!({"type": "update", "id": id, "payload": {key: value}}));
    }

    /// Withdraw a panel.
    pub fn remove(&self, id: &str) {
        {
            let mut st = self.inner.state.lock().unwrap();
            st.reg_order.retain(|i| i != id);
            st.registers.remove(id);
            st.updates.remove(id);
        }
        self.send(&json!({"type": "remove", "id": id}));
    }

    // -- the shared property plane -------------------------------------------
    /// Write ANY panel's properties (its own or a peer's, by composed id).
    pub fn set_props<I, K>(&self, id: &str, props: I)
    where I: IntoIterator<Item = (K, Value)>, K: Into<String> {
        let map: Map<String, Value> =
            props.into_iter().map(|(k, v)| (k.into(), v)).collect();
        self.send(&json!({"type": "set_props", "id": id, "props": map}));
    }

    /// Receive a panel's input events even though another process owns it.
    pub fn subscribe<F: Fn(&Value) + Send + Sync + 'static>(&self, id: &str, f: F) {
        {
            let mut st = self.inner.state.lock().unwrap();
            if !st.subs.iter().any(|s| s == id) { st.subs.push(id.into()); }
            st.on_input.entry(id.into()).or_default().push(std::sync::Arc::new(f));
        }
        self.send(&json!({"type": "subscribe", "id": id}));
    }

    pub fn unsubscribe(&self, id: &str) {
        {
            let mut st = self.inner.state.lock().unwrap();
            st.subs.retain(|s| s != id);
        }
        self.send(&json!({"type": "unsubscribe", "id": id}));
    }

    // -- callbacks -----------------------------------------------------------
    /// Handle a browser operating this source's panel `id`: `f(payload)`.
    pub fn on_input<F: Fn(&Value) + Send + Sync + 'static>(&self, id: &str, f: F) {
        self.inner.state.lock().unwrap()
            .on_input.entry(id.into()).or_default().push(std::sync::Arc::new(f));
    }
    /// Handle a browser moving/resizing panel `id`: `f(layout_frame)`.
    pub fn on_layout<F: Fn(&Value) + Send + Sync + 'static>(&self, id: &str, f: F) {
        self.inner.state.lock().unwrap()
            .on_layout.entry(id.into()).or_default().push(std::sync::Arc::new(f));
    }
    /// Tap every frame the hub sends — the hub's own canvas stream too (read).
    pub fn on_frame<F: Fn(&Value) + Send + Sync + 'static>(&self, f: F) {
        self.inner.state.lock().unwrap().frame_taps.push(std::sync::Arc::new(f));
    }

    // -- reading the canvas --------------------------------------------------
    /// Resolve a panel's composed id from its `name` (its own or a peer's).
    pub fn find(&self, name: &str) -> Option<String> {
        let st = self.inner.state.lock().unwrap();
        st.panels.iter()
            .find(|(_, e)| e.name.as_deref() == Some(name))
            .map(|(id, _)| id.clone())
    }
    /// A snapshot of a panel (its own or a peer's) from the local mirror.
    pub fn panel_entry(&self, id: &str) -> Option<PanelEntry> {
        self.inner.state.lock().unwrap().panels.get(id).cloned()
    }
    /// The whole canvas this connection joined (id -> entry), from the mirror.
    pub fn panels(&self) -> HashMap<String, PanelEntry> {
        self.inner.state.lock().unwrap().panels.clone()
    }
    /// True while the socket is up.
    pub fn is_connected(&self) -> bool {
        self.inner.connected.load(Ordering::SeqCst)
    }

    // -- internals -----------------------------------------------------------
    fn record_register(&self, id: &str, msg: &Value) {
        let mut st = self.inner.state.lock().unwrap();
        if !st.registers.contains_key(id) { st.reg_order.push(id.into()); }
        st.registers.insert(id.into(), msg.clone());
        st.updates.remove(id);
    }

    fn send(&self, msg: &Value) {
        let _ = self.inner.tx.send(Out::Text(msg.to_string()));
    }

    /// Route one inbound frame: taps see all; input/layout hit their handlers;
    /// register/update/remove fold the local mirror.
    fn handle(&self, msg: &Value) {
        let taps: Vec<Handler> = self.inner.state.lock().unwrap().frame_taps.clone();
        for tap in &taps { tap(msg); }
        let kind = msg.get("type").and_then(Value::as_str).unwrap_or("");
        let id = msg.get("id").and_then(Value::as_str).unwrap_or("").to_string();
        match kind {
            "input" => {
                let payload = msg.get("payload").cloned().unwrap_or(Value::Null);
                // Clone the handler Arcs OUT of the lock, then call them
                // unlocked — a handler may re-enter (update/set_props/subscribe).
                let hs: Vec<Handler> = self.inner.state.lock().unwrap()
                    .on_input.get(&id).cloned().unwrap_or_default();
                for h in &hs { h(&payload); }
            }
            "layout" => {
                let hs: Vec<Handler> = self.inner.state.lock().unwrap()
                    .on_layout.get(&id).cloned().unwrap_or_default();
                for h in &hs { h(msg); }
            }
            "register" => {
                let mut st = self.inner.state.lock().unwrap();
                let entry = PanelEntry {
                    component: msg.get("component").and_then(Value::as_str)
                        .unwrap_or("").into(),
                    name: msg.get("name").and_then(Value::as_str).map(String::from),
                    owner: msg.get("owner").and_then(Value::as_str).map(String::from),
                    props: msg.get("props").and_then(Value::as_object).cloned()
                        .unwrap_or_default(),
                    state: Map::new(),
                };
                st.panels.insert(id, entry);
            }
            "update" => {
                if let Some(payload) = msg.get("payload").and_then(Value::as_object) {
                    let mut st = self.inner.state.lock().unwrap();
                    if let Some(e) = st.panels.get_mut(&id) {
                        for (k, v) in payload { e.state.insert(k.clone(), v.clone()); }
                    }
                }
            }
            "remove" => { self.inner.state.lock().unwrap().panels.remove(&id); }
            _ => {}
        }
    }

    /// The frames that reconstruct this source on a (re)connect — registers,
    /// then accumulated state, then subscriptions.
    fn replay_frames(&self) -> Vec<String> {
        let st = self.inner.state.lock().unwrap();
        let mut out = Vec::new();
        for id in &st.reg_order {
            if let Some(reg) = st.registers.get(id) { out.push(reg.to_string()); }
        }
        for (id, payload) in &st.updates {
            out.push(json!({"type": "update", "id": id, "payload": payload}).to_string());
        }
        for id in &st.subs {
            out.push(json!({"type": "subscribe", "id": id}).to_string());
        }
        out
    }
}

fn normalize_ws(url: &str) -> String {
    let t = url.trim();
    if let Some((scheme, rest)) = t.split_once("://") {
        let ws = match scheme { "http" => "ws", "https" => "wss", s => s };
        let rest = rest.trim_end_matches('/');
        let path = if rest.ends_with("/ws") { rest.to_string() } else { format!("{rest}/ws") };
        return format!("{ws}://{path}");
    }
    let hostport = if let Some(p) = t.strip_prefix(':') {
        format!("localhost{p}").replacen("localhost", "localhost:", 1)
    } else if t.contains(':') { t.to_string() } else { format!("localhost:{t}") };
    format!("ws://{hostport}/ws")
}

async fn session_loop(
    inner: Arc<Inner>,
    mut rx: tokio::sync::mpsc::UnboundedReceiver<Out>,
    ws_uri: String,
    password: Option<String>,
    _ready: Arc<AtomicBool>,
) {
    use futures_util::{SinkExt, StreamExt};
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    use tokio_tungstenite::tungstenite::Message;
    let sep = if ws_uri.contains('?') { '&' } else { '?' };
    let label = &inner.label;
    let uri = format!("{ws_uri}{sep}source=1&label={label}&vname={label}");
    let client = Client { inner: inner.clone() };
    // For a protected canvas, run /__auth__ once for the session cookie.
    let cookie = match &password {
        Some(pw) => http_login(&ws_uri, pw).await,
        None => None,
    };
    loop {
        let request = {
            let mut r = match uri.clone().into_client_request() {
                Ok(r) => r,
                Err(_) => return,
            };
            if let Some(tok) = &cookie {
                if let Ok(v) = format!("pc_session={tok}").parse() {
                    r.headers_mut().insert("Cookie", v);
                }
            }
            r
        };
        match tokio_tungstenite::connect_async(request).await {
            Ok((stream, _)) => {
                let (mut sink, mut read) = stream.split();
                // replay everything on (re)connect
                for f in client.replay_frames() {
                    let _ = sink.send(Message::Text(f)).await;
                }
                inner.connected.store(true, Ordering::SeqCst);
                let mut hb = tokio::time::interval(std::time::Duration::from_secs(10));
                loop {
                    tokio::select! {
                        out = rx.recv() => match out {
                            Some(Out::Text(t)) => {
                                if sink.send(Message::Text(t)).await.is_err() { break; }
                            }
                            Some(Out::Binary(b)) => {
                                if sink.send(Message::Binary(b)).await.is_err() { break; }
                            }
                            None => return, // client dropped
                        },
                        _ = hb.tick() => {
                            if sink.send(Message::Text(
                                json!({"type":"heartbeat"}).to_string())).await.is_err() { break; }
                        },
                        msg = read.next() => match msg {
                            Some(Ok(Message::Text(t))) => {
                                if let Ok(v) = serde_json::from_str::<Value>(&t) {
                                    let _ = inner.dispatch.send(v);
                                }
                            }
                            Some(Ok(_)) => {}
                            _ => break, // socket closed
                        },
                    }
                }
                inner.connected.store(false, Ordering::SeqCst);
            }
            Err(_) => {}
        }
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    }
}

/// The `/__auth__` password flow: POST the password, return the pc_session
/// token from Set-Cookie. Minimal HTTP over TCP (no TLS — a tunneled
/// protected canvas would need it; localhost/LAN is the norm for a source).
async fn http_login(ws_uri: &str, password: &str) -> Option<String> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let rest = ws_uri.split_once("://")?.1;
    let hostport = rest.split('/').next()?;
    let (host, port) = hostport.rsplit_once(':')?;
    let port: u16 = port.parse().ok()?;
    let mut body = String::from("password=");
    for b in password.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' =>
                body.push(b as char),
            _ => body.push_str(&format!("%{b:02X}")),
        }
    }
    let req = format!(
        "POST /__auth__ HTTP/1.1\r\nHost: {host}\r\nContent-Type: \
         application/x-www-form-urlencoded\r\nContent-Length: {}\r\n\
         Connection: close\r\n\r\n{body}",
        body.len()
    );
    let mut stream = tokio::net::TcpStream::connect((host, port)).await.ok()?;
    stream.write_all(req.as_bytes()).await.ok()?;
    let mut buf = Vec::new();
    let _ = tokio::time::timeout(std::time::Duration::from_secs(6),
                                 stream.read_to_end(&mut buf)).await;
    let text = String::from_utf8_lossy(&buf);
    for line in text.lines() {
        if line.to_ascii_lowercase().starts_with("set-cookie:") {
            if let Some((_, rest)) = line.split_once("pc_session=") {
                let tok: String = rest.chars()
                    .take_while(|c| *c != ';' && !c.is_whitespace()).collect();
                if !tok.is_empty() { return Some(tok); }
            }
        }
    }
    None
}
