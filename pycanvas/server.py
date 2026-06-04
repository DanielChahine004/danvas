"""FastAPI app: WebSocket endpoint + static serving of the built frontend."""

import asyncio
import os
import threading
import webbrowser
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

DIST_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")


def create_app(bridge, port=8000, open_browser=True):
    @asynccontextmanager
    async def lifespan(app):
        # Capture the running loop so cross-thread broadcasts can target it.
        bridge.set_loop(asyncio.get_running_loop())
        if open_browser:
            url = f"http://127.0.0.1:{port}"
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        yield

    app = FastAPI(lifespan=lifespan)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await bridge.handle_connection(ws)

    # Any other WebSocket path would otherwise fall through to the StaticFiles
    # mount, which only handles HTTP and raises AssertionError. Reject cleanly.
    @app.websocket("/{path:path}")
    async def ws_reject(ws: WebSocket):
        await ws.close(code=1008)

    # Mount the built frontend last so /ws keeps priority over the catch-all.
    if os.path.isdir(DIST_DIR):
        app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="static")

    return app


def run(bridge, port=8000, open_browser=True):
    app = create_app(bridge, port=port, open_browser=open_browser)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()  # blocks until Ctrl+C / shutdown


def run_background(bridge, port=8000, open_browser=True):
    """Start the server in a daemon thread and return immediately.

    Returns the uvicorn ``Server`` so the caller can stop it later via
    ``server.should_exit = True``. Suited to interactive sessions (Jupyter)
    where the cell must return so more components can be inserted.
    """
    app = create_app(bridge, port=port, open_browser=open_browser)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server
