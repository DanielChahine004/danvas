//! Owner-side component logic, transliterated from the Python components so a
//! Rust source drives the same panels the same way instead of hand-building
//! Plotly figures: [`LivePlot`] (danvas/components/liveplot.py's rolling
//! buffer + extend deltas), [`Histogram`] (histogram.py's fixed-bin density
//! heatmap), [`Client::file_browser`] (filebrowser.py's sandboxed listing/
//! navigation), and [`data_url`] (image.py's bytes → data-URL encoding).

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};

use crate::{Client, PanelBuilder};

/// Bytes → a `data:` URL (what an `image` panel's `src` data field takes) —
/// the encoding half of Python's `canvas.image()`. Pass the real MIME type
/// (`"image/png"`, `"image/jpeg"`, …).
pub fn data_url(mime: &str, bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut b64 = String::with_capacity((bytes.len() + 2) / 3 * 4);
    for chunk in bytes.chunks(3) {
        let n = (chunk[0] as u32) << 16
            | (*chunk.get(1).unwrap_or(&0) as u32) << 8
            | *chunk.get(2).unwrap_or(&0) as u32;
        b64.push(ALPHABET[(n >> 18) as usize & 63] as char);
        b64.push(ALPHABET[(n >> 12) as usize & 63] as char);
        b64.push(if chunk.len() > 1 { ALPHABET[(n >> 6) as usize & 63] as char } else { '=' });
        b64.push(if chunk.len() > 2 { ALPHABET[n as usize & 63] as char } else { '=' });
    }
    format!("data:{mime};base64,{b64}")
}

/// The streaming feed behind a `live_plot` panel: a rolling per-trace buffer
/// whose [`push`](Self::push) streams just the new points as an extend delta
/// (`plot_extend` — the frontend appends with `Plotly.extendTraces`), sending
/// a full figure only when a new trace appears; the full figure is always
/// folded into the replay cache so a (re)connecting client renders at once.
/// The transliteration of Python's `LivePlot.push` (without `smoothing`).
///
/// ```no_run
/// # use serde_json::json;
/// # let c = danvas::Client::connect("127.0.0.1:8000", "x").unwrap();
/// c.panel("live", "live_plot").titled("Live plot").show();
/// let mut feed = c.live_plot_feed("live", 300);
/// feed.push(&[("signal", 0.7)]);
/// ```
pub struct LivePlot {
    client: Client,
    id: String,
    max: usize,
    counter: u64,
    traces: Vec<String>,
    xs: Vec<VecDeque<f64>>,
    ys: Vec<VecDeque<f64>>,
}

impl LivePlot {
    /// Append one sample per trace. A key not seen before starts a new trace
    /// on the fly (like the Python `push({"s1": 90, "s2": 45})`).
    pub fn push(&mut self, sample: &[(&str, f64)]) {
        self.counter += 1;
        let x = self.counter as f64;
        let mut new_trace = false;
        for (name, v) in sample {
            let i = match self.traces.iter().position(|t| t == name) {
                Some(i) => i,
                None => {
                    new_trace = true;
                    self.traces.push((*name).into());
                    self.xs.push(VecDeque::with_capacity(self.max));
                    self.ys.push(VecDeque::with_capacity(self.max));
                    self.traces.len() - 1
                }
            };
            if self.xs[i].len() == self.max {
                self.xs[i].pop_front();
                self.ys[i].pop_front();
            }
            self.xs[i].push_back(x);
            self.ys[i].push_back(*v);
        }
        let fig = self.figure();
        if new_trace {
            // A delta can't add a curve: fall back to the full figure.
            self.client.update_split(&self.id, json!({"plot": fig}), "plot", fig);
            return;
        }
        let mut indices = Vec::new();
        let (mut dx, mut dy) = (Vec::new(), Vec::new());
        for (name, v) in sample {
            let i = self.traces.iter().position(|t| t == name).unwrap();
            indices.push(i);
            dx.push(vec![x]);
            dy.push(vec![*v]);
        }
        let ext = json!({"plot_extend": {
            "indices": indices, "x": dx, "y": dy, "max": self.max}});
        self.client.update_split(&self.id, ext, "plot", fig);
    }

    /// The full Plotly figure over the current buffer — the same trace/layout
    /// shape `LivePlot._payload` builds.
    fn figure(&self) -> Value {
        let data: Vec<Value> = self.traces.iter().enumerate().map(|(i, name)| {
            json!({"x": self.xs[i], "y": self.ys[i], "name": name,
                   "mode": "lines", "type": "scatter"})
        }).collect();
        json!({"data": data, "layout": {
            "margin": {"l": 40, "r": 15, "t": 15, "b": 30},
            "showlegend": true, "legend": {"orientation": "h"}}})
    }
}

