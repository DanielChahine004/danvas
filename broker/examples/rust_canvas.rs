//! A danvas canvas authored in Rust — no Python anywhere.
//!
//! Dial-in source against any hub (danvasd or `python -m danvas.merge`):
//! registers a NATIVE slider + label from the language-neutral templates
//! (danvas/templates/components.json, embedded at compile time), reacts to
//! browser input, streams updates back. The whole stack is then
//! `danvasd.exe` + this program + a browser.
//!
//!     danvasd --port 8080
//!     cargo run --example rust_canvas -- --port 8080
//!     # open http://127.0.0.1:8080
//!
//! This is the seed of the `danvas-source` crate: connect, replay-on-
//! reconnect, heartbeat, register_template, on_input — the whole dial-in
//! role in ~150 lines (PROTOCOL.md §dial-in sources).

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

/// The same asset the Python SDK reads — native panel register shapes.
const TEMPLATES: &str = include_str!("../../danvas/templates/components.json");

fn template_register(
    templates: &Value,
    kind: &str,
    id: &str,
    data_overrides: Value,
    x: f64,
    y: f64,
) -> Value {
    let tpl = &templates["templates"][kind];
    let mut props = tpl["props"].clone();
    let mut data = tpl["data"].clone();
    if let (Some(d), Some(o)) = (data.as_object_mut(), data_overrides.as_object()) {
        for (k, v) in o {
            d.insert(k.clone(), v.clone());
        }
    }
    let p = props.as_object_mut().unwrap();
    p.insert("data".into(), Value::String(data.to_string()));
    p.insert("label".into(), Value::String(id.to_string()));
    json!({"type": "register", "id": id, "name": id,
           "component": tpl["component"], "props": props, "x": x, "y": y})
}

#[tokio::main]
async fn main() {
    let mut port: u16 = 8080;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        if a == "--port" {
            if let Some(p) = args.next().and_then(|v| v.parse().ok()) {
                port = p;
            }
        }
    }
    let templates: Value = serde_json::from_str(TEMPLATES).expect("templates");
    let uri = format!("ws://127.0.0.1:{port}/ws?source=1&label=rust-canvas");

    // Reconnect loop: replay our panels on every (re)connect — the dial-in
    // role's one obligation (the hub replays to browsers; we replay to it).
    loop {
        let Ok((stream, _)) = connect_async(&uri).await else {
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            continue;
        };
        println!("[rust-canvas] connected to hub on :{port}");
        let (mut sink, mut read) = stream.split();

        let servo = template_register(
            &templates, "slider", "servo",
            json!({"min": 0, "max": 180, "default": 90, "value": 90}),
            40.0, 40.0);
        let status = template_register(
            &templates, "label", "status", json!({"text": "idle — rust"}),
            40.0, 170.0);
        for frame in [&servo, &status] {
            let _ = sink.send(Message::Text(frame.to_string())).await;
        }

        let mut heartbeat =
            tokio::time::interval(std::time::Duration::from_secs(10));
        loop {
            tokio::select! {
                _ = heartbeat.tick() => {
                    if sink.send(Message::Text(
                        json!({"type": "heartbeat"}).to_string())).await.is_err() {
                        break;
                    }
                }
                msg = read.next() => {
                    let Some(Ok(Message::Text(text))) = msg else { break };
                    let Ok(frame) = serde_json::from_str::<Value>(&text) else {
                        continue;
                    };
                    // on_input for OUR slider: compute in Rust, stream back.
                    if frame["type"] == "input" && frame["id"] == "servo" {
                        let v = frame["payload"]["value"].as_f64().unwrap_or(0.0);
                        println!("[rust-canvas] servo -> {v}");
                        let _ = sink.send(Message::Text(
                            json!({"type": "update", "id": "status",
                                   "payload": {"post":
                                       format!("servo at {v} — computed in rust")}})
                            .to_string())).await;
                    }
                }
            }
        }
        println!("[rust-canvas] hub gone; retrying (panels held by retention)");
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    }
}
