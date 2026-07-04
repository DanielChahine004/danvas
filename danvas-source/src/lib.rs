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
type BinHandler = std::sync::Arc<dyn Fn(&[u8]) + Send + Sync + 'static>;
/// A request handler: given the request `data`, return the reply value.
type RequestHandler = std::sync::Arc<dyn Fn(&Value) -> Value + Send + Sync + 'static>;
/// A snapshot/screenshot reply handler (given the reply `data`).
type SnapHandler = std::sync::Arc<dyn Fn(&Value) + Send + Sync + 'static>;

/// One frame off the socket, on its way to the ordered dispatch thread: a JSON
/// canvas frame, or a binary media envelope (video/audio/opaque input).
enum Inbound {
    Json(Value),
    Bin { code: u8, id: String, data: Vec<u8> },
}

#[derive(Default)]
struct State {
    // Everything declared here, for replay on (re)connect.
    reg_order: Vec<String>,
    registers: HashMap<String, Value>,
    updates: HashMap<String, Map<String, Value>>,
    // Managed shapes + arrows this source owns, kept current for replay.
    shape_order: Vec<String>,
    shapes: HashMap<String, Value>,
    arrow_order: Vec<String>,
    arrows: HashMap<String, Value>,
    subs: Vec<String>,
    // Callbacks, keyed by panel id.
    on_input: HashMap<String, Vec<Handler>>,
    on_layout: HashMap<String, Vec<Handler>>,
    on_binary: HashMap<String, Vec<BinHandler>>,
    on_request: HashMap<String, RequestHandler>,   // one answer per panel
    frame_taps: Vec<Handler>,
    // Multiuser: the browser audience + chat/cursor observers.
    viewers: Vec<Value>,
    on_presence: Vec<Handler>,
    on_cursor: Vec<Handler>,
    on_chat: Vec<Handler>,
    on_draw: Vec<Handler>,
    // Canvas state kept for replay: the camera/chrome + shared React assets.
    view: Map<String, Value>,
    shared_components: Map<String, Value>,
    shared_styles: String,
    // Pending get_snapshot/get_image replies, keyed by reqId.
    snapshot_cbs: HashMap<String, SnapHandler>,
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
    dispatch: UnboundedSender<Inbound>, // inbound frames -> the handler thread
    connected: Arc<AtomicBool>,
    seq: std::sync::atomic::AtomicU64, // monotonic reqId source (snapshots)
}

enum Out {
    Text(String),
    Binary(Vec<u8>),
}

/// The binary envelope: `[code][idLen][id bytes][payload]` (PROTOCOL.md
/// §binary envelope) — the same framing the Python hub and browser use.
fn bin_frame(code: u8, id: &str, payload: &[u8]) -> Vec<u8> {
    let idb = id.as_bytes();
    let mut v = Vec::with_capacity(2 + idb.len() + payload.len());
    v.push(code);
    v.push(idb.len() as u8);
    v.extend_from_slice(idb);
    v.extend_from_slice(payload);
    v
}

fn parse_bin(data: &[u8]) -> Option<(u8, String, Vec<u8>)> {
    if data.len() < 2 {
        return None;
    }
    let code = data[0];
    let idlen = data[1] as usize;
    if data.len() < 2 + idlen {
        return None;
    }
    let id = String::from_utf8_lossy(&data[2..2 + idlen]).into_owned();
    Some((code, id, data[2 + idlen..].to_vec()))
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

/// Builder for a managed canvas shape (geo/text/note/draw/line/highlight/frame).
pub struct ShapeBuilder<'a> {
    client: &'a Client,
    id: String,
    shape_type: String,
    x: f64,
    y: f64,
    rotation: f64,
    opacity: f64,
    props: Map<String, Value>,
}

impl<'a> ShapeBuilder<'a> {
    pub fn at(mut self, x: f64, y: f64) -> Self { self.x = x; self.y = y; self }
    pub fn size(mut self, w: f64, h: f64) -> Self {
        self.props.insert("w".into(), json!(w));
        self.props.insert("h".into(), json!(h));
        self
    }
    pub fn rotation(mut self, deg: f64) -> Self { self.rotation = deg; self }
    pub fn opacity(mut self, o: f64) -> Self { self.opacity = o; self }
    /// Set a shape prop (geo/color/fill/dash/size/text/points/…).
    pub fn prop(mut self, key: &str, value: Value) -> Self {
        self.props.insert(key.into(), value);
        self
    }
    /// Place the shape on the canvas.
    pub fn show(self) {
        let msg = json!({
            "type": "shape", "id": self.id, "shapeType": self.shape_type,
            "x": self.x, "y": self.y, "rotation": self.rotation,
            "opacity": self.opacity, "props": self.props,
        });
        self.client.record_shape(&self.id, &msg);
        self.client.send(&msg);
    }
}

