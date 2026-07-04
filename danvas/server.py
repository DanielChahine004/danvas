"""FastAPI app: WebSocket endpoint + static serving of the built frontend."""

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import socket
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles

# Cookie that carries a viewer's auth session token once they pass the password
# page. The token is random per session and validated server-side, so the
# password itself never rides in a cookie.
_AUTH_COOKIE = "pc_session"


def _dist_dir():
    """Locate the built frontend, both in-source and inside a baked executable.

    A PyInstaller build bundles the frontend under ``pcframe/dist`` in the
    extraction dir (``sys._MEIPASS``) — deliberately *not* under ``danvas/``,
    which would shadow the real package as a namespace dir and break
    ``import danvas``. In a normal install it lives next to this module.
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


def _ws_opts(compress):
    """uvicorn WebSocket options for this bind.

    ``compress`` toggles permessage-deflate. It's off by default and on only for
    a tunnel, because the trade-off flips with the link: deflate squeezes
    repetitive *text* frames (plots/tables) well but barely dents already-
    compressed *binary* media (JPEG/PCM ~3%), while costing real CPU per frame on
    the event-loop thread (~1 ms per video frame, per viewer). On a fast
    local/LAN link the bytes saved aren't worth that CPU (and it directly eats
    into the broadcast fan-out budget); on a bandwidth-constrained public tunnel
    they are. uvicorn exposes a single global switch, so we pick per bind."""
    return {**_WS_OPTS, "ws_per_message_deflate": bool(compress)}


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
        print(f"danvas serving at {local}  (Ctrl+C to stop)")
        return
    print("danvas serving  (Ctrl+C to stop):")
    print(f"  local:   {local}")
    ip = _lan_ip()
    if ip:
        print(f"  network: http://{ip}:{port}"
              "   <- open this on another device on the same Wi-Fi")
    else:
        print(f"  network: http://<this-machine-ip>:{port}"
              "   <- open this on another device on the same Wi-Fi")


# Precompressed-encoding preferences, best ratio first. The build
# (frontend/scripts/precompress.mjs) emits a ``.br`` and ``.gz`` sibling for each
# compressible asset; we serve whichever the client advertises rather than the
# raw ~7 MB bundle. Browsers only offer ``br`` over HTTPS (the tunnel) but send
# ``gzip`` over plain HTTP (the local/LAN bind), so both pull their weight.
_PRECOMPRESSED = (("br", ".br"), ("gzip", ".gz"))


class _FrontendStatic(StaticFiles):
    """Serve the built frontend: precompressed when the client accepts it, and
    never letting the browser cache index.html.

    The JS/CSS bundles are content-hashed (safe to cache forever), but the HTML
    that points at them changes every rebuild. Without this, a browser holding a
    stale index.html requests a bundle hash that no longer exists -> blank/grey
    page after a rebuild. Forcing revalidation of the HTML avoids that.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path in (".", "", "index.html") or path.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return self._maybe_precompressed(scope, response)

    @staticmethod
    def _maybe_precompressed(scope, response):
        """Swap a plain file response for its ``.br``/``.gz`` sibling when one
        exists and the client advertised that encoding.

        Only ever swaps a straight ``200`` file response, and never for a Range
        request (a partial read over the *encoded* bytes is a needless footgun) —
        every other case (304, redirect, missing file) falls through untouched.
        The compressed file's own size/etag are used (a distinct representation),
        the original ``Content-Type`` and any ``Cache-Control`` are carried over,
        and ``Vary: Accept-Encoding`` is set so caches key on the encoding.
        """
        if getattr(response, "status_code", None) != 200:
            return response
        full = getattr(response, "path", None)
        if not full:
            return response
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        if "range" in headers:
            return response
        accepted = {t.split(";")[0].strip()
                    for t in headers.get("accept-encoding", "").split(",")}
        for token, ext in _PRECOMPRESSED:
            if token in accepted and os.path.isfile(full + ext):
                swapped = FileResponse(
                    full + ext, media_type=response.headers.get("content-type"))
                cache_control = response.headers.get("cache-control")
                if cache_control:
                    swapped.headers["Cache-Control"] = cache_control
                swapped.headers["Content-Encoding"] = token
                swapped.headers["Vary"] = "Accept-Encoding"
                return swapped
        return response