/// The recording feed behind a `histogram` panel: [`add`](Self::add) one
/// distribution per step; the panel shows the density heatmap of how it
/// shifts across steps (bins fixed on the first add so every step shares
/// them) — the transliteration of Python's `Histogram.add`/`_figure`
/// (heatmap mode).
pub struct Histogram {
    client: Client,
    id: String,
    bins: usize,
    color: String,
    max_steps: usize,
    edges: Option<Vec<f64>>,
    records: VecDeque<(f64, Vec<f64>)>,
}

impl Histogram {
    /// Record one distribution. `step` defaults to the record count.
    pub fn add(&mut self, values: &[f64], step: Option<f64>) {
        if values.is_empty() {
            return;
        }
        let edges = self.edges.get_or_insert_with(|| {
            let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
            for &v in values {
                lo = lo.min(v);
                hi = hi.max(v);
            }
            if hi <= lo {
                hi = lo + 1.0;
            }
            (0..=self.bins)
                .map(|i| lo + (hi - lo) * i as f64 / self.bins as f64)
                .collect()
        });
        let (lo, hi) = (edges[0], edges[self.bins]);
        let width = (hi - lo) / self.bins as f64;
        let mut counts = vec![0.0f64; self.bins];
        let mut total = 0.0;
        for &v in values {
            if v < lo || v > hi {
                continue;
            }
            let i = (((v - lo) / width) as usize).min(self.bins - 1);
            counts[i] += 1.0;
            total += 1.0;
        }
        if total > 0.0 {
            for c in counts.iter_mut() {
                *c /= total * width; // density=True normalization
            }
        }
        let step = step.unwrap_or(self.records.len() as f64);
        if self.records.len() == self.max_steps {
            self.records.pop_front();
        }
        self.records.push_back((step, counts));
        let fig = self.figure();
        self.client.update(&self.id, "data_patch", json!({"_fig": fig}));
    }

    fn figure(&self) -> Value {
        let edges = self.edges.as_ref().unwrap();
        let centers: Vec<f64> = (0..self.bins)
            .map(|i| (edges[i] + edges[i + 1]) / 2.0).collect();
        let steps: Vec<f64> = self.records.iter().map(|(s, _)| *s).collect();
        // z is [bin][step] — the Python `.T` transpose.
        let z: Vec<Vec<f64>> = (0..self.bins).map(|b| {
            self.records.iter().map(|(_, counts)| counts[b]).collect()
        }).collect();
        json!({"data": [{
            "type": "heatmap", "x": steps, "y": centers, "z": z,
            "colorscale": [[0, "#ffffff"], [1, self.color]], "showscale": false,
        }], "layout": {
            "margin": {"l": 45, "r": 15, "t": 15, "b": 35},
            "xaxis": {"title": {"text": "step"}},
            "yaxis": {"title": {"text": "value"}},
        }})
    }
}

/// Per-inspector-panel state: the current view, the drilled-into row, the
/// trace toggle + its rolling event log, and the last-built rows (so the
/// detail view of a non-panel row can answer from what was shown).
struct InsState {
    view: String,
    detail_key: Option<String>,
    trace: bool,
    log: VecDeque<String>,
    last_rows: Vec<Value>,
}

