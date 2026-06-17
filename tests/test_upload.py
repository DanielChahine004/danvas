import json
import threading

from fastapi.testclient import TestClient

import pycanvas
from pycanvas import server
from pycanvas.bridge import Bridge
from pycanvas.components import UploadedFile


class FakeBridge:
    """Records upload registrations and runs delivered callbacks synchronously."""

    def __init__(self):
        self.sent = []
        self.uploads = {}

    def broadcast(self, msg, exclude=None):
        self.sent.append(msg)

    def broadcast_binary(self, data):
        pass

    def register_upload(self, token, component):
        self.uploads[token] = component


def _props(up):
    return json.loads(up.register_props()["data"])


def test_props_carry_token_url_and_options():
    up = pycanvas.Upload("up", text="Send", accept=".csv", multiple=True)
    p = _props(up)
    assert p["text"] == "Send"
    assert p["accept"] == ".csv"
    assert p["multiple"] is True
    assert p["url"] == f"/__upload__/{up._token}"


def test_bind_registers_token_with_bridge():
    up = pycanvas.Upload("up")
    bridge = FakeBridge()
    up._bind("u1", bridge)
    assert bridge.uploads[up._token] is up


def test_receive_upload_fires_callback_and_sets_value():
    up = pycanvas.Upload("up")
    up._bind("u1", FakeBridge())
    got = []
    up.on_upload(lambda f: got.append(f))

    up._receive_upload({"name": "a.txt", "size": 3, "content_type": "text/plain",
                        "data": b"abc", "path": None})

    assert len(got) == 1
    assert got[0].name == "a.txt" and got[0].read() == b"abc"
    assert up.value.name == "a.txt"


def test_callback_can_receive_viewer():
    up = pycanvas.Upload("up")
    up._bind("u1", FakeBridge())
    seen = {}
    up.on_upload(lambda f, viewer: seen.update(viewer))

    up._receive_upload({"name": "a", "size": 0, "content_type": None,
                        "data": b"", "path": None}, viewer={"role": "admin"})

    assert seen == {"role": "admin"}


def test_uploadedfile_read_and_save_from_memory(tmp_path):
    f = UploadedFile("x.bin", 5, "application/octet-stream", data=b"hello")
    assert f.read() == b"hello"
    out = f.save(str(tmp_path))
    assert out.endswith("x.bin")
    assert open(out, "rb").read() == b"hello"


def test_factory_inserts_and_places():
    canvas = pycanvas.Canvas()
    up = canvas.upload("up", text="Send", x=10, y=20)
    assert up.component == "React"
    assert up.x == 10 and up.y == 20


# -- end-to-end through the HTTP route ------------------------------------------

def _app_with(component):
    canvas = pycanvas.Canvas()
    canvas.insert(component)
    return canvas, TestClient(server.create_app(canvas._bridge, open_browser=False))


def _capture(up):
    """Wire an on_upload handler that records the file + uploader and signals.

    The route delivers uploads on the bridge's dispatch thread, so tests wait on
    the returned event rather than racing it.
    """
    box = {"file": None, "viewer": None, "done": threading.Event()}

    @up.on_upload
    def _(f, viewer):
        box["file"] = f
        box["viewer"] = viewer
        box["done"].set()

    return box


def test_http_upload_in_memory_fires_handler():
    up = pycanvas.Upload("up")
    _canvas, client = _app_with(up)
    box = _capture(up)

    r = client.post(f"/__upload__/{up._token}?name=data.csv",
                    content=b"a,b\n1,2\n", headers={"content-type": "text/csv"})

    assert r.status_code == 200 and r.json()["ok"] is True
    assert box["done"].wait(2)
    assert box["file"].name == "data.csv"
    assert box["file"].data == b"a,b\n1,2\n" and box["file"].path is None


def test_http_upload_streams_to_dest(tmp_path):
    up = pycanvas.Upload("up", dest=str(tmp_path))
    _canvas, client = _app_with(up)
    box = _capture(up)

    r = client.post(f"/__upload__/{up._token}?name=big.bin", content=b"x" * 1000)

    assert r.status_code == 200
    assert box["done"].wait(2)
    f = box["file"]
    assert f.path is not None and f.data is None and f.size == 1000
    assert open(f.path, "rb").read() == b"x" * 1000


def test_http_upload_rejects_oversize():
    up = pycanvas.Upload("up", max_size=10)
    _canvas, client = _app_with(up)
    r = client.post(f"/__upload__/{up._token}?name=big", content=b"x" * 50)
    assert r.status_code == 413


def test_http_upload_unknown_token_404():
    _canvas, client = _app_with(pycanvas.Upload("up"))
    r = client.post("/__upload__/nope?name=x", content=b"x")
    assert r.status_code == 404


def test_http_upload_filename_cannot_escape_dest(tmp_path):
    up = pycanvas.Upload("up", dest=str(tmp_path))
    _canvas, client = _app_with(up)
    box = _capture(up)

    # A traversal attempt collapses to a basename inside dest.
    r = client.post(f"/__upload__/{up._token}?name=../../escape.txt", content=b"x")

    assert r.status_code == 200
    assert box["done"].wait(2)
    assert box["file"].path.startswith(str(tmp_path))
    assert box["file"].name == "escape.txt"


def test_bridge_register_and_remove_purges_token():
    bridge = Bridge()
    up = pycanvas.Upload("up")
    up._bind("u1", bridge)
    assert bridge.upload_component(up._token) is up
    bridge.remove_component("u1")
    assert bridge.upload_component(up._token) is None


# -- viewer identity ------------------------------------------------------------

def test_resolve_viewer_merges_roster_identity_with_trusted_role():
    bridge = Bridge()
    # Stand in for a live connection on the roster.
    bridge._viewers["ws-sentinel"] = {
        "id": "abc123", "name": "Fox", "color": "#ef4444",
        "cursor": {"x": 1, "y": 2}, "role": "viewer",
    }
    # role is the server-trusted value; id/name/color/cursor come from the roster.
    info = bridge.resolve_viewer("abc123", role="manager")
    assert info == {"role": "manager", "id": "abc123", "name": "Fox",
                    "color": "#ef4444", "cursor": {"x": 1, "y": 2}}


def test_resolve_viewer_unknown_id_keeps_uniform_shape():
    # A stale/forged id (or a disconnected uploader) still returns the full
    # shape so handlers read it uniformly -- only role is meaningful, the rest None.
    bridge = Bridge()
    assert bridge.resolve_viewer("ghost", role="manager") == {
        "id": None, "name": None, "color": None, "cursor": None, "role": "manager"}
    assert bridge.resolve_viewer("", role=None) == {
        "id": None, "name": None, "color": None, "cursor": None, "role": None}


def test_http_upload_attributes_to_a_connected_viewer():
    """An end-to-end upload carrying a live viewer id resolves to that identity."""
    up = pycanvas.Upload("up")
    canvas, client = _app_with(up)
    box = _capture(up)

    with client.websocket_connect("/ws") as ws:
        me = None
        for _ in range(6):  # welcome may trail a presence broadcast
            msg = ws.receive_json()
            if msg.get("type") == "welcome":
                me = msg["you"]
                break
        assert me is not None
        r = client.post(
            f"/__upload__/{up._token}?name=x.bin&viewer={me['id']}", content=b"x"
        )

    assert r.status_code == 200
    assert box["done"].wait(2)
    assert box["viewer"]["id"] == me["id"]
    assert box["viewer"]["name"] == me["name"]
    assert "role" in box["viewer"]