class _UploadTooLarge(Exception):
    """Raised mid-stream when an upload exceeds the panel's ``max_size``."""


def _safe_upload_path(dest_root, filename):
    """Resolve ``filename`` to a path strictly inside ``dest_root``.

    The browser supplies the filename, so it's untrusted: ``basename`` strips any
    directory parts (``../`` included) and the result is re-checked against the
    realpath of the root, so an upload can never land outside the destination.
    Collisions get a ``-1``/``-2`` suffix rather than overwriting.
    """
    name = os.path.basename(filename) or "upload.bin"
    root = os.path.realpath(dest_root)
    target = os.path.realpath(os.path.join(root, name))
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("upload filename escapes the destination directory")
    if not os.path.exists(target):
        return target
    base, ext = os.path.splitext(target)
    i = 1
    while os.path.exists(f"{base}-{i}{ext}"):
        i += 1
    return f"{base}-{i}{ext}"


async def _stream_upload_to_disk(request, target, max_size):
    """Stream the request body to ``target`` in chunks; return bytes written.

    Enforces ``max_size`` (if set) as the bytes arrive and deletes the partial
    file before raising :class:`_UploadTooLarge`, so a too-big upload can't fill
    the disk. Streaming keeps server memory flat regardless of file size.
    """
    size = 0
    try:
        with open(target, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if max_size and size > max_size:
                    raise _UploadTooLarge()
                f.write(chunk)
    except _UploadTooLarge:
        if os.path.exists(target):
            os.remove(target)
        raise
    return size


async def _read_upload_to_memory(request, max_size):
    """Read the request body into ``bytes``, enforcing ``max_size`` as it grows."""
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if max_size and size > max_size:
            raise _UploadTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


def _attachment_headers(filename):
    """Build a ``Content-Disposition: attachment`` header for ``filename``.

    Provides both a plain ``filename`` (with quotes/control chars stripped so the
    header stays well-formed) and an RFC 5987 ``filename*`` UTF-8 form, so
    non-ascii names survive. Used for the in-memory (``bytes``) download branch;
    ``FileResponse`` builds the equivalent header itself for on-disk files.
    """
    from urllib.parse import quote

    ascii_name = "".join(c for c in filename if c.isprintable() and c not in '"\\') \
        .encode("ascii", "ignore").decode("ascii") or "download"
    star = quote(filename, safe="")
    return {
        "Content-Disposition":
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{star}"
    }


def _login_page(error=False, message=None):
    """The minimal password prompt shown before an unauthenticated view loads.

    ``message`` is an optional host-provided note (``serve(login_message=...)``)
    shown above the field — e.g. which password each kind of viewer should enter.
    It is HTML-escaped (newlines preserved) so it renders as plain text, never
    markup.
    """
    err = ("<p class='err'>Wrong password — try again.</p>" if error else "")
    note = (f"<p class='note'>{html.escape(str(message))}</p>" if message else "")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>danvas</title><style>"
        "html,body{height:100%;margin:0;font-family:system-ui,sans-serif;"
        "background:#0f172a;color:#e2e8f0}"
        ".wrap{height:100%;display:flex;align-items:center;justify-content:center}"
        "form{background:#1e293b;padding:28px 28px 24px;border-radius:12px;"
        "border:1px solid #334155;min-width:260px;max-width:360px}"
        "h1{font-size:18px;margin:0 0 4px}p{color:#94a3b8;font-size:13px;margin:0 0 16px}"
        ".err{color:#f87171}"
        ".note{white-space:pre-line;line-height:1.5;color:#cbd5e1;"
        "background:rgba(255,255,255,.04);border:1px solid #334155;"
        "border-radius:8px;padding:10px 12px}"
        "input{width:100%;box-sizing:border-box;padding:9px 10px;border-radius:7px;"
        "border:1px solid #475569;background:#0f172a;color:#e2e8f0;font-size:14px}"
        "button{margin-top:12px;width:100%;padding:9px;border:0;border-radius:7px;"
        "background:#3b82f6;color:#fff;font-size:14px;cursor:pointer}"
        "button:hover{background:#2563eb}</style></head><body><div class='wrap'>"
        "<form method='post' action='/__auth__'>"
        "<h1>danvas</h1><p>This canvas is password protected.</p>"
        f"{note}{err}"
        "<input type='password' name='password' placeholder='Password' autofocus>"
        "<button type='submit'>Enter</button></form></div></body></html>"
    )


