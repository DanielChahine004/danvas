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
//!
//! # Coverage
//!
//! The SDK spans the whole wire protocol (v1), so a Rust peer does everything a
//! browser or a Python process can:
//!
//! - **panels** — native built-ins from the shared template asset
//!   ([`slider`](Client::slider)/[`label`](Client::label)/[`button`](Client::button)/
//!   `toggle`/`text_field`/`markdown`/[`video`](Client::video)/[`audio`](Client::audio)),
//!   [`register`](Client::register) for anything else, [`update`](Client::update)/
//!   [`remove`](Client::remove), and [`on_input`](Client::on_input)/[`on_layout`](Client::on_layout).
//! - **media** — [`send_video`](Client::send_video)/[`send_audio`](Client::send_audio)/
//!   [`push_binary`](Client::push_binary) out, [`on_binary`](Client::on_binary) in.
//! - **file transfer** — [`serve_bytes`](Client::serve_bytes)/[`on_download`](Client::on_download)
//!   answer a download panel's click; [`upload_endpoint`](Client::upload_endpoint)/
//!   [`on_upload`](Client::on_upload) receive a browser's files (the FILE
//!   envelope + `file_pull`/`file_push` dance of PROTOCOL.md).
//! - **self-contained serving** — [`serve`] spawns `danvasd`, dials in, and
//!   opens the browser, so a Rust program owns its canvas end to end
//!   (Python's `canvas.serve()` shape); [`Broker`] is the running-daemon handle.
//! - **component logic** — [`live_plot_feed`](Client::live_plot_feed) (rolling
//!   buffer + extend deltas), [`histogram_feed`](Client::histogram_feed)
//!   (fixed-bin density heatmap), [`file_browser`](Client::file_browser)
//!   (sandboxed navigation), [`data_url`] (image bytes → panel `src`) — the
//!   owner-side logic the Python components implement, transliterated.
//! - **shapes & arrows** — [`shape`](Client::shape)/[`geo`](Client::geo)/
//!   [`arrow`](Client::arrow)/[`update_shape`](Client::update_shape).
//! - **interaction & multiuser** — [`on_request`](Client::on_request),
//!   [`viewers`](Client::viewers)/[`on_presence`](Client::on_presence)/
//!   [`on_cursor`](Client::on_cursor), [`chat`](Client::chat)/[`on_chat`](Client::on_chat).
//! - **canvas state** — [`set_view`](Client::set_view),
//!   [`define`](Client::define)/[`style`](Client::style),
//!   [`order`](Client::order), [`draw`](Client::draw)/[`on_draw`](Client::on_draw),
//!   [`get_snapshot`](Client::get_snapshot)/[`get_image`](Client::get_image).
//! - **the shared plane** — [`set_props`](Client::set_props)/[`subscribe`](Client::subscribe)/
//!   [`find`](Client::find) to edit and observe *anyone's* panels.
//!
//! See `examples/` (media, shapes, interact, canvas_state, two_languages).

mod helpers;
mod serve;

pub use helpers::{data_url, Histogram, LivePlot};
pub use serve::{serve, Broker};

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
/// An upload receiver: fired once per file a browser sends to the endpoint.
type UploadHandler = std::sync::Arc<dyn Fn(&UploadedFile) + Send + Sync + 'static>;

/// One file a browser uploaded to an endpoint this source registered
/// ([`upload_endpoint`](Client::upload_endpoint) / [`on_upload`](Client::on_upload)).
/// `content_type` is browser-reported (advisory — don't trust it for security).
#[derive(Clone, Debug)]
pub struct UploadedFile {
    pub name: String,
    pub size: usize,
    pub content_type: String,
    pub data: Vec<u8>,
}

/// Download tokens live this long (matching the Python bridge's TTL): long
/// enough for a HEAD+GET or a retry, short enough that a leaked URL dies.
const DOWNLOAD_TTL: std::time::Duration = std::time::Duration::from_secs(300);

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
    // File transfer (PROTOCOL.md §file transfer). Downloads: token ->
    // (filename, bytes, expiry) served on the hub's file_pull; not single-use
    // (a HEAD+GET or a retry both work), purged by TTL. Uploads: stable
    // endpoint token -> receiver. pending_pushes holds a file_push's meta
    // until its FILE envelope lands (same socket, so ordering is guaranteed).
    downloads: HashMap<String, (String, Vec<u8>, std::time::Instant)>,
    uploads: HashMap<String, UploadHandler>,
    pending_pushes: HashMap<String, Value>,
    // Additive hub capabilities from the welcome frame ("rel": the frontend
    // resolves and cascades relative placement itself -- the SDK-side
    // fallbacks stay quiet when it's advertised).
    hub_features: Vec<String>,
    // Layout-cascade edges (Python's _below_deps/_right_of_deps): anchor id ->
    // panels placed below/right_of it. When the anchor's height settles
    // differently in the browser (auto-height content fitting), every
    // dependent shifts down by the delta, recursively — so a below() chain
    // stays a chain instead of overlapping.
    y_deps: HashMap<String, Vec<String>>,
    // Debounced cascade shifts: anchor id -> (accumulated dh, generation).
    // A drag-resize streams a layout report per frame; shifting the whole
    // chain on each one makes every dependent stutter down the canvas. The
    // deltas accumulate here and apply in ONE shift once the resize has been
    // quiet for a beat (a stale generation means a newer report superseded us).
    casc_pending: HashMap<String, (f64, u64)>,
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
    started: std::time::Instant,       // for the inspector's uptime readouts
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

