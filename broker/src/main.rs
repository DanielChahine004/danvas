//! The `danvasd` binary: a thin argv/env parser over the library crate.
//! All behaviour lives in lib.rs (`danvasd::run`), so the danvas Rust SDK's
//! `broker` feature can link the same hub in-process instead of spawning
//! this executable — one implementation, two delivery shapes.

use danvasd::Config;

#[tokio::main]
async fn main() {
    let mut cfg = Config::default();
    let as_bool = |v: Option<String>| v.map(|s| s == "1" || s == "true");
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--port" => {
                if let Some(p) = args.next().and_then(|v| v.parse().ok()) {
                    cfg.port = p;
                }
            }
            "--host" => {
                if let Some(h) = args.next() {
                    let h = if h == "localhost" { "127.0.0.1".into() } else { h };
                    if let Ok(ip) = h.parse() {
                        cfg.host = ip;
                    }
                }
            }
            "--password" => cfg.password = args.next().filter(|s| !s.is_empty()),
            "--merge-server" => {
                cfg.merge_server = args.next().filter(|s| !s.is_empty())
            }
            "--self-url" => cfg.self_url = args.next().filter(|s| !s.is_empty()),
            "--ui-inspector" => cfg.ui_inspector = as_bool(args.next()),
            "--ui-graveyard" => cfg.ui_graveyard = as_bool(args.next()),
            "--ui-hosting" => cfg.ui_hosting = as_bool(args.next()),
            "--cursors" => cfg.cursors = as_bool(args.next()),
            _ => {}
        }
    }
    cfg.role_passwords = std::env::var("DANVAS_ROLE_PASSWORDS")
        .ok()
        .map(|v| {
            v.split(',')
                .filter_map(|pair| {
                    pair.split_once('=')
                        .map(|(r, p)| (r.trim().to_string(), p.to_string()))
                })
                .collect()
        })
        .unwrap_or_default();
    cfg.ledger = std::env::var("DANVAS_LEDGER").ok();
    if let Err(e) = danvasd::run(cfg).await {
        eprintln!("[danvasd] {e}");
        std::process::exit(1);
    }
}