def _cookie_token(request_or_ws):
    """Read the auth session token from a request's / websocket's cookies."""
    return request_or_ws.cookies.get(_AUTH_COOKIE)


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _session_secret():
    """Key used to sign session cookies.

    Under hot reload the watcher process exports one fixed key to every worker
    (``_danvas_RELOAD_SECRET``) so a viewer's cookie keeps validating across
    restarts — the websocket just reconnects and replays, no re-login on each
    edit. Without it (a plain run) the key is per-process, so a real restart
    re-prompts exactly as before.
    """
    env = os.environ.get("_danvas_RELOAD_SECRET")
    return env.encode("utf-8") if env else secrets.token_bytes(32)


def _sign_session(role, secret):
    """A stateless, tamper-proof session token that encodes ``role``.

    The role travels in the (signed) cookie itself instead of a server-side map,
    so no shared session store is needed — any process holding ``secret`` can
    verify it. ``role`` is ``None`` for single-password mode.
    """
    payload = _b64(json.dumps({"r": role}).encode("utf-8"))
    sig = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64(sig)}"


def _read_session(token, secret):
    """Validate a signed session ``token``; return ``(ok, role)``.

    ``role`` is ``None`` both for single-password mode and for any invalid token
    (callers gate on ``ok`` first).
    """
    if not token or "." not in token:
        return False, None
    payload, _, sig = token.partition(".")
    expected = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expected, _unb64(sig)):
            return False, None
        return True, json.loads(_unb64(payload)).get("r")
    except Exception:
        return False, None


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

    # Optional host note shown on the login page (serve(login_message=...)). Read
    # off the bridge — serve stores it there — so it rides along without threading
    # an extra arg through run()/run_background()/the _serve_* helpers.
    login_message = getattr(bridge, "_login_message", None)

    # Sessions are stateless: the role rides in a signed cookie (see
    # _sign_session), so there's no in-memory token map to lose on a restart —
    # which is what lets hot reload reconnect a viewer without re-login.
    secret = _session_secret()

    def _authed(scope_obj):
        if not auth_required:
            return True
        ok, _ = _read_session(_cookie_token(scope_obj), secret)
        return ok

    def _role_of(scope_obj):
        return _read_session(_cookie_token(scope_obj), secret)[1]

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
                    return HTMLResponse(_login_page(error=True, message=login_message),
                                        status_code=401)
                token = _sign_session(matched_role, secret)
            else:
                if not secrets.compare_digest(str(given), str(single_pw)):
                    return HTMLResponse(_login_page(error=True, message=login_message),
                                        status_code=401)
                token = _sign_session(None, secret)
            resp = RedirectResponse(url="/", status_code=303)
            resp.set_cookie(_AUTH_COOKIE, token, httponly=True,
                            samesite="lax", max_age=86400)
            return resp

        @app.get("/__logout__")
        async def logout():
            # Clear the session cookie and bounce back to the login page. The
            # cookie is httponly (JS can't delete it), so sign-out has to
            # round-trip the server; the next request then fails the gate and
            # the viewer sees the password prompt — free to log in as another
            # role. Reachable only by an already-authed viewer (the gate lets
            # them through); an unauthed hit just gets the login page anyway.
            resp = RedirectResponse(url="/", status_code=303)
            resp.delete_cookie(_AUTH_COOKIE, path="/", samesite="lax")
            return resp

        @app.middleware("http")
        async def gate(request, call_next):
            if request.url.path == "/__auth__" or _authed(request):
                return await call_next(request)
            return HTMLResponse(_login_page(message=login_message), status_code=401)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        if not _authed(ws):
            await ws.close(code=1008)
            return
        await bridge.handle_connection(ws, role=_role_of(ws))

    # File downloads minted by the Download panel. The browser only ever sees an
    # unguessable, short-lived token (see Bridge.register_download); it's resolved
    # here to host-chosen content, so no viewer-supplied path is ever trusted.
    # Sits behind the auth gate above, so a password/role-protected canvas
    # protects its downloads too.
    @app.get("/__download__/{token}")
    async def download(token: str, request: Request):
        item = bridge.take_download(token)
        if item is None:
            # A hub doesn't hold file bytes — the owning SOURCE does. Pull
            # them over the wire (file_pull -> file_meta + FILE envelope).
            merge = getattr(bridge, "_merge", None)
            if merge is not None:
                pulled = await merge.pull_file(token)
                if pulled is not None:
                    p_name, p_data = pulled
                    return Response(content=p_data,
                                    media_type="application/octet-stream",
                                    headers=_attachment_headers(p_name))
            return PlainTextResponse("download expired or not found",
                                     status_code=404)
        filename, source, role = item
        # Per-token role gate (canvas.serve_bytes(role=...)): the shared auth gate
        # above already requires a valid session; this further restricts a download
        # to one login role.
        if role is not None and _role_of(request) != role:
            return PlainTextResponse("forbidden", status_code=403)
        if isinstance(source, (bytes, bytearray)):
            return Response(content=bytes(source),
                            media_type="application/octet-stream",
                            headers=_attachment_headers(filename))
        # A filesystem path: FileResponse streams it and sets the attachment
        # header (incl. utf-8 filename) from ``filename`` itself.
        return FileResponse(source, filename=filename,
                            media_type="application/octet-stream")

    # Read-only inspection for an external process (a terminal, an LLM agent)
    # to QC the live canvas without editing the serving script. Behind the same
    # auth gate as everything else, so a password/role canvas doesn't leak its
    # current UI state or a screenshot of the screen. Defined as sync `def` so
    # Starlette runs them in a threadpool — request_image() blocks on a browser
    # round-trip and must not stall the event loop (the same reason canvas.save
    # calls request_snapshot from a worker thread, not the loop).
    @app.get("/__describe__")
    def describe():
        # Pure Python state — works with no browser open (headless QC).
        canvas = getattr(bridge, "_canvas", None)
        if canvas is not None:
            return JSONResponse(canvas.describe())
        # A standing hub has no canvas of its own; its inventory is the
        # composed replay cache — one entry per merged panel, with the
        # cross-process identity (name/owner) and source liveness.
        merge = getattr(bridge, "_merge", None)
        if merge is None:
            return PlainTextResponse("no canvas", status_code=503)
        components = []
        for up in merge._upstreams.values():
            for nsid, reg in up.registers.items():
                components.append({
                    "id": nsid,
                    "name": reg.get("name"),
                    "owner": reg.get("owner", up.label),
                    "component": reg.get("component"),
                    "x": reg.get("x"), "y": reg.get("y"),
                    "source": up.label, "status": up.status,
                })
        return JSONResponse({"components": components})

    @app.get("/__screenshot__.png")
    def screenshot_png():
        # Needs a connected browser to render; 503 if none, 504 on timeout.
        try:
            png = bridge.request_image([], timeout=15.0)
        except RuntimeError as exc:
            return PlainTextResponse(str(exc), status_code=503)
        except TimeoutError as exc:
            return PlainTextResponse(str(exc), status_code=504)
        return Response(content=png, media_type="image/png")

    # File uploads received by an Upload panel. The browser POSTs the raw file
    # body (name in the query) to its panel's token URL; we stream it (to disk if
    # the panel set ``dest=``, else into memory) and hand it to Python. Behind the
    # auth gate above, so only authorised viewers can upload.
    @app.post("/__upload__/{token}")
    async def upload(token: str, request: Request, name: str = "", viewer: str = ""):
        comp = bridge.upload_component(token)
        if comp is None:
            # A hub doesn't own upload endpoints — a SOURCE does. Push the
            # bytes over the wire (file_push + FILE envelope -> file_ack).
            merge = getattr(bridge, "_merge", None)
            if merge is not None:
                filename = os.path.basename(name) or "upload.bin"
                ctype = (request.headers.get("content-type")
                         or "application/octet-stream")
                try:
                    body = await _read_upload_to_memory(request, None)
                except Exception as exc:
                    return PlainTextResponse(f"upload failed: {exc}",
                                             status_code=400)
                ack = await merge.push_file(token, filename, ctype, body)
                if ack is not None:
                    return {"ok": True, "name": ack.get("name", filename),
                            "size": ack.get("size", len(body))}
            return PlainTextResponse("unknown upload target", status_code=404)
        # Per-endpoint role gate (canvas.receive_files(role=...) / Upload(role=...)):
        # the shared auth gate already requires a session; this restricts the
        # endpoint to one login role.
        target_role = getattr(comp, "_role", None)
        if target_role is not None and _role_of(request) != target_role:
            return PlainTextResponse("forbidden", status_code=403)
        max_size = getattr(comp, "_max_size", None)
        # Reject early when the declared length already blows the cap.
        clen = request.headers.get("content-length")
        if max_size and clen and clen.isdigit() and int(clen) > max_size:
            return PlainTextResponse("file too large", status_code=413)
        filename = os.path.basename(name) or "upload.bin"
        content_type = request.headers.get("content-type") or \
            "application/octet-stream"
        dest = getattr(comp, "_dest", None)
        try:
            if dest:
                target = _safe_upload_path(dest, filename)
                size = await _stream_upload_to_disk(request, target, max_size)
                info = {"name": os.path.basename(target), "size": size,
                        "content_type": content_type, "data": None,
                        "path": target}
            else:
                data = await _read_upload_to_memory(request, max_size)
                info = {"name": filename, "size": len(data),
                        "content_type": content_type, "data": data, "path": None}
        except _UploadTooLarge:
            return PlainTextResponse("file too large", status_code=413)
        except Exception as exc:
            return PlainTextResponse(f"upload failed: {exc}", status_code=400)
        identity = bridge.resolve_viewer(viewer, _role_of(request))
        bridge.deliver_upload(comp, info, viewer=identity)
        return {"ok": True, "name": info["name"], "size": info["size"]}

    # Internal endpoint used by the hot-reload monitor for partial React source
    # updates. Only accessible from loopback — the monitor is always local.
    @app.post("/__hot_source__")
    async def hot_source(request: Request):
        if request.client.host not in ("127.0.0.1", "::1"):
            return PlainTextResponse("forbidden", status_code=403)
        body = await request.json()
        name = body.get("name")
        source = body.get("source")
        comp = next(
            (c for c in bridge._components.values()
             if getattr(c, "name", None) == name and hasattr(c, "set_source")),
            None,
        )
        if comp is None:
            return {"ok": False, "error": f"no React component named {name!r}"}
        comp.set_source(source)
        return {"ok": True}

    # Internal endpoint for the hot-reload monitor's smart middle tier: when a
    # save changed only the bodies of top-level functions, swap those code
    # objects in the live worker instead of restarting it (see _livepatch). The
    # monitor sends the old + new script text; this side classifies and applies.
    # Loopback only — the monitor is always local.
    @app.post("/__hot_patch__")
    async def hot_patch(request: Request):
        if request.client.host not in ("127.0.0.1", "::1"):
            return PlainTextResponse("forbidden", status_code=403)
        from ._livepatch import apply_live_patch, safe_live_diff

        body = await request.json()
        old = body.get("old") or ""
        new = body.get("new") or ""
        specs = safe_live_diff(old, new)
        if specs is None:
            return {"ok": False, "error": "not a body-only change"}
        main_mod = sys.modules.get("__main__")
        if main_mod is None:
            return {"ok": False, "error": "no __main__ module"}
        canvas = getattr(bridge, "_canvas", None)
        background = [fn for fn, _a, _k in getattr(canvas, "_background", [])]
        try:
            ok, detail = apply_live_patch(
                main_mod, list(bridge._components.values()),
                old, new, specs, background_funcs=background,
            )
        except Exception as exc:  # never take the live worker down on a patch bug
            import traceback as _tb
            _tb.print_exc()
            return {"ok": False, "error": repr(exc)}
        return ({"ok": True, "swapped": detail} if ok
                else {"ok": False, "error": detail})

    # Any other WebSocket path would otherwise fall through to the StaticFiles
    # mount, which only handles HTTP and raises AssertionError. Reject cleanly.
    @app.websocket("/{path:path}")
    async def ws_reject(ws: WebSocket):
        await ws.close(code=1008)

    # Serve the root document ourselves so we can send it no-cache: the page
    # references hash-named asset bundles, so a stale cached index.html would point
    # at assets that no longer exist after a rebuild. Registered before the static
    # mount so it wins for "/"; /assets/* still come from the mount.
    _index = os.path.join(DIST_DIR, "index.html")

    @app.get("/")
    def index():
        if not os.path.isfile(_index):
            return PlainTextResponse("frontend not built", status_code=404)
        with open(_index, encoding="utf-8") as f:
            doc = f.read()
        return HTMLResponse(doc, headers={"Cache-Control": "no-cache"})

    # Mount the built frontend last so /ws keeps priority over the catch-all.
    if os.path.isdir(DIST_DIR):
        app.mount("/", _FrontendStatic(directory=DIST_DIR, html=True), name="static")

    # Keep the app reachable from the bridge so live hosting changes (the 🌐
    # button / canvas.expose) can add a LAN listener for the SAME app.
    bridge._app = app
    return app


