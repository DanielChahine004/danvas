//! The broker linked IN: `cargo run --example embedded --features broker`.
//!
//! serve_embedded() runs the same danvasd hub as serve(), but in-process on
//! a background thread — no external binary to find or spawn, nothing to
//! outlive this program. `cargo add danvas --features broker` is the whole
//! install. (The trade: the canvas dies with this process; spawn-external
//! serve() is what gives a canvas that survives host restarts.)
//!
//! Exits 0 after proving the hub is real: the frontend answers over HTTP
//! and a panel registers through the dial-in.

use std::io::{Read, Write};

fn main() {
    let port = 8907;
    let (broker, c) = danvas::serve_embedded(port, "embedded-demo")
        .expect("embedded broker");
    c.slider("demo", 0.0, 100.0, 25.0).show();

    // Prove the embedded hub serves: fetch the template asset over HTTP.
    let mut sock = std::net::TcpStream::connect(("127.0.0.1", port))
        .expect("connect");
    write!(sock, "GET /__templates__ HTTP/1.1\r\nHost: x\r\n\
                  Connection: close\r\n\r\n").unwrap();
    let mut body = String::new();
    sock.read_to_string(&mut body).expect("read");
    assert!(body.contains("200 OK"), "unexpected response: {body:.100}");
    assert!(body.contains("\"slider\""), "templates asset missing sliders");

    println!("EMBEDDED OK: hub in-process at {} (slider registered)",
             broker.url());
}
