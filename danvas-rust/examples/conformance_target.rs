//! The Rust target for the source-SDK conformance suite
//! (tests/test_sdk_conformance.py; the Python twin and the normative behavior
//! table live in tests/sdk_conformance_target.py).
//!
//!     cargo build --example conformance_target
//!     # then the suite spawns: target/debug/examples/conformance_target <port>

use danvas::Client;
use serde_json::json;

fn main() {
    let port = std::env::args().nth(1).expect("usage: conformance_target <port>");
    let c = Client::connect(&format!("127.0.0.1:{port}"), "ct")
        .expect("connect to danvasd");

    c.label("lbl", "hello").at(10.0, 10.0).show();

    c.slider("sld", 0.0, 100.0, 10.0).at(10.0, 110.0).show();
    let lc = c.clone();
    c.on_input("sld", move |p| {
        let v = p.get("value").and_then(|v| v.as_f64()).unwrap_or(0.0);
        lc.update("lbl", "post", json!(format!("v={v:.0}")));
    });

    c.button("ask").at(10.0, 210.0).show();
    c.on_request("ask", |data| {
        let ping = data.get("ping").and_then(|v| v.as_i64()).unwrap_or(0);
        json!({"pong": ping + 1})
    });

    c.panel("dl", "download").at(10.0, 310.0).show();
    c.on_download("dl", || ("hello.txt".into(), b"conformance-bytes\n".to_vec()));

    c.panel("up", "upload").at(10.0, 410.0).show();
    let uc = c.clone();
    c.on_upload("up", move |f| {
        uc.update("lbl", "post", json!(format!("up={}:{}", f.name, f.size)));
    });

    c.custom("bin", "<b>bin</b>", "", "").at(10.0, 510.0).show();
    let bc = c.clone();
    c.on_binary("bin", move |data| {
        bc.update("lbl", "post", json!(format!("bin={}", data.len())));
        bc.send_media(3, "bin", data); // CUSTOM: echo the bytes back
    });

    c.video("cam").at(10.0, 610.0).show();
    c.button("ctl").at(10.0, 710.0).show();
    let vc = c.clone();
    c.on_input("ctl", move |_| {
        vc.send_video("cam", b"\xff\xd8conformance-jpeg");
    });

    println!("[ct] conformance target live on :{port}");
    std::thread::park();
}
