"""Expose a locally-bound canvas to the public internet through a tunnel.

The frontend dials its WebSocket from the page's own origin (see
``frontend/src/bridge.js``: it picks ``wss``/``ws`` from ``location.protocol``
and targets ``location.host``), so a canvas served on ``127.0.0.1`` is reachable
through any HTTPS tunnel with **no client changes**. This module shells out to a
tunnel binary, scrapes the public URL it announces, and hands it back.

``cloudflared`` (the default) needs no signup, shows visitors no interstitial,
and speaks WebSockets — the smoothest "just open the link" experience.
``localtunnel`` (``lt`` / ``npx localtunnel``) is also supported but needs Node
and shows first-time visitors an IP-password reminder page.

Security note: a tunnel hands the world your *loopback* bind, so the assumption
that ``127.0.0.1`` is safe no longer holds. Callers that gate remote code
execution (``Canvas.serve``) must treat ``tunnel=True`` as a public bind.
"""

import os
import re
import shutil
import subprocess
import threading


def _lt_cmd(binary, port):
    """localtunnel invocation: ``lt --port N`` or ``npx localtunnel --port N``."""
    stem = os.path.splitext(os.path.basename(binary))[0].lower()
    if stem == "npx":
        return [binary, "localtunnel", "--port", str(port)]
    return [binary, "--port", str(port)]


def _pycloudflared_binary():
    """Path to a cloudflared binary managed by the optional ``pycloudflared``.

    Installed via the ``danvas[tunnel]`` extra, ``pycloudflared`` downloads and
    caches the cloudflared binary, so ``tunnel=True`` needs no manual install.
    Returns the cached path (downloading it on first use), or ``None`` if the
    package isn't installed. Errors during download are swallowed so resolution
    falls through to the not-found message.
    """
    try:
        from pycloudflared.util import download, get_info
    except ImportError:
        return None
    try:
        info = get_info()
        if os.path.isfile(info.executable):
            return info.executable
        return download(info)  # ~20 MB, first use only; shows a progress bar
    except Exception:
        return None


def _resolve_binary(spec):
    """Locate a provider's executable, falling back past a stale PATH.

    In order: ``shutil.which`` (the normal case); the installer's default
    locations — covering a shell open since before a Windows install updated the
    machine PATH; then an optional ``acquire`` hook (e.g. ``pycloudflared`` from
    the ``danvas[tunnel]`` extra, which downloads the binary on demand).
    Returns the full path, or ``None`` if nothing resolves.
    """
    for name in spec["binaries"]:
        found = shutil.which(name)
        if found:
            return found
    for path in spec.get("fallback_paths", ()):
        expanded = os.path.expandvars(path)
        if os.path.isfile(expanded):
            return expanded
    acquire = spec.get("acquire")
    return acquire() if acquire else None

# One entry per provider: which executables can drive it, how to build the
# command for a port, and the regex that matches the public URL it prints.
_PROVIDERS = {
    "cloudflared": {
        "binaries": ["cloudflared"],
        "cmd": lambda b, port: [b, "tunnel", "--url", f"http://localhost:{port}"],
        "pattern": re.compile(r"https://[-\w]+\.trycloudflare\.com"),
        # Default install dirs, probed when PATH lookup fails (e.g. a shell open
        # since before the installer ran). The winget/MSI build lands here.
        "fallback_paths": (
            r"%ProgramFiles(x86)%\cloudflared\cloudflared.exe",
            r"%ProgramFiles%\cloudflared\cloudflared.exe",
        ),
        # Last resort: let the optional pycloudflared package fetch the binary.
        "acquire": _pycloudflared_binary,
        "install": "pip install 'danvas[tunnel]' (auto-downloads cloudflared), "
                   "or install it yourself (brew install cloudflared, "
                   "winget install --id Cloudflare.cloudflared, or see "
                   "https://developers.cloudflare.com/cloudflare-one/connections/"
                   "connect-networks/downloads/)",
    },
    "localtunnel": {
        # Prefer a globally installed `lt`; fall back to `npx localtunnel`.
        "binaries": ["lt", "npx"],
        "cmd": _lt_cmd,
        "pattern": re.compile(r"https://[-\w]+\.loca\.lt"),
        "install": "npm install -g localtunnel (or have npx on PATH)",
    },
}


