//! A Rust remake of examples/catalogue.py — one column showcasing every native
//! danvas panel, authored from the danvas-source SDK the same way the Python
//! one is: `below()` chaining instead of hardcoded coordinates, the SDK's
//! component feeds (histogram binning, live-plot extend deltas, sandboxed
//! file browsing) instead of baked figures, and real downloads/uploads.
//!
//! Self-contained by default — it spawns `danvasd` and opens the browser:
//!
//!     cargo run --example catalogue
//!
//! Or dial into a broker you started yourself:
//!
//!     danvasd --host 0.0.0.0 --port 8200
//!     cargo run --example catalogue -- 8200

use danvas_source::Client;
use serde_json::json;

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

/// `n` samples from N(mean, std²) — xorshift64 + Box-Muller, so the histogram
/// records the same normal-per-epoch distributions the .py draws with numpy.
fn normals(seed: &mut u64, mean: f64, std: f64, n: usize) -> Vec<f64> {
    let mut next = || {
        let mut x = *seed;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        *seed = x;
        (x >> 11) as f64 / (1u64 << 53) as f64
    };
    (0..n).map(|_| {
        let (u1, u2) = (next().max(1e-12), next());
        mean + std * (-2.0 * u1.ln()).sqrt() * (std::f64::consts::TAU * u2).cos()
    }).collect()
}

