//! A Rust remake of examples/catalogue.py — one column showcasing every native
//! danvas panel, authored from the danvas-source SDK, matched panel-for-panel:
//! same ids, card labels, rainbow colors, positions, and
//! populated data (table rows, a Plotly plot, the real histogram, a *streaming*
//! live plot, a matplotlib image, a *streaming* audio tone + webcam). One binary
//! broker + one protocol, a Rust program drives the *same* full canvas.
//!
//!     danvasd --host 0.0.0.0 --port 8200
//!     cargo run --example catalogue -- 8200

use danvas_source::Client;
use serde_json::{json, Value};
use std::path::Path;
use std::sync::{Arc, Mutex};

/// The file-browser listing payload for `cwd` (sandboxed under `root`): folders
/// first, then files, each alphabetical — the shape the FileBrowser panel reads.
fn fb_listing(root: &Path, cwd: &Path) -> Value {
    let mut entries: Vec<(String, bool, u64)> = Vec::new();
    if let Ok(rd) = std::fs::read_dir(cwd) {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if name.starts_with('.') { continue; }
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
    json!({"cwd": display, "atRoot": cwd == root, "selected": Value::Null, "entries": items})
}

const HISTOGRAM_FIG: &str = include_str!("assets/histogram_fig.json");
const LIVEPLOT_FIG: &str = include_str!("assets/liveplot_fig.json");
const IMAGE_SRC: &str = include_str!("assets/image_src.txt");
/// A 24-frame webcam animation (scrolling scope) so the feed is visibly live.
const WEBCAM: [&[u8]; 24] = [
    include_bytes!("assets/webcam/f00.jpg"), include_bytes!("assets/webcam/f01.jpg"),
    include_bytes!("assets/webcam/f02.jpg"), include_bytes!("assets/webcam/f03.jpg"),
    include_bytes!("assets/webcam/f04.jpg"), include_bytes!("assets/webcam/f05.jpg"),
    include_bytes!("assets/webcam/f06.jpg"), include_bytes!("assets/webcam/f07.jpg"),
    include_bytes!("assets/webcam/f08.jpg"), include_bytes!("assets/webcam/f09.jpg"),
    include_bytes!("assets/webcam/f10.jpg"), include_bytes!("assets/webcam/f11.jpg"),
    include_bytes!("assets/webcam/f12.jpg"), include_bytes!("assets/webcam/f13.jpg"),
    include_bytes!("assets/webcam/f14.jpg"), include_bytes!("assets/webcam/f15.jpg"),
    include_bytes!("assets/webcam/f16.jpg"), include_bytes!("assets/webcam/f17.jpg"),
    include_bytes!("assets/webcam/f18.jpg"), include_bytes!("assets/webcam/f19.jpg"),
    include_bytes!("assets/webcam/f20.jpg"), include_bytes!("assets/webcam/f21.jpg"),
    include_bytes!("assets/webcam/f22.jpg"), include_bytes!("assets/webcam/f23.jpg"),
];

const ROSE: &str = "#e05c7a";
const AMBER: &str = "#e0923a";
const YELLOW: &str = "#c8b400";
const TEAL: &str = "#2aab8a";
const SKY: &str = "#3a8fd4";
const INDIGO: &str = "#6b6bd4";
const VIOLET: &str = "#a45cc8";
const PINK: &str = "#d45aa0";
const CORAL: &str = "#e06050";
const SAGE: &str = "#5aab72";
const SLATE: &str = "#5a8aaa";
const PLUM: &str = "#8a5ab4";

fn main() {
    let port = std::env::args().nth(1).unwrap_or_else(|| "8200".into());
    let c = Client::connect(&format!("127.0.0.1:{port}"), "catalogue-rust")
        .expect("connect to danvasd");
    println!("[rust] catalogue connected on :{port}");

    // Explicit positions copied from the Python catalogue's resolved masonry
    // layout, so the two are pixel-identical. (below=/right_of=/... exist on the
    // builder too, for browser-driven relative placement.)

    // 1. Label — also the live readout for the slider handler.
    c.label("lbl", "Hello from label").titled("Label").at(80.0, 80.0).size(240.0, 84.0).color(ROSE).show();

    // 2. Markdown
    c.panel("md", "markdown")
        .set("html", json!("<p><strong>Markdown</strong> \u{2014} supports \
             <code>code</code>, <em>italics</em>, lists, and more.</p>"))
        .titled("Markdown").at(344.0, 176.0).size(380.0, 240.0).color(AMBER).show();

    // 3. Slider — a browser drag runs this Rust handler -> the label.
    c.slider("brightness", 0.0, 100.0, 50.0).titled("Slider").at(748.0, 282.0).size(240.0, 96.0).color(YELLOW).show();
    let inner = c.clone();
    c.on_input("brightness", move |p| {
        let v = p.get("value").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let word = if v < 33.0 { "dim" } else if v > 66.0 { "bright" } else { "medium" };
        inner.update("lbl", "post", json!(format!("brightness {v:.0} ({word})")));
    });

    // 4. Toggle
    c.panel("speed", "toggle").set("options", json!(["Off", "Slow", "Fast"]))
        .set("value", json!("Slow")).titled("Toggle").at(1012.0, 382.0).size(260.0, 84.0).color(TEAL).show();

    // 5. Button
    c.button("ping").set("text", json!("Click me")).titled("Button").at(1296.0, 494.0).size(200.0, 84.0).color(SKY).show();

    // 6. Text field
    c.panel("input", "text_field").set("placeholder", json!("Type something\u{2026}"))
        .titled("Text field").at(80.0, 589.0).size(240.0, 80.0).color(INDIGO).show();

    // 7. Table — three rows, like the .py.
    c.panel("scores", "table")
        .set("cols", json!(["Name", "Score"])).set("numeric", json!([false, true]))
        .set("rows", json!([["Alice", 92], ["Bob", 85], ["Carol", 78]]))
        .titled("Table").at(344.0, 700.0).size(520.0, 360.0).color(VIOLET).show();

    // 8. Plot — a sine curve (props._fig via a data_patch update).
    c.panel("chart", "plot").titled("Plot").at(888.0, 959.0).size(560.0, 420.0).color(PINK).show();
    let xs: Vec<i32> = (0..20).collect();
    let ys: Vec<f64> = xs.iter().map(|&x| ((x as f64) * 0.4).sin()).collect();
    c.update("chart", "data_patch", json!({ "_fig": {
        "data": [{"x": xs, "y": ys, "mode": "lines+markers", "type": "scatter", "name": "sin"}],
        "layout": {"margin": {"t": 20, "l": 30, "r": 10, "b": 24}}
    }}));

    // 9. Histogram — the exact figure the Python component builds.
    c.panel("hist", "histogram").titled("Histogram").at(80.0, 1395.0).size(560.0, 420.0).color(CORAL).show();
    let hfig: Value = serde_json::from_str(HISTOGRAM_FIG).unwrap();
    c.update("hist", "data_patch", json!({ "_fig": hfig }));

    // 10. Live plot — STREAM a sine signal (the `plot` value payload), like the
    //     .py background loop, so the curve moves.
    c.panel("live", "live_plot").titled("Live plot").at(664.0, 1831.0).size(560.0, 380.0).color(SAGE).show();
    let mut base: Value = serde_json::from_str(LIVEPLOT_FIG).unwrap();
    let lc = c.clone();
    std::thread::spawn(move || {
        let (mut xs, mut yy, mut t) = (Vec::new(), Vec::new(), 0.0_f64);
        loop {
            xs.push(t);
            yy.push((t * 0.5).sin());
            if xs.len() > 160 { xs.remove(0); yy.remove(0); }
            base["data"][0]["x"] = json!(xs);
            base["data"][0]["y"] = json!(yy);
            lc.update("live", "plot", base.clone());
            t += 0.128;
            std::thread::sleep(std::time::Duration::from_millis(120));
        }
    });

    // 11. Image — the matplotlib figure (a data-URL PNG).
    c.panel("img", "image").set("src", json!(IMAGE_SRC.trim()))
        .titled("Image").at(80.0, 2227.0).size(420.0, 320.0).color(SKY).show();

    // 12-16. Download / Upload / File browser / Webview / Inspector.
    c.panel("dl", "download").set("text", json!("Download hello.txt"))
        .titled("Download").at(524.0, 2507.0).size(200.0, 84.0).color(SLATE).show();
    c.panel("up", "upload").set("text", json!("Choose a file"))
        .titled("Upload").at(748.0, 2602.0).size(240.0, 120.0).color(PLUM).show();
    c.panel("fb", "file_browser").titled("File browser").at(1012.0, 2703.0).size(320.0, 420.0).color(ROSE).show();
    // A real, navigable directory listing served from Rust (root = cwd): push
    // the initial listing, then answer the panel's ready/open/up click events.
    let fb_root = std::env::current_dir().unwrap_or_else(|_| ".".into());
    let fb_cwd = Arc::new(Mutex::new(fb_root.clone()));
    c.update("fb", "post", fb_listing(&fb_root, &fb_cwd.lock().unwrap()));
    let (fc, root2, cwd2) = (c.clone(), fb_root.clone(), fb_cwd.clone());
    c.on_input("fb", move |p| {
        let ev = p.get("event").and_then(|v| v.as_str()).unwrap_or("");
        let mut d = cwd2.lock().unwrap();
        match ev {
            "up" => if *d != root2 {
                if let Some(par) = d.parent() {
                    if par.starts_with(&root2) || par == root2 { *d = par.to_path_buf(); }
                }
            },
            "open" => if let Some(name) = p.get("name").and_then(|v| v.as_str()) {
                let cand = d.join(name);
                if cand.is_dir() && cand.starts_with(&root2) { *d = cand; }
            },
            _ => {} // "ready" (or a file open) — just (re)push the current listing
        }
        fc.update("fb", "post", fb_listing(&root2, &d));
    });
    c.panel("wv", "webview").set("url", json!("https://example.com"))
        .titled("Webview").at(80.0, 3451.0).size(800.0, 600.0).color(AMBER).show();
    c.panel("ins", "inspector").titled("Inspector").at(904.0, 3661.0).size(520.0, 320.0).color(TEAL).show();

    // 17. Audio feed — stream a 440 Hz tone so it actually plays (once enabled).
    c.audio("feed").titled("Audio feed").at(80.0, 3997.0).size(260.0, 120.0).color(INDIGO).show();
    let ac = c.clone();
    std::thread::spawn(move || {
        let (sr, freq) = (16000.0_f64, 440.0_f64);
        let mut phase = 0.0_f64;
        loop {
            let mut pcm = Vec::with_capacity(1024 * 2);
            for _ in 0..1024 {
                let s = (phase * std::f64::consts::TAU).sin() * 0.2 * i16::MAX as f64;
                pcm.extend_from_slice(&(s as i16).to_le_bytes());
                phase = (phase + freq / sr) % 1.0;
            }
            ac.send_audio("feed", &pcm);
            std::thread::sleep(std::time::Duration::from_millis(64));
        }
    });

    // 18. Webcam — the real camera when built `--features camera`, else the
    // baked animation (see spawn_webcam below).
    c.video("cam").titled("Webcam").at(364.0, 4133.0).size(340.0, 280.0).color(SAGE).show();
    spawn_webcam(&c);

    // 19. Chat
    c.panel("room", "chat").titled("Chat").at(728.0, 4350.0).size(320.0, 400.0).color(ROSE).show();

    // 20. Custom — a self-contained click-counter button.
    c.panel("cust", "custom").prop("html", json!(
        "<style>button{font:14px sans-serif;padding:8px 16px;border-radius:6px;\
         border:0;background:#6b6bd4;color:#fff;cursor:pointer}</style>\
         <button onclick=\"this.textContent='Clicked ' + (++n)\">Click me</button>\
         <script>var n=0;</script>"))
        .titled("Custom").at(1072.0, 4500.0).size(380.0, 320.0).color(AMBER).show();

    // Inspector: push the live panel table now that every panel is declared,
    // and re-push whenever the browser hits Refresh (or switches source/trace).
    c.update("ins", "data_patch", c.inspector_rows());
    let ic = c.clone();
    c.on_input("ins", move |_p| {
        ic.update("ins", "data_patch", ic.inspector_rows());
    });

    println!("[rust] catalogue live on :{port}; ctrl+c to stop");
    std::thread::park();
}

/// Stream the baked 24-frame animation as the webcam feed (~15 fps) — the
/// default when the `camera` feature is off (no platform camera libs needed).
#[cfg(not(feature = "camera"))]
fn spawn_webcam(c: &Client) {
    let vc = c.clone();
    std::thread::spawn(move || {
        let mut i = 0usize;
        loop {
            vc.send_video("cam", WEBCAM[i % WEBCAM.len()]);
            i += 1;
            std::thread::sleep(std::time::Duration::from_millis(66));
        }
    });
}

/// Capture the physical webcam and stream real JPEG frames — built with
/// `--features camera`. Falls back to the baked animation if the camera can't
/// be opened (busy, absent). MJPEG cameras pass through untouched; other
/// formats are decoded to RGB and re-encoded to JPEG.
#[cfg(feature = "camera")]
fn spawn_webcam(c: &Client) {
    use nokhwa::pixel_format::RgbFormat;
    use nokhwa::utils::{CameraIndex, FrameFormat, RequestedFormat, RequestedFormatType};
    use nokhwa::Camera;

    let vc = c.clone();
    std::thread::spawn(move || {
        let requested =
            RequestedFormat::new::<RgbFormat>(RequestedFormatType::AbsoluteHighestFrameRate);
        let mut cam = match Camera::new(CameraIndex::Index(0), requested) {
            Ok(cam) => cam,
            Err(e) => {
                eprintln!("[rust] camera open failed ({e}); using the animation");
                return animate(vc);
            }
        };
        if let Err(e) = cam.open_stream() {
            eprintln!("[rust] camera stream failed ({e}); using the animation");
            return animate(vc);
        }
        println!("[rust] webcam: streaming the real camera");
        loop {
            let frame = match cam.frame() {
                Ok(f) => f,
                Err(e) => {
                    eprintln!("[rust] frame error: {e}");
                    std::thread::sleep(std::time::Duration::from_millis(100));
                    continue;
                }
            };
            // MJPEG cameras hand us a JPEG already — forward it untouched.
            if frame.source_frame_format() == FrameFormat::MJPEG {
                vc.send_video("cam", frame.buffer());
                continue;
            }
            // Otherwise decode to RGB and JPEG-encode ourselves.
            if let Ok(rgb) = frame.decode_image::<RgbFormat>() {
                let (w, h) = (rgb.width(), rgb.height());
                let mut jpg = Vec::new();
                let enc = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut jpg, 75);
                if image::ImageEncoder::write_image(
                    enc, &rgb, w, h, image::ExtendedColorType::Rgb8,
                ).is_ok() {
                    vc.send_video("cam", &jpg);
                }
            }
        }
    });
}

/// The baked-animation loop, shared by the no-camera build and the camera
/// build's fallback.
#[cfg(feature = "camera")]
fn animate(vc: Client) {
    let mut i = 0usize;
    loop {
        vc.send_video("cam", WEBCAM[i % WEBCAM.len()]);
        i += 1;
        std::thread::sleep(std::time::Duration::from_millis(66));
    }
}
