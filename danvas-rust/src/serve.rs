//! Self-contained serving: find and spawn the `danvasd` binary so a Rust
//! program can own its canvas end to end — the transliteration of
//! `danvas/remote.py::serve_via_broker`'s spawn half. A Rust process is still
//! a dial-in *source* (the broker owns the port, frontend, retention); this
//! just removes the "start danvasd by hand" step:
//!
//! ```no_run
//! let (_broker, c) = danvas::serve(8000, "rig").unwrap();
//! c.slider("servo", 0.0, 180.0, 90.0).show();
//! std::thread::park();   // _broker must stay alive — dropping it stops danvasd
//! ```

use crate::Client;

/// A running `danvasd` this process spawned (and owns: dropping the handle
/// stops it). Hold it for the program's lifetime.
pub struct Broker {
    child: Option<std::process::Child>,
    /// The port the broker is serving on.
    pub port: u16,
    host: String,
}

impl Broker {
    /// Spawn `danvasd` on 127.0.0.1:`port` and wait (≤15 s) for it to open
    /// the port. The binary is searched in the same spirit as Python's
    /// `_find_danvasd`: `$DANVASD`, beside the current executable, `$PATH`,
    /// then a repo checkout's `broker/target/{release,debug}`.
    pub fn spawn(port: u16) -> Result<Broker, String> {
        Self::spawn_on("127.0.0.1", port)
    }

    /// As [`spawn`](Self::spawn), binding `host` (e.g. `"0.0.0.0"` for LAN).
    pub fn spawn_on(host: &str, port: u16) -> Result<Broker, String> {
        let binary = find_danvasd().ok_or_else(|| {
            "danvasd (the serving binary) was not found. Fix by one of:\n\
             - point $DANVASD at a danvasd binary\n\
             - put danvasd on $PATH (or beside this executable)\n\
             - build it from a checkout: cargo build --release \
               --manifest-path broker/Cargo.toml"
                .to_string()
        })?;
        let mut child = std::process::Command::new(&binary)
            .args(["--port", &port.to_string(), "--host", host])
            .spawn()
            .map_err(|e| format!("could not launch {binary:?}: {e}"))?;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
        loop {
            if std::net::TcpStream::connect(("127.0.0.1", port)).is_ok() {
                break;
            }
            if let Ok(Some(status)) = child.try_wait() {
                return Err(format!("danvasd exited on startup ({status})"));
            }
            if std::time::Instant::now() > deadline {
                let _ = child.kill();
                return Err("danvasd never opened its port".into());
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        Ok(Broker { child: Some(child), port, host: host.to_string() })
    }

    /// The browser-facing address of this broker.
    pub fn url(&self) -> String {
        let host = if self.host == "0.0.0.0" { "127.0.0.1" } else { &self.host };
        format!("http://{host}:{}", self.port)
    }

    /// Open the default browser at the canvas (what Python's `serve()` does).
    pub fn open_browser(&self) {
        open_url(&self.url());
    }
}

impl Drop for Broker {
    fn drop(&mut self) {
        if let Some(child) = self.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// Serve a canvas from this process alone — the DEFAULT entry point of a
/// danvas program (dial-only [`Client::connect`] is the explicit opt-out):
/// spawn `danvasd` on `port`, or ATTACH to one already serving it (so two
/// danvas programs pointed at the same port compose on one canvas), dial in
/// as `label`, and open the browser (only when we spawned). Returns the
/// broker handle (keep it alive — dropping it stops a spawned server; an
/// attached one is not ours to stop) and the connected [`Client`].
pub fn serve(port: u16, label: &str) -> Result<(Broker, Client), String> {
    let attached = std::net::TcpStream::connect(("127.0.0.1", port)).is_ok();
    let broker = if attached {
        Broker { child: None, port, host: "127.0.0.1".into() }
    } else {
        Broker::spawn(port)?
    };
    let client = Client::connect(&format!("127.0.0.1:{port}"), label)?;
    if !attached {
        broker.open_browser();
    }
    Ok((broker, client))
}

/// Serve the canvas from THIS process with the broker linked in — the same
/// `danvasd` hub as [`serve`], run on a background thread instead of spawned
/// as a child (`cargo add danvas --features broker`; no binary to find,
/// nothing to leak). Attaches instead when something already serves `port`,
/// like [`serve`]. The trade: the canvas dies with this process — [`serve`]'s
/// out-of-process broker is what lets the UI survive a host crash/restart.
#[cfg(feature = "broker")]
pub fn serve_embedded(port: u16, label: &str) -> Result<(Broker, Client), String> {
    let attached = std::net::TcpStream::connect(("127.0.0.1", port)).is_ok();
    if !attached {
        std::thread::Builder::new()
            .name("danvasd-embedded".into())
            .spawn(move || {
                let rt = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("tokio runtime for the embedded broker");
                let cfg = danvasd::Config { port, ..Default::default() };
                if let Err(e) = rt.block_on(danvasd::run(cfg)) {
                    eprintln!("[danvas] embedded danvasd: {e}");
                }
            })
            .map_err(|e| format!("could not start the embedded broker: {e}"))?;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
        loop {
            if std::net::TcpStream::connect(("127.0.0.1", port)).is_ok() {
                break;
            }
            if std::time::Instant::now() > deadline {
                return Err("embedded danvasd never opened its port".into());
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
    }
    let client = Client::connect(&format!("127.0.0.1:{port}"), label)?;
    let broker = Broker { child: None, port, host: "127.0.0.1".into() };
    // Unlike serve(), no automatic browser: embedding leans programmatic
    // (tests, headless rigs). Call broker.open_browser() when wanted.
    Ok((broker, client))
}

/// Locate the danvasd binary: `$DANVASD` (explicit override), beside the
/// current executable (a shipped app), `$PATH`, then the repo-checkout build
/// dirs relative to the working directory (developer convenience).
fn find_danvasd() -> Option<std::path::PathBuf> {
    let exe = if cfg!(windows) { "danvasd.exe" } else { "danvasd" };
    if let Ok(p) = std::env::var("DANVASD") {
        let p = std::path::PathBuf::from(p);
        if p.is_file() {
            return Some(p);
        }
    }
    if let Ok(me) = std::env::current_exe() {
        if let Some(dir) = me.parent() {
            let p = dir.join(exe);
            if p.is_file() {
                return Some(p);
            }
        }
    }
    if let Ok(path) = std::env::var("PATH") {
        for dir in std::env::split_paths(&path) {
            let p = dir.join(exe);
            if p.is_file() {
                return Some(p);
            }
        }
    }
    for rel in ["broker/target/release", "broker/target/debug",
                "../broker/target/release", "../broker/target/debug"] {
        let p = std::path::PathBuf::from(rel).join(exe);
        if p.is_file() {
            return Some(p);
        }
    }
    None
}

fn open_url(url: &str) {
    let result = if cfg!(windows) {
        // `start` is a cmd builtin; the empty "" is its window-title slot.
        std::process::Command::new("cmd").args(["/c", "start", "", url]).spawn()
    } else if cfg!(target_os = "macos") {
        std::process::Command::new("open").arg(url).spawn()
    } else {
        std::process::Command::new("xdg-open").arg(url).spawn()
    };
    let _ = result;
}
