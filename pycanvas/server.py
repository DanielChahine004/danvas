"""FastAPI app: WebSocket endpoint + static serving of the built frontend."""

import asyncio
import os
import secrets
import socket
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Cookie that carries a viewer's auth session token once they pass the password
# page. The token is random per session and validated server-side, so the
# password itself never rides in a cookie.
_AUTH_COOKIE = "pc_session"


def _dist_dir():
    """Locate the built frontend, both in-source and inside a baked executable.

    A PyInstaller build bundles the frontend under ``pcframe/dist`` in the
    extraction dir (``sys._MEIPASS``) — deliberately *not* under ``pycanvas/``,
    which would shadow the real package as a namespace dir and break
    ``import pycanvas``. In a normal install it lives next to this module.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "pcframe", "dist")
    return os.path.join(os.path.dirname(__file__), "frontend", "dist")


DIST_DIR = _dist_dir()

# Disable uvicorn's WebSocket keepalive ping. The ping is an independent writer
# the app can't serialize, so under backpressure (e.g. a high-rate video feed
# filling the socket buffer) it collides with an in-flight send and trips an
# assertion in the websockets legacy drain. Dead peers are still dropped when the
# next broadcast send to them fails, so the ping isn't needed here.
_WS_OPTS = {"ws_ping_interval": None, "ws_ping_timeout": None}


def _lan_ip():
    """Best-effort LAN IP of this machine — the address other devices dial.

    Opens a UDP socket toward a public address to discover which local interface
    routes outward, then reads that interface's IP. No packets are actually sent,
    and it works offline as long as a network interface is up. Returns ``None``
    if no route can be determined.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _announce(host, port):
    """Print where the canvas is reachable, including a LAN URL for other devices.

    A ``127.0.0.1`` / ``localhost`` bind is local-only, so just the local URL is
    shown. Any other bind (``0.0.0.0``, ``""``, a specific IP) is reachable from
    the network, so the machine's LAN URL is printed too — that's the address to
    open on a phone/another computer on the same Wi-Fi.
    """
    local = f"http://127.0.0.1:{port}"
    if host in ("127.0.0.1", "localhost"):
        print(f"PyCanvas serving at {local}  (Ctrl+C to stop)")
        return
    print("PyCanvas serving  (Ctrl+C to stop):")
    print(f"  local:   {local}")
    ip = _lan_ip()
    if ip:
        print(f"  network: http://{ip}:{port}"
              "   <- open this on another device on the same Wi-Fi")
    else:
        print(f"  network: http://<this-machine-ip>:{port}"
              "   <- open this on another device on the same Wi-Fi")


