//! Media over the binary envelope, from Rust: register a native video panel,
//! stream JPEG frames to it, and echo any binary a browser sends back.
//!
//!     danvasd --port 8080
//!     cargo run --example media -- 8080
//!
//! A browser sees the video panel light up; anything it sendBinary()s to the
//! panel comes back as the next "frame" — proving both directions of the
//! binary media envelope cross the hub from a Rust peer.

use danvas_source::Client;

fn main() {
    let port = std::env::args().nth(1).unwrap_or_else(|| "8080".into());
    let c = Client::connect(&format!("127.0.0.1:{port}"), "media-rust")
        .expect("connect to the hub");

    c.video("cam").at(40.0, 40.0).size(360.0, 300.0).show();

    // Echo: a browser's canvas.sendBinary(...) to this panel comes back as the
    // next video frame — the round trip proves both envelope directions.
    let cc = c.clone();
    c.on_binary("cam", move |bytes| {
        println!("[rust] got {} bytes from a browser; re-streaming", bytes.len());
        cc.send_video("cam", bytes);
    });

    // Stream a few marker "frames" so a fresh browser sees binary code 1 arrive.
    for i in 0..5u8 {
        c.send_video("cam", &[0xFF, 0xD8, 0xFF, i]); // JPEG SOI + a marker byte
        std::thread::sleep(std::time::Duration::from_millis(120));
    }

    println!("[rust] media source live on :{port}; ctrl+c to stop");
    std::thread::park();
}
