"""Expose a locally-bound canvas to the public internet through a tunnel.

The frontend dials its WebSocket from the page's own origin (see
``frontend/src/bridge.js``: it picks ``wss``/``ws`` from ``location.protocol``
and targets ``location.host``), so a canvas served on ``127.0.0.1`` is reachable
through any HTTPS tunnel with **no client changes**. This module shells out to a
tunnel binary, scrapes the public URL it announces, and hands it back.

``cloudflared`` (the default) needs no signup, shows visitors no interstitial,
and speaks WebSockets â€” the smoothest "just open the link" experience.
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
    locations â€” covering a shell open since before a Windows install updated the
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
            f"PATH â€” {spec['install']}. If you just installed it, open a new "
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
            f"{provider} exited before announcing a URL â€” check it is installed "
            f"and working ({spec['install']})"
        )
    return Tunnel(proc, found["url"], provider)