class _FrontendStatic(StaticFiles):
    """Serve the built frontend, but never let the browser cache index.html.

    The JS/CSS bundles are content-hashed (safe to cache forever), but the HTML
    that points at them changes every rebuild. Without this, a browser holding a
    stale index.html requests a bundle hash that no longer exists -> blank/grey
    page after a rebuild. Forcing revalidation of the HTML avoids that.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path in (".", "", "index.html") or path.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


def _login_page(error=False):
    """The minimal password prompt shown before an unauthenticated view loads."""
    msg = ("<p class='err'>Wrong password — try again.</p>" if error else "")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>PyCanvas</title><style>"
        "html,body{height:100%;margin:0;font-family:system-ui,sans-serif;"
        "background:#0f172a;color:#e2e8f0}"
        ".wrap{height:100%;display:flex;align-items:center;justify-content:center}"
        "form{background:#1e293b;padding:28px 28px 24px;border-radius:12px;"
        "border:1px solid #334155;min-width:260px}"
        "h1{font-size:18px;margin:0 0 4px}p{color:#94a3b8;font-size:13px;margin:0 0 16px}"
        ".err{color:#f87171}"
        "input{width:100%;box-sizing:border-box;padding:9px 10px;border-radius:7px;"
        "border:1px solid #475569;background:#0f172a;color:#e2e8f0;font-size:14px}"
        "button{margin-top:12px;width:100%;padding:9px;border:0;border-radius:7px;"
        "background:#3b82f6;color:#fff;font-size:14px;cursor:pointer}"
        "button:hover{background:#2563eb}</style></head><body><div class='wrap'>"
        "<form method='post' action='/__auth__'>"
        "<h1>PyCanvas</h1><p>This canvas is password protected.</p>"
        f"{msg}"
        "<input type='password' name='password' placeholder='Password' autofocus>"
        "<button type='submit'>Enter</button></form></div></body></html>"
    )


def _cookie_token(request_or_ws):
    """Read the auth session token from a request's / websocket's cookies."""
    return request_or_ws.cookies.get(_AUTH_COOKIE)


def create_app(bridge, port=8000, open_browser=True, password=None,
               passwords=None):
    """Create the FastAPI app.

    ``passwords`` is a ``{role: password}`` dict that enables role-based access:
    the role a visitor authenticates with is stored in their session and passed
    to the bridge so per-role panel filtering and viewer callbacks work.
    ``password`` (a single string) keeps backward compatibility — all viewers
    get ``role=None``. If both are given, ``passwords`` takes precedence.
    """
    @asynccontextmanager
    async def lifespan(app):
        bridge.set_loop(asyncio.get_running_loop())
        if open_browser:
            url = f"http://127.0.0.1:{port}"
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        yield

    app = FastAPI(lifespan=lifespan)

    # Normalise: passwords= dict takes precedence over the legacy password= str.
    # role_map: {role: password} when roles are in use; None otherwise.
    # single_pw: plain password string when no roles needed; None otherwise.
    if passwords is not None:
        role_map = passwords
        single_pw = None
    elif password is not None:
        role_map = None
        single_pw = password
    else:
        role_map = None
        single_pw = None

    auth_required = role_map is not None or single_pw is not None

    # Sessions store: token -> role (role is None when single_pw is used).
    sessions = {}

    def _authed(scope_obj):
        return not auth_required or _cookie_token(scope_obj) in sessions

    def _role_of(scope_obj):
        return sessions.get(_cookie_token(scope_obj))

    if auth_required:
        @app.post("/__auth__")
        async def authenticate(request: Request):
            from urllib.parse import parse_qs
            body = (await request.body()).decode("utf-8", "replace")
            given = parse_qs(body).get("password", [""])[0]
            if role_map is not None:
                # Find which role this password matches (constant-time per entry).
                matched_role = None
                for role, pw in role_map.items():
                    if secrets.compare_digest(str(given), str(pw)):
                        matched_role = role
                        break
                if matched_role is None:
                    return HTMLResponse(_login_page(error=True), status_code=401)
                token = secrets.token_urlsafe(24)
                sessions[token] = matched_role
            else:
                if not secrets.compare_digest(str(given), str(single_pw)):
                    return HTMLResponse(_login_page(error=True), status_code=401)
                token = secrets.token_urlsafe(24)
                sessions[token] = None
            resp = RedirectResponse(url="/", status_code=303)
            resp.set_cookie(_AUTH_COOKIE, token, httponly=True,
                            samesite="lax", max_age=86400)
            return resp

        @app.middleware("http")
        async def gate(request, call_next):
            if request.url.path == "/__auth__" or _authed(request):
                return await call_next(request)
            return HTMLResponse(_login_page(), status_code=401)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        if not _authed(ws):
            await ws.close(code=1008)
            return
        await bridge.handle_connection(ws, role=_role_of(ws))

    # Any other WebSocket path would otherwise fall through to the StaticFiles
    # mount, which only handles HTTP and raises AssertionError. Reject cleanly.
    @app.websocket("/{path:path}")
    async def ws_reject(ws: WebSocket):
        await ws.close(code=1008)

    # Mount the built frontend last so /ws keeps priority over the catch-all.
    if os.path.isdir(DIST_DIR):
        app.mount("/", _FrontendStatic(directory=DIST_DIR, html=True), name="static")

    return app


def run(bridge, port=8000, open_browser=True, host="127.0.0.1", password=None,
        passwords=None):
    app = create_app(bridge, port=port, open_browser=open_browser,
                     password=password, passwords=passwords)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            **_WS_OPTS)
    server = uvicorn.Server(config)
    _announce(host, port)
    server.run()  # blocks until Ctrl+C / shutdown


def run_background(bridge, port=8000, open_browser=True, host="127.0.0.1",
                   password=None, passwords=None):
    """Start the server in a daemon thread and return immediately.

    Returns the uvicorn ``Server`` so the caller can stop it later via
    ``server.should_exit = True``. Suited to interactive sessions (Jupyter)
    where the cell must return so more components can be inserted.
    """
    app = create_app(bridge, port=port, open_browser=open_browser,
                     password=password, passwords=passwords)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            **_WS_OPTS)
    server = uvicorn.Server(config)
    _announce(host, port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server