fn main() {
    // With a port argument, dial that broker; without one, be self-contained:
    // spawn danvasd, dial in, open the browser (the Python catalogue's serve()).
    let (broker, c) = match std::env::args().nth(1) {
        Some(port) => {
            let c = Client::connect(&format!("127.0.0.1:{port}"), "catalogue-rust")
                .expect("connect to danvasd");
            println!("[rust] catalogue connected on :{port}");
            (None, c)
        }
        None => {
            let (b, c) = danvas_source::serve(8200, "catalogue-rust")
                .expect("spawn danvasd (build it or set $DANVASD)");
            println!("[rust] catalogue serving itself at {}", b.url());
            (Some(b), c)
        }
    };

    // One column, chained below= like the .py — only the root is positioned.

    // 1. Label — also the live readout for the slider handler.
    c.label("lbl", "Hello from label").titled("Label")
        .at(80.0, 80.0).size(240.0, 84.0).color(ROSE).show();

    // 2. Markdown
    c.panel("md", "markdown")
        .set("html", json!("<p><strong>Markdown</strong> \u{2014} supports \
             <code>code</code>, <em>italics</em>, lists, and more.</p>"))
        .titled("Markdown").below("lbl").size(380.0, 240.0).color(AMBER).show();

    // 3. Slider — a browser drag runs this Rust handler -> the label.
    c.slider("brightness", 0.0, 100.0, 50.0).titled("Slider")
        .below("md").size(240.0, 96.0).color(YELLOW).show();
    let inner = c.clone();
    c.on_input("brightness", move |p| {
        let v = p.get("value").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let word = if v < 33.0 { "dim" } else if v > 66.0 { "bright" } else { "medium" };
        inner.update("lbl", "post", json!(format!("brightness {v:.0} ({word})")));
    });

    // 4. Toggle
    c.panel("speed", "toggle").set("options", json!(["Off", "Slow", "Fast"]))
        .set("value", json!("Slow")).titled("Toggle")
        .below("brightness").size(260.0, 84.0).color(TEAL).show();

    // 5. Button
    c.button("ping").set("text", json!("Click me")).titled("Button")
        .below("speed").size(200.0, 84.0).color(SKY).show();

    // 6. Text field
    c.panel("input", "text_field").set("placeholder", json!("Type something\u{2026}"))
        .titled("Text field").below("ping").size(240.0, 80.0).color(INDIGO).show();

    // 7. Table — three rows, like the .py.
    c.panel("scores", "table")
        .set("cols", json!(["Name", "Score"])).set("numeric", json!([false, true]))
        .set("rows", json!([["Alice", 92], ["Bob", 85], ["Carol", 78]]))
        .titled("Table").below("input").size(520.0, 360.0).color(VIOLET).show();

    // 8. Plot — a sine curve (props._fig via a data_patch update).
    c.panel("chart", "plot").titled("Plot")
        .below("scores").size(560.0, 420.0).color(PINK).show();
    let xs: Vec<i32> = (0..20).collect();
    let ys: Vec<f64> = xs.iter().map(|&x| ((x as f64) * 0.4).sin()).collect();
    c.update("chart", "data_patch", json!({ "_fig": {
        "data": [{"x": xs, "y": ys, "mode": "lines+markers", "type": "scatter", "name": "sin"}],
        "layout": {"margin": {"t": 20, "l": 30, "r": 10, "b": 24}}
    }}));

    // 9. Histogram — five epochs of drifting normals, binned by the SDK feed
    //    (the same fixed-bin density heatmap Histogram.add builds in Python).
    c.panel("hist", "histogram").titled("Histogram")
        .below("chart").size(560.0, 420.0).color(CORAL).show();
    let mut hist = c.histogram_feed("hist", 30, Some(CORAL));
    let mut seed = 0x9e3779b97f4a7c15u64;
    for epoch in 0..5 {
        hist.add(&normals(&mut seed, epoch as f64 * 0.3, 1.0, 300),
                 Some(epoch as f64));
    }

    // 10. Live plot — STREAM a sine signal through the SDK feed (extend
    //     deltas on the wire, full figure folded for replay). Same cadence,
    //     jitter, and window as the .py background loop.
    c.panel("live", "live_plot").titled("Live plot")
        .below("hist").size(560.0, 380.0).color(SAGE).show();
    let mut live = c.live_plot_feed("live", 300);
    std::thread::spawn(move || {
        let mut t = 0.0_f64;
        let mut seed = 0x2545f4914f6cdd1du64;
        let mut uniform = move |lo: f64, hi: f64| {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            lo + (hi - lo) * ((seed >> 11) as f64 / (1u64 << 53) as f64)
        };
        loop {
            live.push(&[("signal", (t * 0.5 + uniform(-0.1, 0.1)).sin())]);
            t += 0.064;
            std::thread::sleep(std::time::Duration::from_millis(64));
        }
    });

    // 11. Image — the matplotlib figure (a data-URL PNG; encode your own
    //     bytes with danvas_source::data_url).
    c.panel("img", "image").set("src", json!(IMAGE_SRC.trim()))
        .titled("Image").below("live").size(420.0, 320.0).color(SKY).show();

    // 12. Download: a click asks this process for the bytes (file_pull + FILE
    //     envelope through the hub), same content as the .py.
    c.panel("dl", "download").set("text", json!("Download hello.txt"))
        .titled("Download").below("img").size(200.0, 84.0).color(SLATE).show();
    c.on_download("dl", || ("hello.txt".into(), b"hello from danvas\n".to_vec()));

    // 13. Upload: mint a receiving endpoint and patch it into the panel's url.
    c.panel("up", "upload").set("text", json!("Choose a file"))
        .titled("Upload").below("dl").size(240.0, 120.0).color(PLUM).show();
    c.on_upload("up", |f| println!("[rust] upload: {} ({} bytes, {})",
                                   f.name, f.size, f.content_type));

    // 14. File browser — a real, navigable listing sandboxed under cwd,
    //     served by the SDK (Python FileBrowser's owner-side logic).
    let fb_root = std::env::current_dir().unwrap_or_else(|_| ".".into());
    c.file_browser("fb", fb_root)
        .titled("File browser").below("up").size(320.0, 420.0).color(ROSE).show();

    // 15-16. Webview / Inspector. The inspector arrives fully wired: view
    // dropdown, Refresh, row drill-down, and the Trace event-log panel.
    c.panel("wv", "webview").set("url", json!("https://example.com"))
        .titled("Webview").below("fb").size(800.0, 600.0).color(AMBER).show();
    c.inspector("ins").titled("Inspector")
        .below("wv").size(520.0, 320.0).color(TEAL).show();

    // 17. Audio feed — stream a 440 Hz tone so it actually plays (once enabled).
    c.audio("feed").titled("Audio feed")
        .below("ins").size(260.0, 120.0).color(INDIGO).show();
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
    c.video("cam").titled("Webcam")
        .below("feed").size(340.0, 280.0).color(SAGE).show();
    spawn_webcam(&c);

    // 19. Chat
    c.panel("room", "chat").titled("Chat")
        .below("cam").size(320.0, 400.0).color(ROSE).show();

    // 20. Custom — a self-contained click-counter button, the same html/css/js
    // split as the .py; c.custom() wraps it with the base reset (content
    // centred) and the interaction shim (canvas gestures over the iframe).
    c.custom("cust",
        "<button onclick=\"this.textContent='Clicked ' + (++n)\">Click me</button>",
        "button{font:14px sans-serif;padding:8px 16px;border-radius:6px;border:0;\
         background:#6b6bd4;color:#fff;cursor:pointer}",
        "var n=0;")
        .titled("Custom").below("room").size(380.0, 320.0).color(AMBER).show();

    // Inspector: seed the table now that every panel is declared (the wired
    // panel handles Refresh/views/drill-down/Trace from here on).
    c.update("ins", "data_patch", c.inspector_rows());

    match &broker {
        Some(b) => println!("[rust] catalogue live at {}; ctrl+c to stop", b.url()),
        None => println!("[rust] catalogue live; ctrl+c to stop"),
    }
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
/// `--features camera`. The open runs on its own thread with the baked
/// animation streaming meanwhile: Media Foundation can block indefinitely
/// when the device is wedged (e.g. a previous holder was force-killed), so
/// the feed must never hinge on `Camera::new` returning. The animation hands
/// over to the real camera the moment it opens; an open that errors leaves
/// the animation running. MJPEG cameras pass through untouched; other
/// formats are decoded to RGB and re-encoded to JPEG.
#[cfg(feature = "camera")]
fn spawn_webcam(c: &Client) {
    use nokhwa::pixel_format::RgbFormat;
    use nokhwa::utils::{CameraIndex, FrameFormat, RequestedFormat, RequestedFormatType};
    use nokhwa::Camera;

    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    // `Camera` is not Send, so the opener thread owns the whole capture loop;
    // the animation thread streams until `live` flips, then bows out.
    let live = Arc::new(AtomicBool::new(false));

    let vc = c.clone();
    let cam_live = live.clone();
    std::thread::spawn(move || {
        let requested = RequestedFormat::new::<RgbFormat>(
            RequestedFormatType::AbsoluteHighestFrameRate);
        let mut cam = match Camera::new(CameraIndex::Index(0), requested) {
            Ok(cam) => cam,
            Err(e) => {
                eprintln!("[rust] camera open failed ({e}); staying on the animation");
                return;
            }
        };
        if let Err(e) = cam.open_stream() {
            eprintln!("[rust] camera stream failed ({e}); staying on the animation");
            return;
        }
        cam_live.store(true, Ordering::SeqCst);
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

    let vc = c.clone();
    std::thread::spawn(move || {
        println!("[rust] webcam: opening the camera (animation until it's up)");
        let mut i = 0usize;
        while !live.load(Ordering::SeqCst) {
            vc.send_video("cam", WEBCAM[i % WEBCAM.len()]);
            i += 1;
            std::thread::sleep(std::time::Duration::from_millis(66));
        }
    });
}