/// Builder for an arrow bound between two endpoints (panels or shapes, by id).
pub struct ArrowBuilder<'a> {
    client: &'a Client,
    id: String,
    start: String,
    end: String,
    props: Map<String, Value>,
}

impl<'a> ArrowBuilder<'a> {
    /// Set an arrow prop (color/dash/size/text/bend/arrowhead_start/_end).
    pub fn prop(mut self, key: &str, value: Value) -> Self {
        self.props.insert(key.into(), value);
        self
    }
    /// Bind the arrow on the canvas.
    pub fn show(self) {
        let msg = json!({
            "type": "arrow", "id": self.id,
            "start": self.start, "end": self.end, "props": self.props,
        });
        self.client.record_arrow(&self.id, &msg);
        self.client.send(&msg);
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
        let (dtx, drx) = unbounded_channel::<Inbound>();
        let connected = Arc::new(AtomicBool::new(false));
        let inner = Arc::new(Inner {
            label: label.to_string(),
            templates,
            state: Mutex::new(State::default()),
            tx,
            dispatch: dtx,
            connected: connected.clone(),
            seq: std::sync::atomic::AtomicU64::new(0),
        });
        let client = Client { inner: inner.clone() };

        // Ordered handler dispatch thread (off the socket, like source.py).
        {
            let c = client.clone();
            std::thread::spawn(move || {
                let mut drx = drx;
                while let Some(inbound) = drx.blocking_recv() {
                    c.handle_inbound(inbound);
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
    /// A native video panel — stream JPEG frames to it with [`send_video`].
    pub fn video(&self, id: &str) -> PanelBuilder<'_> {
        self.tpl_builder(id, "video")
    }
    /// A native audio panel — stream int16 PCM to it with [`send_audio`].
    pub fn audio(&self, id: &str) -> PanelBuilder<'_> {
        self.tpl_builder(id, "audio")
    }
    /// Any templated built-in (slider/label/button/toggle/text_field/markdown/
    /// video/audio).
    pub fn panel(&self, id: &str, kind: &str) -> PanelBuilder<'_> {
        // kind must be &'static in the builder; leak-free via a match.
        let k: &'static str = match kind {
            "slider" => "slider", "label" => "label", "button" => "button",
            "toggle" => "toggle", "text_field" => "text_field",
            "markdown" => "markdown", "video" => "video", "audio" => "audio",
            _ => "label",
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

    /// Withdraw a panel, shape, or arrow this source owns.
    pub fn remove(&self, id: &str) {
        {
            let mut st = self.inner.state.lock().unwrap();
            st.reg_order.retain(|i| i != id);
            st.registers.remove(id);
            st.updates.remove(id);
            st.shape_order.retain(|i| i != id);
            st.shapes.remove(id);
            st.arrow_order.retain(|i| i != id);
            st.arrows.remove(id);
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
    /// Handle raw binary a browser sends to panel `id` (canvas.sendBinary /
    /// requestCamera / requestMicrophone → `@on_binary`): `f(bytes)`.
    pub fn on_binary<F: Fn(&[u8]) + Send + Sync + 'static>(&self, id: &str, f: F) {
        self.inner.state.lock().unwrap()
            .on_binary.entry(id.into()).or_default().push(std::sync::Arc::new(f));
    }
    /// Answer a browser's `await canvas.request(data)` on panel `id`: the
    /// closure's return value resolves the caller's Promise. One answer per
    /// panel (a later call replaces it), matching the Python contract.
    pub fn on_request<F: Fn(&Value) -> Value + Send + Sync + 'static>(&self, id: &str, f: F) {
        self.inner.state.lock().unwrap()
            .on_request.insert(id.into(), std::sync::Arc::new(f));
    }

    // -- multiuser (presence / cursors / chat) -------------------------------
    /// The current browser audience (id/name/color/device/role), folded from
    /// the hub's presence stream.
    pub fn viewers(&self) -> Vec<Value> {
        self.inner.state.lock().unwrap().viewers.clone()
    }
    /// React to the roster changing (a viewer joins/leaves/renames): `f(frame)`.
    pub fn on_presence<F: Fn(&Value) + Send + Sync + 'static>(&self, f: F) {
        self.inner.state.lock().unwrap().on_presence.push(std::sync::Arc::new(f));
    }
    /// React to a viewer's pointer moving (needs serve(cursors=True)): `f(frame)`.
    pub fn on_cursor<F: Fn(&Value) + Send + Sync + 'static>(&self, f: F) {
        self.inner.state.lock().unwrap().on_cursor.push(std::sync::Arc::new(f));
    }
    /// Post a chat line to the shared room.
    pub fn chat(&self, text: &str) {
        self.send(&json!({"type": "chat", "text": text}));
    }
    /// React to a chat line (from any viewer or peer): `f(frame)`.
    pub fn on_chat<F: Fn(&Value) + Send + Sync + 'static>(&self, f: F) {
        self.inner.state.lock().unwrap().on_chat.push(std::sync::Arc::new(f));
    }

