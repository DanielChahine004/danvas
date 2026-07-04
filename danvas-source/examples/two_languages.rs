//! Two languages, one canvas — the polyglot proof.
//!
//! A Rust process joins a running canvas and does everything a Python
//! `danvas.connect()` process can: authors NATIVE panels, edits a *peer's*
//! panel by name (the shared property plane), reacts to a peer's events, and
//! streams its own state. Point it at any hub (danvasd or `python -m
//! danvas.merge` or a Python `canvas.serve()`):
//!
//!     cargo run --example two_languages -- 8080
//!
//! The canvas then has this Rust process's slider + label alongside whatever
//! Python (or a browser) put there — and interactions cross the language line.

use danvas_source::Client;

fn main() {
    let port = std::env::args().nth(1).unwrap_or_else(|| "8080".into());
    let c = Client::connect(&format!("127.0.0.1:{port}"), "rust-peer")
        .expect("connect to the hub");
    println!("[rust] connected as a source");

    // --- our own native panels (rendered from the shared template asset) ---
    c.slider("rust_servo", 0.0, 180.0, 90.0).at(40.0, 40.0).show();
    c.label("rust_status", "idle — rust").at(40.0, 170.0).show();

    // react to a browser dragging OUR slider — compute in Rust, stream back
    let cc = c.clone();
    c.on_input("rust_servo", move |p| {
        let v = p.get("value").cloned().unwrap_or(serde_json::json!(0));
        println!("[rust] servo -> {v}");
        cc.update("rust_status", "post",
                  serde_json::json!(format!("servo {v} — computed in rust")));
    });

    // --- the shared plane: reach a PEER's panel named "peer" ---------------
    // Wait for it to appear on the canvas (another process owns it), then edit
    // it (set_props) and subscribe to its events — reacting in Rust to a panel
    // this process didn't create.
    let cc = c.clone();
    std::thread::spawn(move || {
        loop {
            if let Some(id) = cc.find("peer") {
                println!("[rust] found peer panel {id}; retuning + subscribing");
                cc.set_props(&id, [("max".to_string(), serde_json::json!(42))]);
                let inner = cc.clone();
                cc.subscribe(&id, move |p| {
                    println!("[rust] peer fired: {p}");
                    inner.update("rust_status", "post",
                                 serde_json::json!("reacted to peer in rust"));
                });
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
    });

    println!("[rust] canvas live; ctrl+c to stop");
    std::thread::park();
}