fn esc(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

/// The drill-down payload for a row `key`: rich (register + props + state)
/// for a panel this source registered, else the row's own name/type/value
/// re-shaped as fields, else a "no longer available" marker.
fn detail_for(client: &Client, st: &InsState, key: &str) -> Value {
    if let Some(d) = client.inspector_detail(key) {
        return d;
    }
    if let Some(row) = st.last_rows.iter()
        .find(|r| r.get("key").and_then(Value::as_str) == Some(key))
    {
        let field = |k: &str| json!({
            "field": k, "type": "str",
            "value": row.get(k).map(|v| match v {
                Value::String(s) => s.clone(),
                v => v.to_string(),
            }).unwrap_or_default(),
        });
        return json!({"key": key,
                      "type": row.get("type").cloned().unwrap_or(json!("row")),
                      "repr": row.get("value").cloned().unwrap_or(json!("")),
                      "fields": [field("name"), field("type"), field("value")]});
    }
    json!({"key": key, "missing": true})
}

impl Client {
    /// An Inspector panel wired like Python's: the header dropdown switches
    /// views live (`panels` — every panel on the canvas; `canvas` — this
    /// instance's weight; `system` — host basics; `globals` — Python-only,
    /// says so), Refresh rebuilds, clicking a row drills into its fields, and
    /// Trace toggles a live event-log panel beside it (the Rust stand-in for
    /// Python's dispatch-trace call tree: one line per input/layout/request
    /// this source is routed). Returns the builder — place and `.show()`.
    pub fn inspector(&self, id: &str) -> PanelBuilder<'_> {
        let s = Arc::new(Mutex::new(InsState {
            view: "components".into(), detail_key: None, trace: false,
            log: VecDeque::new(), last_rows: Vec::new(),
        }));
        let pid = id.to_string();
        let trace_id = format!("{id}-trace");

        // The event log behind the Trace panel: tap interactions routed to
        // this source (cheap no-op while the trace is closed).
        {
            let (s2, c2, tid) = (s.clone(), self.clone(), trace_id.clone());
            self.on_frame(move |msg| {
                let kind = msg.get("type").and_then(Value::as_str).unwrap_or("");
                if !matches!(kind, "input" | "layout" | "request") {
                    return;
                }
                let target = msg.get("id").and_then(Value::as_str).unwrap_or("");
                if target == tid {
                    return;
                }
                let html = {
                    let mut st = s2.lock().unwrap();
                    if !st.trace {
                        return;
                    }
                    let what = msg.get("payload").or_else(|| msg.get("data"))
                        .map(|v| v.to_string()).unwrap_or_default();
                    let what: String = what.chars().take(60).collect();
                    st.log.push_back(format!(
                        "{:>7.1}s  {:<7} {:<12} {}",
                        c2.uptime_secs(), kind, target, what));
                    if st.log.len() > 30 {
                        st.log.pop_front();
                    }
                    let body: Vec<String> =
                        st.log.iter().map(|l| esc(l)).collect();
                    format!(
                        "<pre style=\"margin:0;font:11px ui-monospace,monospace;\
                         white-space:pre-wrap;word-break:break-all\">{}</pre>",
                        body.join("\n"))
                };
                c2.update(&tid, "data_patch", json!({"html": html}));
            });
        }

        // The action handler: the same {action: …} frames the JSX sends Python.
        let (s3, c3, pid3, tid3) = (s.clone(), self.clone(), pid.clone(), trace_id);
        self.on_input(id, move |p| {
            let action = p.get("action").and_then(Value::as_str).unwrap_or("");
            let build = |view: &str| -> Vec<Value> {
                match view {
                    "canvas" => c3.inspector_canvas_rows(),
                    "system" => c3.inspector_system_rows(),
                    "globals" => vec![json!({
                        "key": "globals", "name": "globals", "type": "str",
                        "value": "script globals are a Python-source view",
                    })],
                    _ => c3.inspector_rows()["rows"]
                        .as_array().cloned().unwrap_or_default(),
                }
            };
            match action {
                "refresh" => {
                    let mut st = s3.lock().unwrap();
                    st.last_rows = build(&st.view);
                    let mut patch = json!({"rows": st.last_rows});
                    if let Some(key) = st.detail_key.clone() {
                        patch["detail"] = detail_for(&c3, &st, &key);
                    }
                    drop(st);
                    c3.update(&pid3, "data_patch", patch);
                }
                "source" => {
                    let view = p.get("source").and_then(Value::as_str)
                        .unwrap_or("components").to_string();
                    let mut st = s3.lock().unwrap();
                    st.view = view.clone();
                    st.detail_key = None;
                    st.last_rows = build(&view);
                    let cols: Vec<&str> = if view == "components" {
                        vec!["name", "label", "type", "value", "visible",
                             "x", "y", "w", "h"]
                    } else {
                        vec!["name", "type", "value"]
                    };
                    let patch = json!({"source": view, "cols": cols,
                                       "rows": st.last_rows});
                    drop(st);
                    c3.update(&pid3, "data_patch", patch);
                }
                "detail" => {
                    let key = p.get("key").and_then(Value::as_str)
                        .map(String::from);
                    let mut st = s3.lock().unwrap();
                    st.detail_key = key.clone();
                    let patch = key.map(|k| json!({"detail": detail_for(&c3, &st, &k)}));
                    drop(st);
                    if let Some(patch) = patch {
                        c3.update(&pid3, "data_patch", patch);
                    }
                }
                "trace" => {
                    let open = {
                        let mut st = s3.lock().unwrap();
                        st.trace = !st.trace;
                        if !st.trace {
                            st.log.clear();
                        }
                        st.trace
                    };
                    if open {
                        c3.panel(&tid3, "markdown")
                            .set("html", json!("<pre style=\"margin:0;font:11px \
                                 ui-monospace,monospace\">waiting for events…</pre>"))
                            .titled("Trace — routed events")
                            .right_of(&pid3).gap(20.0)
                            .size(420.0, 320.0)
                            .show();
                    } else {
                        c3.remove(&tid3);
                    }
                }
                _ => {}
            }
        });
        self.panel(id, "inspector")
    }

    /// A Custom panel (raw HTML/CSS/JS in a sandboxed iframe). The frontend
    /// owns the rest (customShim.ts): it wraps a bare fragment with the base
    /// reset (content centred) and injects the `canvas` API + the interaction
    /// shim — scroll-to-zoom, right-drag pan, context menu, tool shortcuts —
    /// keyed by the browser-local panel id. This just merges the parts; pass
    /// `""` for unused ones.
    pub fn custom(&self, id: &str, html: &str, css: &str, js: &str) -> PanelBuilder<'_> {
        let mut doc = String::new();
        if !css.is_empty() {
            doc.push_str(&format!("<style>{css}</style>"));
        }
        doc.push_str(html);
        if !js.is_empty() {
            doc.push_str(&format!("<script>{js}</script>"));
        }
        self.panel(id, "custom").prop("html", json!(doc))
    }

    /// The streaming feed for a registered `live_plot` panel — see [`LivePlot`].
    pub fn live_plot_feed(&self, id: &str, max_points: usize) -> LivePlot {
        LivePlot { client: self.clone(), id: id.into(), max: max_points,
                   counter: 0, traces: Vec::new(), xs: Vec::new(), ys: Vec::new() }
    }

    /// The recording feed for a registered `histogram` panel — see
    /// [`Histogram`]. `color` tints the heatmap (pass the panel's accent).
    pub fn histogram_feed(&self, id: &str, bins: usize, color: Option<&str>)
        -> Histogram
    {
        Histogram { client: self.clone(), id: id.into(), bins: bins.max(1),
                    color: color.unwrap_or("#4a90d9").into(), max_steps: 200,
                    edges: None, records: VecDeque::new() }
    }

    /// A `file_browser` panel serving a real, navigable directory listing
    /// sandboxed under `root` — the owner-side logic of Python's FileBrowser
    /// (the viewer picks paths, so nothing may escape the root). Returns the
    /// builder; chain placement and `.show()` as usual. The listing is pushed
    /// when the panel mounts (its `ready` event) and re-pushed per navigation.
    pub fn file_browser(&self, id: &str, root: impl Into<PathBuf>) -> PanelBuilder<'_> {
        let root: PathBuf = root.into();
        let root = std::fs::canonicalize(&root).unwrap_or(root);
        let cwd = Arc::new(Mutex::new(root.clone()));
        let client = self.clone();
        let pid = id.to_string();
        self.on_input(id, move |p| {
            let ev = p.get("event").and_then(Value::as_str).unwrap_or("");
            let mut d = cwd.lock().unwrap();
            match ev {
                "up" => {
                    if *d != root {
                        if let Some(parent) = d.parent() {
                            if parent.starts_with(&root) {
                                *d = parent.to_path_buf();
                            }
                        }
                    }
                }
                "open" => {
                    if let Some(name) = p.get("name").and_then(Value::as_str) {
                        // A single path component only — no separators, no "..".
                        if !name.contains(['/', '\\']) && name != ".." {
                            let cand = d.join(name);
                            if cand.is_dir() && cand.starts_with(&root) {
                                *d = cand;
                            }
                        }
                    }
                }
                _ => {} // "ready" (or a file open): just (re)push the listing
            }
            client.update(&pid, "post", fb_listing(&root, &d));
        });
        self.panel(id, "file_browser")
    }
}

