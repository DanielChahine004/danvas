//! Managed shapes + arrows authored in Rust: two boxes, an arrow between them,
//! and a live edit — a Python-owned diagram, drawn from a Rust peer.
//!
//!     danvasd --port 8080
//!     cargo run --example shapes -- 8080

use danvas::Client;
use serde_json::json;

fn main() {
    let port = std::env::args().nth(1).unwrap_or_else(|| "8080".into());
    let c = Client::connect(&format!("127.0.0.1:{port}"), "shapes-rust")
        .expect("connect to the hub");

    c.geo("box_a", "rectangle").at(40.0, 40.0).size(160.0, 90.0)
        .prop("color", json!("blue")).prop("fill", json!("semi"))
        .prop("text", json!("A")).show();
    c.geo("box_b", "rectangle").at(40.0, 200.0).size(160.0, 90.0)
        .prop("text", json!("B")).show();
    c.arrow("a2b", "box_a", "box_b")
        .prop("color", json!("blue")).prop("dash", json!("dashed"))
        .prop("text", json!("A->B")).show();

    // A live edit: recolour + nudge box A (folds in for reconnect replay).
    std::thread::sleep(std::time::Duration::from_millis(250));
    c.update_shape("box_a",
        [("color".to_string(), json!("orange")), ("x".to_string(), json!(80.0))]);

    println!("[rust] shapes live on :{port}; ctrl+c to stop");
    std::thread::park();
}
