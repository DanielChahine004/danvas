//! A Rust remake of examples/catalogue.py — one column showcasing every native
//! danvas panel, authored from the danvas-source SDK, matched to the Python
//! catalogue panel-for-panel: same ids, card labels, colors, order, default
//! sizes (so the frontend auto-cascades them identically), and populated data
//! (table rows, a Plotly plot, a histogram, a *streaming* live plot, a
//! matplotlib image, a streaming audio tone + video). One binary broker + one
//! protocol, a Rust program drives the *same* full canvas.
//!
//!     danvasd --host 0.0.0.0 --port 8200
//!     cargo run --example catalogue -- 8200

use danvas_source::Client;
use serde_json::{json, Value};

const HISTOGRAM_FIG: &str = include_str!("assets/histogram_fig.json");
const LIVEPLOT_FIG: &str = include_str!("assets/liveplot_fig.json");
const IMAGE_SRC: &str = include_str!("assets/image_src.txt");
const FRAME: &[u8] = include_bytes!("rust_frame.jpg");

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

    // No x/y — like the .py, the frontend auto-cascades in registration order.
    // Sizes are the Python catalogue's resolved defaults, so the cascade matches.

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
        let mut xs: Vec<f64> = Vec::new();
        let mut yy: Vec<f64> = Vec::new();
        let mut t = 0.0_f64;
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

    // 12. Download
    c.panel("dl", "download").set("text", json!("Download hello.txt"))
        .titled("Download").at(524.0, 2507.0).size(200.0, 84.0).color(SLATE).show();

    // 13. Upload
    c.panel("up", "upload").set("text", json!("Choose a file"))
        .titled("Upload").at(748.0, 2602.0).size(240.0, 120.0).color(PLUM).show();

    // 14. File browser
    c.panel("fb", "file_browser").titled("File browser").at(1012.0, 2703.0).size(320.0, 420.0).color(ROSE).show();

    // 15. Webview
    c.panel("wv", "webview").set("url", json!("https://example.com"))
        .titled("Webview").at(80.0, 3451.0).size(800.0, 600.0).color(AMBER).show();

    // 16. Inspector
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

    // 18. Webcam — stream the test frame.
    c.video("cam").titled("Webcam").at(364.0, 4133.0).size(340.0, 280.0).color(SAGE).show();
    let vc = c.clone();
    std::thread::spawn(move || loop {
        vc.send_video("cam", FRAME);
        std::thread::sleep(std::time::Duration::from_millis(500));
    });

    // 19. Chat
    c.panel("room", "chat").titled("Chat").at(728.0, 4350.0).size(320.0, 400.0).color(ROSE).show();

    // 20. Custom — a self-contained click-counter button.
    c.panel("cust", "custom").prop("html", json!(
        "<style>button{font:14px sans-serif;padding:8px 16px;border-radius:6px;\
         border:0;background:#6b6bd4;color:#fff;cursor:pointer}</style>\
         <button onclick=\"this.textContent='Clicked ' + (++n)\">Click me</button>\
         <script>var n=0;</script>"))
        .titled("Custom").at(1072.0, 4500.0).size(380.0, 320.0).color(AMBER).show();

    println!("[rust] catalogue live on :{port}; ctrl+c to stop");
    std::thread::park();
}
