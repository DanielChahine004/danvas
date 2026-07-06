//! Interaction + multiuser from Rust: answer requests, watch the roster, chat.
//!
//!     danvasd --port 8080
//!     cargo run --example interact -- 8080
//!
//! A browser can `await canvas.request({n})` the compute panel and get an answer
//! from Rust; as viewers join, the Rust peer posts the new headcount to chat.

use danvas::Client;
use serde_json::json;

fn main() {
    let port = std::env::args().nth(1).unwrap_or_else(|| "8080".into());
    let c = Client::connect(&format!("127.0.0.1:{port}"), "interact-rust")
        .expect("connect to the hub");

    // Request/response: the closure's return value resolves the browser's Promise.
    c.button("compute").set("text", json!("compute in Rust")).at(40.0, 40.0).show();
    c.on_request("compute", |data| {
        let n = data.get("n").and_then(|v| v.as_i64()).unwrap_or(0);
        json!({ "doubled": n * 2 })
    });

    // Presence: post the new headcount to chat whenever the roster changes.
    let cc = c.clone();
    c.on_presence(move |m| {
        let n = m.get("count").and_then(|v| v.as_i64()).unwrap_or(0);
        cc.chat(&format!("roster now {n}"));
    });

    // Chat: observe what viewers say.
    c.on_chat(|m| {
        if let Some(t) = m.get("text").and_then(|v| v.as_str()) {
            println!("[rust] chat: {t}");
        }
    });

    println!("[rust] interactive peer live on :{port}; ctrl+c to stop");
    std::thread::park();
}