    // -- canvas state (view / shared assets / ink / z-order / snapshot) -------
    /// Set the camera/chrome (x/y/zoom/locked/ui/grid/read_only/…); folds into
    /// the hub view and replays on reconnect.
    pub fn set_view<I, K>(&self, fields: I)
    where I: IntoIterator<Item = (K, Value)>, K: Into<String> {
        let delta: Map<String, Value> =
            fields.into_iter().map(|(k, v)| (k.into(), v)).collect();
        {
            let mut st = self.inner.state.lock().unwrap();
            for (k, v) in &delta { st.view.insert(k.clone(), v.clone()); }
        }
        self.send(&json!({"type": "view", "view": delta}));
    }
    /// Register a shared React component by name (canvas.define) — usable in
    /// every React panel. Replays before registers so panels mount with it.
    pub fn define(&self, name: &str, source: &str) {
        let shared = {
            let mut st = self.inner.state.lock().unwrap();
            st.shared_components.insert(name.into(), json!(source));
            self.shared_frame(&st)
        };
        self.send(&shared);
    }
    /// Set the global stylesheet (canvas.style).
    pub fn style(&self, css: &str) {
        let shared = {
            let mut st = self.inner.state.lock().unwrap();
            st.shared_styles = css.into();
            self.shared_frame(&st)
        };
        self.send(&shared);
    }
    /// Restack a panel/shape: op is "front"/"back"/"forward"/"backward".
    pub fn order(&self, id: &str, op: &str) {
        self.send(&json!({"type": "order", "id": id, "op": op}));
    }
    /// Bring a panel/shape to the top.
    pub fn to_front(&self, id: &str) { self.order(id, "front"); }
    /// Send a panel/shape to the bottom.
    pub fn to_back(&self, id: &str) { self.order(id, "back"); }
    /// Send a free-form ink diff (`{added,updated,removed}` of tldraw records) —
    /// hub-native annotation relayed to every viewer.
    pub fn draw(&self, diff: Value) {
        self.send(&json!({"type": "draw", "diff": diff}));
    }
    /// React to free-form drawing (viewers drawing/moving/erasing): `f(draw_frame)`.
    pub fn on_draw<F: Fn(&Value) + Send + Sync + 'static>(&self, f: F) {
        self.inner.state.lock().unwrap().on_draw.push(std::sync::Arc::new(f));
    }
    /// Ask a connected browser for the free-form document (canvas.save's half):
    /// `cb(data)` fires when a browser replies. Needs an open tab.
    pub fn get_snapshot<F: Fn(&Value) + Send + Sync + 'static>(&self, cb: F) {
        let req = self.next_req();
        self.inner.state.lock().unwrap()
            .snapshot_cbs.insert(req.clone(), std::sync::Arc::new(cb));
        self.send(&json!({"type": "get_snapshot", "reqId": req, "panelIds": []}));
    }
    /// Ask a connected browser to render `shape_ids` (empty = whole page) to a
    /// PNG; `cb(reply)` fires with `{data: <base64>}` or `{error}`.
    pub fn get_image<F: Fn(&Value) + Send + Sync + 'static>(
        &self, shape_ids: Vec<String>, cb: F) {
        let req = self.next_req();
        self.inner.state.lock().unwrap()
            .snapshot_cbs.insert(req.clone(), std::sync::Arc::new(cb));
        self.send(&json!({"type": "get_image", "reqId": req, "shapeIds": shape_ids}));
    }

    fn next_req(&self) -> String {
        let n = self.inner.seq.fetch_add(1, Ordering::SeqCst);
        format!("rs{n}")
    }
    fn shared_frame(&self, st: &State) -> Value {
        json!({"type": "shared", "components": st.shared_components,
               "styles": st.shared_styles})
    }

    // -- binary media (the binary envelope) ----------------------------------
    /// Send a media envelope of `code` for panel `id` (see PROTOCOL.md codes).
    pub fn send_media(&self, code: u8, id: &str, data: &[u8]) {
        let _ = self.inner.tx.send(Out::Binary(bin_frame(code, id, data)));
    }
    /// Stream one JPEG frame to a [`video`](Self::video) panel (code VIDEO=1).
    pub fn send_video(&self, id: &str, jpeg: &[u8]) {
        self.send_media(1, id, jpeg);
    }
    /// Stream int16-LE PCM to an [`audio`](Self::audio) panel (code AUDIO=2).
    pub fn send_audio(&self, id: &str, pcm: &[u8]) {
        self.send_media(2, id, pcm);
    }
    /// Push opaque bytes to a React panel this source authored (code REACT=4)
    /// — the zero-copy `canvas.onFrame` stream in the panel's JSX.
    pub fn push_binary(&self, id: &str, data: &[u8]) {
        self.send_media(4, id, data);
    }

    // -- managed shapes & arrows ---------------------------------------------
    /// A managed canvas shape: `shape(id,"geo").at(x,y).size(w,h).prop(..).show()`.
    /// Shape types: geo, text, note, draw, line, highlight, frame.
    pub fn shape(&self, id: &str, shape_type: &str) -> ShapeBuilder<'_> {
        ShapeBuilder {
            client: self, id: id.into(), shape_type: shape_type.into(),
            x: 0.0, y: 0.0, rotation: 0.0, opacity: 1.0, props: Map::new(),
        }
    }
    /// A geometric shape (rectangle/ellipse/diamond/star/…): sugar for
    /// `shape(id,"geo").prop("geo", kind)`.
    pub fn geo(&self, id: &str, kind: &str) -> ShapeBuilder<'_> {
        self.shape(id, "geo").prop("geo", json!(kind))
    }
    /// An arrow bound between two endpoints (panels or shapes, by id).
    pub fn arrow(&self, id: &str, start: &str, end: &str) -> ArrowBuilder<'_> {
        ArrowBuilder {
            client: self, id: id.into(), start: start.into(), end: end.into(),
            props: Map::new(),
        }
    }
    /// Edit a managed shape live. Keys x/y/rotation/opacity move it; all others
    /// merge into its props (the `shape_update` frame; folded for replay).
    pub fn update_shape<I, K>(&self, id: &str, fields: I)
    where I: IntoIterator<Item = (K, Value)>, K: Into<String> {
        let mut top = Map::new();
        let mut props = Map::new();
        for (k, v) in fields {
            let k = k.into();
            if matches!(k.as_str(), "x" | "y" | "rotation" | "opacity") {
                top.insert(k, v);
            } else {
                props.insert(k, v);
            }
        }
        let mut msg = json!({"type": "shape_update", "id": id});
        {
            let o = msg.as_object_mut().unwrap();
            for (k, v) in &top { o.insert(k.clone(), v.clone()); }
            if !props.is_empty() {
                o.insert("props".into(), Value::Object(props.clone()));
            }
        }
        // Fold into the stored shape so a reconnect replays current state.
        {
            let mut st = self.inner.state.lock().unwrap();
            if let Some(shape) = st.shapes.get_mut(id).and_then(|s| s.as_object_mut()) {
                for (k, v) in &top { shape.insert(k.clone(), v.clone()); }
                if let Some(sp) = shape.get_mut("props").and_then(Value::as_object_mut) {
                    for (k, v) in &props { sp.insert(k.clone(), v.clone()); }
                }
            }
        }
        self.send(&msg);
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

    fn record_shape(&self, id: &str, msg: &Value) {
        let mut st = self.inner.state.lock().unwrap();
        if !st.shapes.contains_key(id) { st.shape_order.push(id.into()); }
        st.shapes.insert(id.into(), msg.clone());
    }

    fn record_arrow(&self, id: &str, msg: &Value) {
        let mut st = self.inner.state.lock().unwrap();
        if !st.arrows.contains_key(id) { st.arrow_order.push(id.into()); }
        st.arrows.insert(id.into(), msg.clone());
    }

    fn send(&self, msg: &Value) {
        let _ = self.inner.tx.send(Out::Text(msg.to_string()));
    }

    /// Route one inbound frame off the ordered dispatch thread.
    fn handle_inbound(&self, inbound: Inbound) {
        match inbound {
            Inbound::Json(v) => self.handle(&v),
            Inbound::Bin { code, id, data } => self.handle_binary(code, &id, &data),
        }
    }

    /// A binary media envelope from the hub: opaque input a browser sent to one
    /// of this source's panels (camera/mic/sendBinary) → its `on_binary`.
    fn handle_binary(&self, _code: u8, id: &str, data: &[u8]) {
        let hs: Vec<BinHandler> = self.inner.state.lock().unwrap()
            .on_binary.get(id).cloned().unwrap_or_default();
        for h in &hs { h(data); }
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
            "request" => {
                // A browser's canvas.request(data) for one of our panels: run
                // the handler and reply by reqId (the hub routes it to the asker).
                let h = self.inner.state.lock().unwrap().on_request.get(&id).cloned();
                if let Some(h) = h {
                    let data = msg.get("data").cloned().unwrap_or(Value::Null);
                    let req_id = msg.get("reqId").cloned().unwrap_or(Value::Null);
                    let result = h(&data);
                    self.send(&json!({"type": "response", "reqId": req_id,
                                      "result": result}));
                }
            }
            "presence" => {
                let vs = msg.get("viewers").and_then(Value::as_array)
                    .cloned().unwrap_or_default();
                let hs: Vec<Handler> = {
                    let mut st = self.inner.state.lock().unwrap();
                    st.viewers = vs;
                    st.on_presence.clone()
                };
                for h in &hs { h(msg); }
            }
            "cursor" | "cursor_gone" => {
                let hs: Vec<Handler> =
                    self.inner.state.lock().unwrap().on_cursor.clone();
                for h in &hs { h(msg); }
            }
            "chat" => {
                let hs: Vec<Handler> =
                    self.inner.state.lock().unwrap().on_chat.clone();
                for h in &hs { h(msg); }
            }
            "draw" => {
                let hs: Vec<Handler> =
                    self.inner.state.lock().unwrap().on_draw.clone();
                for h in &hs { h(msg); }
            }
            "snapshot" | "image" => {
                // A browser's reply to our get_snapshot/get_image: fire the
                // callback registered under this reqId (once).
                let req = msg.get("reqId").and_then(Value::as_str).unwrap_or("");
                let cb = self.inner.state.lock().unwrap().snapshot_cbs.remove(req);
                if let Some(cb) = cb { cb(msg); }
            }
            _ => {}
        }
    }

    /// The frames that reconstruct this source on a (re)connect — registers,
    /// then accumulated state, then subscriptions.
    fn replay_frames(&self) -> Vec<String> {
        let st = self.inner.state.lock().unwrap();
        let mut out = Vec::new();
        // Shared assets first, so React panels mount with their components/styles.
        if !st.shared_components.is_empty() || !st.shared_styles.is_empty() {
            out.push(self.shared_frame(&st).to_string());
        }
        for id in &st.reg_order {
            if let Some(reg) = st.registers.get(id) { out.push(reg.to_string()); }
        }
        for (id, payload) in &st.updates {
            out.push(json!({"type": "update", "id": id, "payload": payload}).to_string());
        }
        for id in &st.shape_order {
            if let Some(s) = st.shapes.get(id) { out.push(s.to_string()); }
        }
        for id in &st.arrow_order {
            if let Some(a) = st.arrows.get(id) { out.push(a.to_string()); }
        }
        if !st.view.is_empty() {
            out.push(json!({"type": "view", "view": st.view}).to_string());
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
                                    let _ = inner.dispatch.send(Inbound::Json(v));
                                }
                            }
                            Some(Ok(Message::Binary(b))) => {
                                if let Some((code, id, data)) = parse_bin(&b) {
                                    let _ = inner.dispatch.send(
                                        Inbound::Bin { code, id, data });
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