class Tunnel:
    """A live tunnel process and the public ``url`` it exposes.

    Call :meth:`stop` to tear the tunnel down (also invoked automatically when
    the owning ``Canvas``/``Merge`` server stops).
    """

    def __init__(self, proc, url, provider):
        self._proc = proc
        self.url = url
        self.provider = provider

    def stop(self):
        """Terminate the tunnel subprocess (idempotent)."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# === the tunnel keeper ========================================================
# Quick tunnels mint a NEW URL every time the tunnel process restarts — so a
# tunnel owned by the serving script invalidates the link you shared on every
# code iteration. The keeper is a small DETACHED process that owns the tunnel
# for a port and outlives the scripts that use it: serve(tunnel=True) finds a
# live keeper and reuses its URL, or spawns one. Deliberately not an "owned"
# child (contrast _procown): surviving the script is its entire job. Stop it
# with `python -m danvas.tunnel --stop --port N`.

def _state_path(port):
    import tempfile
    return os.path.join(tempfile.gettempdir(), f"danvas-tunnel-{port}.json")


def _read_state(port):
    import json
    try:
        with open(_state_path(port), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _pid_alive(pid):
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def ensure_tunnel(port, provider="cloudflared", timeout=45):
    """A tunnel for ``port`` whose URL is STABLE across script restarts.

    Reuses a live keeper's URL if one already holds this port; otherwise
    spawns a detached keeper (`python -m danvas.tunnel --port N`) and waits
    for it to announce. The returned :class:`Tunnel`'s ``stop()`` is a no-op
    — the keeper owns the process; stop it explicitly with
    ``python -m danvas.tunnel --stop --port N``.
    """
    import json
    import sys
    import time

    st = _read_state(port)
    if st and st.get("provider") == provider and _pid_alive(st.get("pid", -1)):
        return Tunnel(None, st["url"], provider)
    path = _state_path(port)
    try:
        os.remove(path)   # stale state from a dead keeper
    except OSError:
        pass
    log = open(path + ".log", "ab")
    kwargs = {}
    if os.name == "nt":
        DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # no console, own group
        kwargs["creationflags"] = DETACHED
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, "-m", "danvas.tunnel",
         "--port", str(port), "--provider", provider],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        **kwargs)
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _read_state(port)
        if st and st.get("provider") == provider \
                and _pid_alive(st.get("pid", -1)):
            return Tunnel(None, st["url"], provider)
        time.sleep(0.3)
    raise RuntimeError(
        f"the tunnel keeper never announced a URL for port {port} — see "
        f"{path}.log")


def _run_keeper(port, provider):
    """The keeper body: own the tunnel, publish state, reopen if it dies."""
    import json
    import time

    path = _state_path(port)

    def publish(t):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"url": t.url, "pid": os.getpid(), "port": port,
                       "provider": provider}, f)
        print(f"[danvas.tunnel] {t.url} -> localhost:{port} "
              f"(keeper pid {os.getpid()})", flush=True)

    t = open_tunnel(port, provider=provider)
    publish(t)
    try:
        while True:
            time.sleep(2)
            if t._proc is not None and t._proc.poll() is not None:
                # The tunnel process died (network blip, provider restart):
                # reopen. Quick tunnels mint a new URL here — unavoidable —
                # but scripts restarting no longer do.
                t = open_tunnel(port, provider=provider)
                publish(t)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
        t.stop()


def _stop_keeper(port):
    st = _read_state(port)
    if not st or not _pid_alive(st.get("pid", -1)):
        print(f"no live tunnel keeper for port {port}")
        return
    pid = int(st["pid"])
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)
    try:
        os.remove(_state_path(port))
    except OSError:
        pass
    print(f"stopped tunnel keeper for port {port} (pid {pid})")


def open_tunnel(port, provider="cloudflared", timeout=30):
    """Start a tunnel to ``localhost:port`` and return a :class:`Tunnel`.

    Blocks until the provider announces its public URL (or ``timeout`` seconds
    elapse). Raises ``RuntimeError`` if the provider binary is missing, exits
    early, or never announces a URL; ``ValueError`` for an unknown provider.
    """
    spec = _PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(
            f"unknown tunnel provider {provider!r}; choose from "
            f"{', '.join(sorted(_PROVIDERS))}"
        )
    binary = _resolve_binary(spec)
    if binary is None:
        raise RuntimeError(
            f"tunnel provider {provider!r} needs one of {spec['binaries']} on "
            f"PATH — {spec['install']}. If you just installed it, open a new "
            f"terminal so PATH refreshes."
        )
    proc = subprocess.Popen(
        spec["cmd"](binary, port),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    pattern = spec["pattern"]
    found = {}
    done = threading.Event()

    def _scan():
        # Keep draining output even after the URL is found so the OS pipe buffer
        # never fills and stalls the tunnel (cloudflared logs continuously).
        for line in proc.stdout:
            if "url" not in found:
                m = pattern.search(line)
                if m:
                    found["url"] = m.group(0)
                    done.set()
        done.set()  # stdout closed: process ended

    threading.Thread(target=_scan, daemon=True).start()
    got = done.wait(timeout)
    if "url" not in found:
        proc.terminate()
        if not got:
            raise RuntimeError(
                f"timed out after {timeout}s waiting for {provider} to report a "
                f"public URL"
            )
        raise RuntimeError(
            f"{provider} exited before announcing a URL — check it is installed "
            f"and working ({spec['install']})"
        )
    return Tunnel(proc, found["url"], provider)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="danvas tunnel keeper: hold a public tunnel for a port "
                    "so its URL survives script restarts")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--provider", default="cloudflared")
    ap.add_argument("--stop", action="store_true",
                    help="stop the keeper for --port")
    ap.add_argument("--status", action="store_true",
                    help="print the keeper's URL for --port, if alive")
    a = ap.parse_args()
    if a.stop:
        _stop_keeper(a.port)
    elif a.status:
        _st = _read_state(a.port)
        if _st and _pid_alive(_st.get("pid", -1)):
            print(_st["url"])
        else:
            print(f"no live tunnel keeper for port {a.port}")
    else:
        _run_keeper(a.port, a.provider)