def _make_server_socket(host, port):
    """Pre-bind a TCP socket with SO_REUSEADDR and hand it to uvicorn.

    uvicorn skips SO_REUSEADDR on Windows (its POSIX semantics — reuse a
    TIME_WAIT port — are unsafe there), so after Ctrl+C the port stays busy
    for up to two minutes.  Creating the socket ourselves and passing it via
    ``sockets=`` bypasses uvicorn's socket creation and fixes the problem
    on all platforms.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.set_inheritable(True)
    return sock


def _configure_logging():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")
    logging.getLogger("danvas").setLevel(logging.INFO)


def run(bridge, port=8000, open_browser=True, host="127.0.0.1", password=None,
        passwords=None, compress=False):
    _configure_logging()
    app = create_app(bridge, port=port, open_browser=open_browser,
                     password=password, passwords=passwords)
    try:
        sock = _make_server_socket(host, port)
    except OSError:
        print(
            f"\n\033[31m[danvas] Port {port} is already in use.\033[0m\n"
            f"  Another danvas server (or something else) is already listening on {host}:{port}.\n"
            f"  Kill it first, or pass a different port:  canvas.serve(port=8001)\n",
            flush=True,
        )
        raise SystemExit(1)
    config = uvicorn.Config(app, log_level="warning",
                            timeout_graceful_shutdown=5, **_ws_opts(compress))
    server = uvicorn.Server(config)
    _announce(host, port)
    server.run(sockets=[sock])  # blocks until Ctrl+C / shutdown


def run_background(bridge, port=8000, open_browser=True, host="127.0.0.1",
                   password=None, passwords=None, compress=False):
    """Start the server in a daemon thread and return immediately.

    Returns the uvicorn ``Server`` so the caller can stop it later via
    ``server.should_exit = True``. Suited to interactive sessions (Jupyter)
    where the cell must return so more components can be inserted.
    """
    _configure_logging()
    app = create_app(bridge, port=port, open_browser=open_browser,
                     password=password, passwords=passwords)
    try:
        sock = _make_server_socket(host, port)
    except OSError:
        print(
            f"\n\033[31m[danvas] Port {port} is already in use.\033[0m\n"
            f"  Another danvas server (or something else) is already listening on {host}:{port}.\n"
            f"  Kill it first, or pass a different port:  canvas.serve(port=8001)\n",
            flush=True,
        )
        raise SystemExit(1)
    config = uvicorn.Config(app, log_level="warning", **_ws_opts(compress))
    server = uvicorn.Server(config)
    _announce(host, port)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]},
                              daemon=True)
    thread.start()
    return server