/// An unguessable URL-safe token (24 random bytes, base64url) — the same
/// entropy as the Python bridge's `secrets.token_urlsafe(24)`.
fn mint_token() -> String {
    const ALPHABET: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut raw = [0u8; 24];
    getrandom::getrandom(&mut raw).expect("OS randomness");
    let mut out = String::with_capacity(32);
    for chunk in raw.chunks(3) {
        let n = (chunk[0] as u32) << 16 | (chunk[1] as u32) << 8 | chunk[2] as u32;
        for shift in [18, 12, 6, 0] {
            out.push(ALPHABET[(n >> shift) as usize & 63] as char);
        }
    }
    out
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
    kind: String,
    data: Map<String, Value>,
    props: Map<String, Value>,   // raw prop overrides (label, html, …)
    place: Map<String, Value>,
    rel: Option<(&'static str, String, f64)>,  // (below/above/…, anchor, gap)
    frame_color: Option<String>, // top-level frameColor (accent border/theme)
}

impl<'a> PanelBuilder<'a> {
    /// Override a data field (min/max/value/text/options/...).
    pub fn set(mut self, key: &str, value: Value) -> Self {
        // Debug builds check the key against the template's contract (the
        // machine-readable statement of the panel's authorable fields —
        // PROTOCOL.md § component contracts): a typo'd field would otherwise
        // vanish silently into the data blob.
        #[cfg(debug_assertions)]
        {
            let declared = &self.client.inner.templates["templates"]
                [self.kind.as_str()]["contract"]["data"];
            if let Some(fields) = declared.as_object() {
                if !fields.contains_key(key) {
                    eprintln!(
                        "[danvas] warning: `{key}` is not a declared data \
                         field of the `{}` template — see its contract in \
                         components.json (typo, or update the CONTRACT)",
                        self.kind);
                }
            }
        }
        self.data.insert(key.into(), value);
        self
    }
    /// Override a raw register prop (e.g. a Custom panel's `html`).
    pub fn prop(mut self, key: &str, value: Value) -> Self {
        self.props.insert(key.into(), value);
        self
    }
    /// Set the card header (defaults to the panel id).
    pub fn titled(self, label: &str) -> Self {
        self.prop("label", json!(label))
    }
    /// Per-panel accent colour ('#rrggbb') — the twin of Python's `color=`.
    /// Sets the register frame's top-level `frameColor`; the frontend derives
    /// the `_th` CSS-variable palette from it (theme.ts), so the color math
    /// lives in the shared renderer, not in every SDK.
    pub fn color(mut self, hex: &str) -> Self {
        self.frame_color = Some(hex.to_string());
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
    /// Place this panel below an anchor (its own or a peer's id), `gap` px under
    /// it and left-aligned — the twin of Python's `below=`. Resolved once the
    /// anchor's position is known (deferred to the browser's report if needed),
    /// so panels can be declared anchor-first in natural order. Default gap 16.
    pub fn below(mut self, anchor: &str) -> Self {
        self.rel = Some(("below", anchor.into(), 16.0));
        self
    }
    /// Place this panel above an anchor (left-aligned, `gap` px over it).
    pub fn above(mut self, anchor: &str) -> Self {
        self.rel = Some(("above", anchor.into(), 16.0));
        self
    }
    /// Place this panel to the right of an anchor (top-aligned, `gap` px beside).
    pub fn right_of(mut self, anchor: &str) -> Self {
        self.rel = Some(("right_of", anchor.into(), 16.0));
        self
    }
    /// Place this panel to the left of an anchor (top-aligned, `gap` px beside).
    pub fn left_of(mut self, anchor: &str) -> Self {
        self.rel = Some(("left_of", anchor.into(), 16.0));
        self
    }
    /// Override the relative-placement gap (default 16 px).
    pub fn gap(mut self, g: f64) -> Self {
        if let Some(r) = self.rel.as_mut() { r.2 = g; }
        self
    }
    /// Register it on the canvas.
    pub fn show(mut self) {
        let has_xy = self.place.contains_key("x") && self.place.contains_key("y");
        let new_w = self.place.get("w").and_then(Value::as_f64);
        let new_h = self.place.get("h").and_then(Value::as_f64);
        let rel = self.rel.clone();
        let id = self.id.clone();
        let client = self.client.clone();
        // Relative placement is resolved BEFORE registering when the anchor's
        // geometry is already known, so the register frame itself carries x/y —
        // a positioned register never enters the browser's masonry flow, which
        // would otherwise race (and win over) a position sent as a follow-up
        // update. Python resolves `below=` at insert time for the same reason.
        // An unpositioned anchor defers to its first layout report instead.
        let mut deferred = None;
        if let (Some((kind, anchor, gap)), false) = (rel.clone(), has_xy) {
            client.record_y_dep(kind, &anchor, &id);
            match client.resolve_relative(kind, &anchor, gap, new_w, new_h) {
                Some((x, y)) => {
                    self.place.insert("x".into(), json!(x));
                    self.place.insert("y".into(), json!(y));
                }
                None => deferred = Some((kind, anchor, gap)),
            }
        }
        // The register frame carries rel either way: on a rel-aware hub the
        // FRONTEND places (when x/y is absent) and re-settles the chain when
        // heights change; the local resolution above keeps chains
        // deterministic with no browser attached.
        self.client.register_template_raw(&id, &self.kind, self.data,
                                          self.props, self.place,
                                          self.frame_color, rel);
        if let Some((kind, anchor, gap)) = deferred {
            client.defer_relative(id, kind, anchor, gap, new_w, new_h);
        }
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
            started: std::time::Instant::now(),
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
    fn tpl_builder(&self, id: &str, kind: &str) -> PanelBuilder<'_> {
        let data = self.inner.templates["templates"][kind]["data"]
            .as_object().cloned().unwrap_or_default();
        PanelBuilder { client: self, id: id.into(), kind: kind.into(),
                       data, props: Map::new(), place: Map::new(), rel: None,
                       frame_color: None }
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
    /// Any templated built-in by kind — every panel in the shared asset
    /// (slider/label/button/toggle/text_field/markdown/video/audio/table/plot/
    /// histogram/live_plot/image/webview/custom/download/upload/file_browser/
    /// inspector/chat). Override fields with `.set(key, value)`.
    pub fn panel(&self, id: &str, kind: &str) -> PanelBuilder<'_> {
        self.tpl_builder(id, kind)
    }

    fn register_template_raw(&self, id: &str, kind: &str, mut data: Map<String, Value>,
                             overrides: Map<String, Value>, place: Map<String, Value>,
                             frame_color: Option<String>,
                             rel: Option<(&'static str, String, f64)>) {
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
        // Raw prop overrides win (a Custom panel's html, a .titled() card label).
        for (k, v) in overrides { props.insert(k, v); }
        let mut msg = json!({
            "type": "register", "id": id, "name": id,
            "component": tpl["component"], "props": props,
        });
        for k in ["x", "y"] {
            if let Some(v) = place.get(k) {
                msg.as_object_mut().unwrap().insert(k.into(), v.clone());
            }
        }
        // Top-level frameColor (accent the card frame tints with) — like the
        // Python component's color=; without it the card fills with the
        // translucent accent instead of a subtle framed tint.
        if let Some(fc) = frame_color {
            msg.as_object_mut().unwrap().insert("frameColor".into(), json!(fc));
        }
        // Relative placement rides the register frame (PROTOCOL.md): explicit
        // x/y (when the SDK resolved the chain locally) wins at the frontend,
        // but the rel still records the dependency edge that drives the
        // browser-side height-settle cascade.
        if let Some((kind_r, anchor, gap)) = rel {
            msg.as_object_mut().unwrap().insert("rel".into(),
                json!({"kind": kind_r, "anchor": anchor, "gap": gap}));
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

    /// Reposition a panel this source owns (x/y in canvas coords) — the wire
    /// form a browser drag relays, folded for replay.
    pub fn set_layout(&self, id: &str, x: f64, y: f64) {
        {
            let mut st = self.inner.state.lock().unwrap();
            let e = st.updates.entry(id.into()).or_default();
            e.insert("x".into(), json!(x));
            e.insert("y".into(), json!(y));
        }
        self.send(&json!({"type": "update", "id": id, "payload": {"x": x, "y": y}}));
    }

    /// A panel's geometry as this source knows it, merging (most recent first)
    /// accumulated updates (set_layout / folded browser reports), the register
    /// frame (top-level x/y, props w/h — template defaults included), and the
    /// hub mirror (peers' panels). x/y may be unknown; w/h default to 0.
    fn known_geom(&self, id: &str) -> (Option<f64>, Option<f64>, f64, f64) {
        let st = self.inner.state.lock().unwrap();
        let upd = st.updates.get(id);
        let reg = st.registers.get(id);
        let props = reg.and_then(|r| r.get("props")).and_then(Value::as_object);
        let mirror = st.panels.get(id);
        let get = |k: &str| {
            upd.and_then(|u| u.get(k)).and_then(Value::as_f64)
                .or_else(|| reg.and_then(|r| r.get(k)).and_then(Value::as_f64))
                .or_else(|| props.and_then(|p| p.get(k)).and_then(Value::as_f64))
                .or_else(|| mirror.and_then(|e| {
                    e.state.get(k).and_then(Value::as_f64)
                        .or_else(|| e.props.get(k).and_then(Value::as_f64))
                }))
        };
        (get("x"), get("y"), get("w").unwrap_or(0.0), get("h").unwrap_or(0.0))
    }

    /// Record the layout-cascade edge for a relative placement (mirrors Python
    /// registering _below_deps whether or not placement was deferred).
    fn record_y_dep(&self, kind: &str, anchor: &str, id: &str) {
        if matches!(kind, "below" | "right_of") {
            self.inner.state.lock().unwrap()
                .y_deps.entry(anchor.into()).or_default().push(id.into());
        }
    }

    /// Resolve a relative placement against what this source already knows.
    /// The anchor's geometry is looked up in local knowledge first (registers
    /// carry the template's default size, placements fold as they go) — so a
    /// whole `.below()` chain rooted at one positioned panel resolves
    /// synchronously, browser or not — then the hub mirror (a peer's panel).
    /// `None` when the anchor has no position anywhere yet.
    fn resolve_relative(&self, kind: &str, anchor: &str, gap: f64,
                        new_w: Option<f64>, new_h: Option<f64>)
        -> Option<(f64, f64)>
    {
        let (ax, ay, aw, ah) = self.known_geom(anchor);
        let (ax, ay) = (ax?, ay?);
        Some(match kind {
            "above" => (ax, ay - gap - new_h.unwrap_or(0.0)),
            "right_of" => (ax + aw + gap, ay),
            "left_of" => (ax - gap - new_w.unwrap_or(0.0), ay),
            _ => (ax, ay + ah + gap), // below
        })
    }

    /// Deferred relative placement: one-shot on the anchor's first positioned
    /// layout report (the panel joins the browser's masonry flow meanwhile,
    /// exactly like the Python `below=` with an unpositioned anchor).
    fn defer_relative(&self, id: String, kind: &'static str, anchor: String,
                      gap: f64, new_w: Option<f64>, new_h: Option<f64>) {
        let client = self.clone();
        let done = std::sync::Arc::new(AtomicBool::new(false));
        self.on_layout(&anchor.clone(), move |frame| {
            if done.load(Ordering::SeqCst) { return; }
            let g = |k: &str| frame.get(k).and_then(Value::as_f64);
            let (ax, ay) = match (g("x"), g("y")) {
                (Some(a), Some(b)) => (a, b),
                _ => return, // an h-only auto-height report: wait for x/y
            };
            done.store(true, Ordering::SeqCst);
            let (x, y) = match kind {
                "above" => (ax, ay - gap - new_h.unwrap_or(0.0)),
                "right_of" => (ax + g("w").unwrap_or(0.0) + gap, ay),
                "left_of" => (ax - gap - new_w.unwrap_or(0.0), ay),
                _ => (ax, ay + g("h").unwrap_or(0.0) + gap),
            };
            client.set_layout(&id, x, y);
        });
    }

    /// Send one wire payload while folding a *different* payload into the
    /// replay cache — for components whose wire form is a delta but whose
    /// replayable state is a whole snapshot (LivePlot's `plot_extend` on the
    /// wire vs the full `plot` figure a reconnecting client needs).
    pub(crate) fn update_split(&self, id: &str, wire: Value,
                               replay_key: &str, replay: Value) {
        {
            let mut st = self.inner.state.lock().unwrap();
            st.updates.entry(id.into()).or_default()
                .insert(replay_key.into(), replay);
        }
        self.send(&json!({"type": "update", "id": id, "payload": wire}));
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

    // -- file transfer (downloads/uploads through the hub) ---------------------
    /// Stash `data` under a fresh unguessable token and return the URL a
    /// browser downloads it from (`/__download__/<token>`, saved as
    /// `filename`). Valid ~5 minutes; when the hub asks (`file_pull`) this
    /// client streams the bytes up — the twin of Python's
    /// `canvas.serve_bytes`.
    pub fn serve_bytes(&self, filename: &str, data: Vec<u8>) -> String {
        let token = mint_token();
        let mut st = self.inner.state.lock().unwrap();
        let now = std::time::Instant::now();
        st.downloads.retain(|_, (_, _, exp)| *exp > now);
        st.downloads.insert(token.clone(),
                            (filename.into(), data, now + DOWNLOAD_TTL));
        format!("/__download__/{token}")
    }

    /// Answer a `download` panel's click: `f()` returns `(filename, bytes)`,
    /// resolved fresh per click; the reply carries the minted one-off URL the
    /// panel then fetches. The move that makes `panel(id, "download")` real:
    ///
    /// ```no_run
    /// # use serde_json::json;
    /// # let c = danvas_source::Client::connect("127.0.0.1:8000", "x").unwrap();
    /// c.panel("dl", "download").set("text", json!("Export")).show();
    /// c.on_download("dl", || ("data.csv".into(), b"a,b\n1,2\n".to_vec()));
    /// ```
    pub fn on_download<F>(&self, id: &str, f: F)
    where F: Fn() -> (String, Vec<u8>) + Send + Sync + 'static {
        let client = self.clone();
        self.on_request(id, move |_req| {
            let (filename, data) = f();
            let url = client.serve_bytes(&filename, data);
            json!({"url": url, "filename": filename})
        });
    }

    /// Register a file-receiving endpoint: returns the URL a browser POSTs to
    /// (`/__upload__/<token>`, stable for the client's lifetime); `f` fires
    /// with each [`UploadedFile`]. The twin of Python's
    /// `canvas.receive_files` — point an `upload` panel's `url` data field at
    /// it, or use [`on_upload`](Self::on_upload) which wires both.
    pub fn upload_endpoint<F>(&self, f: F) -> String
    where F: Fn(&UploadedFile) + Send + Sync + 'static {
        let token = mint_token();
        self.inner.state.lock().unwrap()
            .uploads.insert(token.clone(), std::sync::Arc::new(f));
        format!("/__upload__/{token}")
    }

    /// Make an already-registered `upload` panel deliver its files to `f`:
    /// mints an endpoint and patches it into the panel's `url` data field
    /// (folded for replay). Call after the panel's `.show()`:
    ///
    /// ```no_run
    /// # use serde_json::json;
    /// # let c = danvas_source::Client::connect("127.0.0.1:8000", "x").unwrap();
    /// c.panel("up", "upload").set("text", json!("Drop a file")).show();
    /// c.on_upload("up", |file| println!("{} ({} bytes)", file.name, file.size));
    /// ```
    pub fn on_upload<F>(&self, id: &str, f: F)
    where F: Fn(&UploadedFile) + Send + Sync + 'static {
        let url = self.upload_endpoint(f);
        self.update(id, "data_patch", json!({"url": url}));
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
    /// Build the row set an Inspector panel renders: one object per panel this
    /// source has registered, in declaration order, with the columns danvas's
    /// inspector expects (`name/label/type/value/visible/x/y/w/h`). Push it
    /// with `update(ins_id, "data_patch", client.inspector_rows())` — the same
    /// `data_patch` the Python Inspector sends on refresh. Prefer
    /// [`inspector`](Self::inspector), which wires the whole panel (views,
    /// drill-down, trace) and refreshes itself.
    pub fn inspector_rows(&self) -> Value {
        let st = self.inner.state.lock().unwrap();
        let mut rows = Vec::new();
        for id in &st.reg_order {
            let reg = match st.registers.get(id) { Some(r) => r, None => continue };
            let props = reg.get("props").and_then(Value::as_object);
            let getp = |k: &str| props.and_then(|p| p.get(k)).cloned();
            let label = getp("label").and_then(|v| v.as_str().map(str::to_string))
                .unwrap_or_else(|| id.clone());
            let component = reg.get("component").and_then(Value::as_str)
                .unwrap_or("React").to_string();
            let upd = st.updates.get(id);
            let getu = |k: &str| upd.and_then(|u| u.get(k)).cloned();
            let x = reg.get("x").cloned().or_else(|| getu("x")).unwrap_or(Value::Null);
            let y = reg.get("y").cloned().or_else(|| getu("y")).unwrap_or(Value::Null);
            // Current value: whatever the panel last streamed (post/value/text).
            let value = getu("post").or_else(|| getu("value")).or_else(|| getu("text"));
            let value_str = match value {
                None | Some(Value::Null) => "None".to_string(),
                Some(Value::String(s)) => format!("'{s}'"),
                Some(v) => v.to_string(),
            };
            rows.push(json!({
                "key": id, "name": id, "label": label, "type": component,
                "value": value_str, "visible": true,
                "x": x, "y": y, "w": getp("w").unwrap_or(Value::Null),
                "h": getp("h").unwrap_or(Value::Null), "locked": false,
            }));
        }
        // Plain JSON: the inspector template parses rows tolerantly (string or
        // array), so no double-encoding is needed.
        json!({ "rows": rows })
    }

    /// Seconds since this client connected (inspector uptime readouts).
    pub(crate) fn uptime_secs(&self) -> f64 {
        self.inner.started.elapsed().as_secs_f64()
    }

    /// The inspector's "canvas" view for a Rust source: this instance's weight
    /// — what it contributes and what it can see — as name/type/value rows
    /// (the Rust analogue of Python's RSS/panel-count view).
    pub(crate) fn inspector_canvas_rows(&self) -> Vec<Value> {
        let st = self.inner.state.lock().unwrap();
        let replay_bytes: usize = st.registers.values()
            .map(|v| v.to_string().len())
            .chain(st.updates.values().map(|u| Value::Object(u.clone()).to_string().len()))
            .sum();
        let row = |name: &str, t: &str, value: String| {
            json!({"key": name, "name": name, "type": t, "value": value})
        };
        vec![
            row("source label", "str", self.inner.label.clone()),
            row("connected", "bool", self.is_connected().to_string()),
            row("uptime", "str", format!("{:.0} s", self.uptime_secs())),
            row("panels (this source)", "int", st.registers.len().to_string()),
            row("panels (canvas)", "int", st.panels.len().to_string()),
            row("shapes", "int", st.shapes.len().to_string()),
            row("arrows", "int", st.arrows.len().to_string()),
            row("subscriptions", "int", st.subs.len().to_string()),
            row("viewers", "int", st.viewers.len().to_string()),
            row("replay cache", "str", format!("{:.1} KB", replay_bytes as f64 / 1024.0)),
        ]
    }

    /// The inspector's "system" view for a Rust source: what the standard
    /// library can tell about the host (Python's psutil-backed CPU/RAM/GPU
    /// telemetry has no dependency-free Rust equivalent — say so in-table
    /// rather than showing an empty view).
    pub(crate) fn inspector_system_rows(&self) -> Vec<Value> {
        let row = |name: &str, t: &str, value: String| {
            json!({"key": name, "name": name, "type": t, "value": value})
        };
        vec![
            row("os", "str", std::env::consts::OS.into()),
            row("arch", "str", std::env::consts::ARCH.into()),
            row("pid", "int", std::process::id().to_string()),
            row("cpus", "int", std::thread::available_parallelism()
                .map(|n| n.get().to_string()).unwrap_or_else(|_| "?".into())),
            row("process uptime", "str", format!("{:.0} s", self.uptime_secs())),
            row("host telemetry", "str",
                "CPU/RAM/GPU readouts need a Python source (psutil)".into()),
        ]
    }

    /// Drill-down detail for a panel this source registered: its register
    /// frame as `repr`, its props + accumulated state as the field table.
    pub(crate) fn inspector_detail(&self, key: &str) -> Option<Value> {
        let st = self.inner.state.lock().unwrap();
        let reg = st.registers.get(key)?;
        let vtype = |v: &Value| match v {
            Value::Null => "null", Value::Bool(_) => "bool",
            Value::Number(_) => "number", Value::String(_) => "str",
            Value::Array(_) => "list", Value::Object(_) => "dict",
        };
        let clip = |s: String| if s.len() > 120 {
            format!("{}…", s.chars().take(119).collect::<String>())
        } else { s };
        let mut fields = Vec::new();
        if let Some(props) = reg.get("props").and_then(Value::as_object) {
            for (k, v) in props {
                fields.push(json!({"field": format!("props.{k}"),
                                   "type": vtype(v), "value": clip(v.to_string())}));
            }
        }
        if let Some(upd) = st.updates.get(key) {
            for (k, v) in upd {
                fields.push(json!({"field": format!("state.{k}"),
                                   "type": vtype(v), "value": clip(v.to_string())}));
            }
        }
        let component = reg.get("component").and_then(Value::as_str).unwrap_or("React");
        let mut repr = reg.to_string();
        if repr.len() > 300 {
            repr = format!("{}…", repr.chars().take(299).collect::<String>());
        }
        Some(json!({"key": key, "type": component, "repr": repr,
                    "fields": fields}))
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

    /// A binary media envelope from the hub: FILE (code 6) completes a pending
    /// upload push (the envelope id is its reqId); everything else is opaque
    /// input a browser sent to one of this source's panels
    /// (camera/mic/sendBinary) → its `on_binary`.
    fn handle_binary(&self, code: u8, id: &str, data: &[u8]) {
        if code == 6 {
            self.handle_file_bytes(id, data);
            return;
        }
        let hs: Vec<BinHandler> = self.inner.state.lock().unwrap()
            .on_binary.get(id).cloned().unwrap_or_default();
        for h in &hs { h(data); }
    }

    /// The FILE bytes of a `file_push` broadcast: deliver to the endpoint that
    /// owns the token and ack, or decline (`ok: false`) — every source answers
    /// every push, so the hub's HTTP reply never has to wait out its timeout.
    fn handle_file_bytes(&self, req: &str, data: &[u8]) {
        let push = self.inner.state.lock().unwrap().pending_pushes.remove(req);
        let Some(push) = push else { return };
        let token = push.get("token").and_then(Value::as_str).unwrap_or("");
        let handler = self.inner.state.lock().unwrap().uploads.get(token).cloned();
        let Some(handler) = handler else {
            self.send(&json!({"type": "file_ack", "reqId": req, "ok": false}));
            return;
        };
        // The hub basenames the filename already; strip separators again anyway.
        let name = push.get("name").and_then(Value::as_str)
            .and_then(|n| n.rsplit(['/', '\\']).next())
            .filter(|n| !n.is_empty())
            .unwrap_or("upload.bin")
            .to_string();
        let file = UploadedFile {
            name: name.clone(),
            size: data.len(),
            content_type: push.get("content_type").and_then(Value::as_str)
                .unwrap_or("application/octet-stream").into(),
            data: data.to_vec(),
        };
        // A panicking receiver must not swallow the ack (the browser's POST
        // would hang out its 15 s) — deliver, then ack regardless.
        let _ = std::panic::catch_unwind(
            std::panic::AssertUnwindSafe(|| handler(&file)));
        self.send(&json!({"type": "file_ack", "reqId": req, "ok": true,
                          "name": name, "size": data.len()}));
    }

    /// Route one inbound frame: taps see all; input/layout hit their handlers;
    /// register/update/remove fold the local mirror.
    fn handle(&self, msg: &Value) {
        let taps: Vec<Handler> = self.inner.state.lock().unwrap().frame_taps.clone();
        for tap in &taps { tap(msg); }
        let kind = msg.get("type").and_then(Value::as_str).unwrap_or("");
        let id = msg.get("id").and_then(Value::as_str).unwrap_or("").to_string();
        match kind {
            "welcome" => {
                let feats = msg.get("features").and_then(Value::as_array)
                    .map(|a| a.iter().filter_map(Value::as_str)
                        .map(String::from).collect())
                    .unwrap_or_default();
                self.inner.state.lock().unwrap().hub_features = feats;
            }
            "input" => {
                let payload = msg.get("payload").cloned().unwrap_or(Value::Null);
                // Clone the handler Arcs OUT of the lock, then call them
                // unlocked — a handler may re-enter (update/set_props/subscribe).
                let hs: Vec<Handler> = self.inner.state.lock().unwrap()
                    .on_input.get(&id).cloned().unwrap_or_default();
                for h in &hs { h(&payload); }
            }
            "layout" => {
                // Fold a browser's move/resize of one of OUR panels into the
                // accumulated updates, so hand-arranged layouts survive a
                // reconnect (Python's _store_base_layout), and cascade a
                // height settling (auto-height content fitting) through the
                // below/right_of chain (Python's _cascade_height) — debounced,
                // so a drag-resize shifts the chain once at the end, not per
                // pointer-move frame.
                let mut debounce_gen = None;
                {
                    let mut st = self.inner.state.lock().unwrap();
                    if st.registers.contains_key(&id) {
                        let old_h = st.updates.get(&id)
                            .and_then(|u| u.get("h")).and_then(Value::as_f64)
                            .or_else(|| st.registers.get(&id)
                                .and_then(|r| r.get("props"))
                                .and_then(|p| p.get("h"))
                                .and_then(Value::as_f64));
                        let new_h = msg.get("h").and_then(Value::as_f64);
                        let e = st.updates.entry(id.clone()).or_default();
                        for k in ["x", "y", "w", "h", "rotation"] {
                            if let Some(v) = msg.get(k).filter(|v| v.is_number()) {
                                e.insert(k.into(), v.clone());
                            }
                        }
                        let hub_rel = st.hub_features.iter().any(|f| f == "rel");
                        if let (Some(old), Some(new)) = (old_h, new_h) {
                            let dh = new - old;
                            // The frontend owns the cascade on rel-aware hubs.
                            if dh.abs() > 0.5 && !hub_rel {
                                let entry = st.casc_pending
                                    .entry(id.clone()).or_insert((0.0, 0));
                                entry.0 += dh;
                                entry.1 += 1;
                                debounce_gen = Some(entry.1);
                            }
                        }
                    }
                }
                if let Some(gen) = debounce_gen {
                    let client = self.clone();
                    let anchor = id.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(
                            std::time::Duration::from_millis(180));
                        let mut shifts: Vec<(String, f64)> = Vec::new();
                        {
                            let mut st = client.inner.state.lock().unwrap();
                            match st.casc_pending.get(&anchor) {
                                // Still the latest report? Apply the whole
                                // accumulated shift in one go.
                                Some(&(dh, g)) if g == gen => {
                                    st.casc_pending.remove(&anchor);
                                    collect_y_shifts(&mut st, &anchor, dh,
                                                     &mut shifts);
                                }
                                _ => return, // superseded by a newer report
                            }
                        }
                        for (dep, new_y) in shifts {
                            client.send(&json!({"type": "update", "id": dep,
                                                "payload": {"y": new_y}}));
                        }
                    });
                }
                let hs: Vec<Handler> = self.inner.state.lock().unwrap()
                    .on_layout.get(&id).cloned().unwrap_or_default();
                for h in &hs { h(msg); }
            }
            "register" => {
                let mut st = self.inner.state.lock().unwrap();
                // Fold the frame's top-level geometry into the entry's state so
                // known_geom / find-and-place work on peers' panels (source.py
                // keeps x/y/w/h the same way; a hub folds layout into the
                // register it replays).
                let mut state = Map::new();
                for k in ["x", "y", "w", "h"] {
                    if let Some(v) = msg.get(k) {
                        state.insert(k.into(), v.clone());
                    }
                }
                let entry = PanelEntry {
                    component: msg.get("component").and_then(Value::as_str)
                        .unwrap_or("").into(),
                    name: msg.get("name").and_then(Value::as_str).map(String::from),
                    owner: msg.get("owner").and_then(Value::as_str).map(String::from),
                    props: msg.get("props").and_then(Value::as_object).cloned()
                        .unwrap_or_default(),
                    state,
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
            "set_props" => {
                // The shared property plane, hub-routed: apply another peer's
                // write on OUR panel and echo the result -- the owner's echoed
                // state is canonical (PROTOCOL.md). A thin SDK has no typed
                // setters, so "apply" is: placement keys fold as a layout
                // would; everything else merges into the panel's data blob
                // (register + replay stay canonical) and echoes as a
                // data_patch so every browser converges.
                let Some(props) = msg.get("props").and_then(Value::as_object)
                    .cloned() else { return };
                let mut echo = Map::new();
                let mut data_patch = Map::new();
                {
                    let mut st = self.inner.state.lock().unwrap();
                    if !st.registers.contains_key(&id) {
                        return; // not ours (or a shape) -- nothing to apply
                    }
                    for (k, v) in props {
                        if matches!(k.as_str(),
                                    "x" | "y" | "w" | "h" | "rotation"
                                    | "opacity") {
                            st.updates.entry(id.clone()).or_default()
                                .insert(k.clone(), v.clone());
                            echo.insert(k, v);
                        } else {
                            data_patch.insert(k, v);
                        }
                    }
                    if !data_patch.is_empty() {
                        // Fold into the cached register's data blob so this
                        // source's own replay carries the new values.
                        if let Some(props_obj) = st.registers.get_mut(&id)
                            .and_then(|r| r.get_mut("props"))
                            .and_then(Value::as_object_mut)
                        {
                            let mut blob: Map<String, Value> = props_obj
                                .get("data").and_then(Value::as_str)
                                .and_then(|s| serde_json::from_str(s).ok())
                                .unwrap_or_default();
                            for (k, v) in &data_patch {
                                blob.insert(k.clone(), v.clone());
                            }
                            props_obj.insert("data".into(), json!(
                                serde_json::to_string(&blob).unwrap()));
                        }
                        echo.insert("data_patch".into(),
                                    Value::Object(data_patch));
                    }
                }
                if !echo.is_empty() {
                    self.send(&json!({"type": "update", "id": id,
                                      "payload": echo}));
                }
            }
            "file_pull" => {
                // The hub asks for a download token's bytes on a browser's
                // behalf (broadcast — tokens are opaque): stream file_meta +
                // a FILE envelope if the token is ours, else decline.
                let req = msg.get("reqId").and_then(Value::as_str)
                    .unwrap_or("").to_string();
                let token = msg.get("token").and_then(Value::as_str).unwrap_or("");
                let item = {
                    let mut st = self.inner.state.lock().unwrap();
                    let now = std::time::Instant::now();
                    st.downloads.retain(|_, (_, _, exp)| *exp > now);
                    st.downloads.get(token)
                        .map(|(f, d, _)| (f.clone(), d.clone()))
                };
                match item {
                    Some((filename, data)) => {
                        self.send(&json!({"type": "file_meta", "reqId": req,
                                          "ok": true, "filename": filename}));
                        let _ = self.inner.tx.send(
                            Out::Binary(bin_frame(6, &req, &data)));
                    }
                    None => self.send(&json!({"type": "file_meta",
                                              "reqId": req, "ok": false})),
                }
            }
            "file_push" => {
                // An upload's meta: its FILE bytes follow on this socket —
                // stash until they land (handle_file_bytes pairs by reqId).
                if let Some(req) = msg.get("reqId").and_then(Value::as_str) {
                    self.inner.state.lock().unwrap()
                        .pending_pushes.insert(req.into(), msg.clone());
                }
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

/// Shift `anchor`'s below/right_of dependents by `dh`, recursively, folding
/// each dependent's new y into the replay state and collecting the frames to
/// send (caller sends outside the lock). The Rust `_cascade_height`.
fn collect_y_shifts(st: &mut State, anchor: &str, dh: f64,
                    out: &mut Vec<(String, f64)>) {
    let deps: Vec<String> = st.y_deps.get(anchor).cloned().unwrap_or_default();
    for dep in deps {
        if out.iter().any(|(d, _)| d == &dep) { continue; } // cycle guard
        let y = st.updates.get(&dep)
            .and_then(|u| u.get("y")).and_then(Value::as_f64)
            .or_else(|| st.registers.get(&dep)
                .and_then(|r| r.get("y")).and_then(Value::as_f64));
        let Some(y) = y else { continue };
        let new_y = y + dh;
        st.updates.entry(dep.clone()).or_default().insert("y".into(), json!(new_y));
        out.push((dep.clone(), new_y));
        collect_y_shifts(st, &dep, dh, out);
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

