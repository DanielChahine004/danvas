//! Canvas state from Rust: camera/chrome (set_view), shared React assets
//! (define/style), z-order, free-form ink (on_draw), and a snapshot round-trip.
//!
//!     danvasd --port 8080
//!     cargo run --example canvas_state -- 8080

use danvas_source::Client;
use serde_json::json;

fn main() {
    let port = std::env::args().nth(1).unwrap_or_else(|| "8080".into());
    let c = Client::connect(&format!("127.0.0.1:{port}"), "state-rust")
        .expect("connect to the hub");

    // Shared React component + global stylesheet (replay before registers).
    c.define("Pill", "function Pill({ children }){ return <b className='pill'>{children}</b> }");
    c.style(".pill{ color:#e05c7a }");

    // Camera/chrome — folds into the hub view, baked into every welcome.
    c.set_view([("zoom".to_string(), json!(1.5)), ("locked".to_string(), json!(true))]);

    c.label("hdr", "canvas state, authored in Rust").at(40.0, 40.0).show();

    // z-order is a live op (not replayed), so raise the header whenever a viewer
    // joins — the browser sees the order frame live.
    let ccp = c.clone();
    c.on_presence(move |_m| ccp.to_front("hdr"));

    // React to viewers drawing free-form ink.
    let cc = c.clone();
    c.on_draw(move |_m| cc.chat("someone drew"));

    // A snapshot round-trip: tapping "snap" asks a browser for the document.
    c.button("snap").set("text", json!("snapshot")).at(40.0, 120.0).show();
    let cc2 = c.clone();
    c.on_input("snap", move |_p| {
        let back = cc2.clone();
        cc2.get_snapshot(move |reply| {
            let n = reply.get("data").and_then(|d| d.get("records"))
                .and_then(|r| r.as_array()).map(|a| a.len()).unwrap_or(0);
            back.chat(&format!("snapshot has {n} records"));
        });
    });

    println!("[rust] canvas-state peer live on :{port}; ctrl+c to stop");
    std::thread::park();
}
