"""Client-side dial-in helpers: normalise a source spec, probe it for auth, and
run its password flow.

These are pure-stdlib (http.client / urllib) and used by BOTH a dial-in source
(``danvas.SourceClient`` / ``danvas.connect``) and the merge hub that dials out.
They live here — not in merge.py, which pulls the FastAPI server stack — so a
light client/broker install can dial into a canvas without that dependency. One
definition, every caller.
"""

import re
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import quote, urlsplit


def _parse_source(spec):
    """Normalise a source spec to ``(ws_uri, http_parts, label)``.

    ``http_parts`` is ``(scheme, host, port, is_tls)`` for the source's HTTP
    origin -- used to probe whether the canvas is password-protected and to run
    its ``/__auth__`` login flow. Accepts a bare port (``8001`` / ``":8001"``),
    a ``host:port``, or a full URL (``https://x.loca.lt`` / ``wss://host/ws``).
    """
    if isinstance(spec, int):
        ws_uri, label = f"ws://localhost:{spec}/ws", f"localhost:{spec}"
    else:
        text = str(spec).strip()
        if "://" in text:
            scheme, _, rest = text.partition("://")
            scheme = {"http": "ws", "https": "wss"}.get(scheme.lower(), scheme.lower())
            rest = rest.rstrip("/")
            label = rest.split("/", 1)[0]
            if not rest.endswith("/ws"):
                rest += "/ws"
            ws_uri = f"{scheme}://{rest}"
        else:
            if text.startswith(":"):
                text = "localhost" + text
            if ":" in text:
                host, _, port = text.rpartition(":")
                host = host or "localhost"
            else:
                host, port = "localhost", text
            ws_uri, label = f"ws://{host}:{int(port)}/ws", f"{host}:{port}"
    u = urlsplit(ws_uri)
    tls = u.scheme == "wss"
    host = u.hostname or "localhost"
    port = u.port or (443 if tls else 80)
    http_parts = ("https" if tls else "http", host, port, tls)
    return ws_uri, http_parts, label


def _http_conn(http_parts, timeout=6):
    _scheme, host, port, tls = http_parts
    cls = HTTPSConnection if tls else HTTPConnection
    return cls(host, port, timeout=timeout)


def _probe_source(http_parts):
    """Classify a source (blocking; run in an executor): ``"open"`` (reachable, no
    auth), ``"auth"`` (password-protected -> HTTP 401), or ``"offline"``."""
    try:
        conn = _http_conn(http_parts)
        conn.request("GET", "/")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return "auth" if resp.status == 401 else "open"
    except Exception:
        return "offline"


def _authenticate(http_parts, password):
    """Run a source's ``/__auth__`` password flow (blocking; run in an executor).

    Returns the ``pc_session`` cookie token on success, or ``None`` on a wrong
    password / unreachable host. The canvas replies to a correct password with a
    303 redirect carrying ``Set-Cookie: pc_session=...``.
    """
    try:
        conn = _http_conn(http_parts)
        body = "password=" + quote(password or "", safe="")
        conn.request("POST", "/__auth__", body=body,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        set_cookie = resp.getheader("Set-Cookie") or ""
        resp.read()
        conn.close()
        m = re.search(r"pc_session=([^;]+)", set_cookie)
        return m.group(1) if m else None
    except Exception:
        return None