/// The listing payload for `cwd` (sandboxed under `root`): folders first, then
/// files, each alphabetical — the shape the FileBrowser panel renders.
fn fb_listing(root: &Path, cwd: &Path) -> Value {
    let mut entries: Vec<(String, bool, u64)> = Vec::new();
    if let Ok(rd) = std::fs::read_dir(cwd) {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if name.starts_with('.') {
                continue;
            }
            let md = e.metadata().ok();
            let is_dir = md.as_ref().map(|m| m.is_dir()).unwrap_or(false);
            let size = if is_dir { 0 } else { md.map(|m| m.len()).unwrap_or(0) };
            entries.push((name, is_dir, size));
        }
    }
    entries.sort_by(|a, b| (!a.1).cmp(&(!b.1))
        .then(a.0.to_lowercase().cmp(&b.0.to_lowercase())));
    let items: Vec<Value> = entries.iter()
        .map(|(n, d, s)| json!({"name": n, "dir": d, "size": s})).collect();
    let display = match cwd.strip_prefix(root) {
        Ok(rel) if rel.as_os_str().is_empty() => "/".to_string(),
        Ok(rel) => format!("/{}", rel.to_string_lossy().replace('\\', "/")),
        Err(_) => "/".to_string(),
    };
    json!({"cwd": display, "atRoot": cwd == root,
           "selected": Value::Null, "entries": items})
}
