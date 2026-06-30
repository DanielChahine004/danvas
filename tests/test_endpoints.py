"""The public HTTP-endpoint primitives: Canvas.serve_bytes / Canvas.receive_files.

These are the capabilities the Download/Upload panels are built on, lifted out so
any panel/recipe can mint an auth-gated download URL or a file-receiving endpoint
from plain Python — the "expose the capability, not the panel" boundary. Tested
end-to-end through the real FastAPI routes (token resolution, role gate, filename
sandboxing, size cap), since that's where the security-sensitive behaviour lives.
"""

import threading

from fastapi.testclient import TestClient

import danvas
from danvas import server


def _client(canvas):
    return TestClient(server.create_app(canvas._bridge, open_browser=False))


# -- serve_bytes (download) --------------------------------------------------

def test_serve_bytes_roundtrips_bytes():
    canvas = danvas.Canvas()
    url = canvas.serve_bytes(b"a,b\n1,2\n", filename="data.csv")
    r = _client(canvas).get(url)
    assert r.status_code == 200
    assert r.content == b"a,b\n1,2\n"
    assert "attachment" in r.headers.get("content-disposition", "")


def test_serve_bytes_serves_a_path(tmp_path):
    p = tmp_path / "report.txt"
    p.write_bytes(b"hello")
    canvas = danvas.Canvas()
    url = canvas.serve_bytes(str(p), filename="report.txt")
    assert _client(canvas).get(url).content == b"hello"


def test_serve_bytes_missing_path_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        danvas.Canvas().serve_bytes("no/such/file.bin")


def test_serve_bytes_unknown_token_404():
    canvas = danvas.Canvas()
    assert _client(canvas).get("/__download__/bogus").status_code == 404


def test_serve_bytes_role_gate_blocks_mismatch():
    # No passwords set -> _role_of is None, which != "admin", so a role-restricted
    # token is refused even though the canvas is otherwise open.
    canvas = danvas.Canvas()
    url = canvas.serve_bytes(b"secret", filename="s.txt", role="admin")
    assert _client(canvas).get(url).status_code == 403


def test_serve_bytes_no_role_is_served():
    canvas = danvas.Canvas()
    url = canvas.serve_bytes(b"open", filename="o.txt")
    assert _client(canvas).get(url).status_code == 200


# -- receive_files (upload) --------------------------------------------------

def _wait_box(on_file_box):
    assert on_file_box["done"].wait(2)


def _capture():
    box = {"file": None, "viewer": None, "done": threading.Event()}

    def on_file(f, viewer):
        box["file"] = f
        box["viewer"] = viewer
        box["done"].set()

    return box, on_file


def test_receive_files_in_memory_fires_handler():
    canvas = danvas.Canvas()
    box, on_file = _capture()
    url = canvas.receive_files(on_file)
    r = _client(canvas).post(f"{url}?name=data.csv", content=b"a,b\n1,2\n")
    assert r.status_code == 200 and r.json()["ok"] is True
    _wait_box(box)
    assert box["file"].name == "data.csv"
    assert box["file"].data == b"a,b\n1,2\n" and box["file"].path is None


def test_receive_files_streams_to_dest(tmp_path):
    canvas = danvas.Canvas()
    box, on_file = _capture()
    url = canvas.receive_files(on_file, dest=str(tmp_path))
    r = _client(canvas).post(f"{url}?name=big.bin", content=b"x" * 1000)
    assert r.status_code == 200
    _wait_box(box)
    f = box["file"]
    assert f.path is not None and f.data is None and f.size == 1000
    assert open(f.path, "rb").read() == b"x" * 1000


def test_receive_files_sandboxes_a_traversal_filename(tmp_path):
    canvas = danvas.Canvas()
    box, on_file = _capture()
    url = canvas.receive_files(on_file, dest=str(tmp_path))
    r = _client(canvas).post(f"{url}?name=../../escape.txt", content=b"x")
    assert r.status_code == 200
    _wait_box(box)
    assert box["file"].path.startswith(str(tmp_path))   # collapsed inside dest
    assert box["file"].name == "escape.txt"


def test_receive_files_rejects_oversize():
    canvas = danvas.Canvas()
    _box, on_file = _capture()
    url = canvas.receive_files(on_file, max_size=10)
    assert _client(canvas).post(f"{url}?name=big", content=b"x" * 50).status_code == 413


def test_receive_files_role_gate_blocks_mismatch():
    canvas = danvas.Canvas()
    _box, on_file = _capture()
    url = canvas.receive_files(on_file, role="admin")          # no passwords -> role None
    assert _client(canvas).post(f"{url}?name=x", content=b"x").status_code == 403


def test_receive_files_single_arg_handler():
    # on_file may take just the file (no viewer), like the rest of the API.
    canvas = danvas.Canvas()
    got = {"f": None, "done": threading.Event()}
    url = canvas.receive_files(lambda f: (got.update(f=f), got["done"].set()))
    _client(canvas).post(f"{url}?name=a.txt", content=b"hi")
    assert got["done"].wait(2)
    assert got["f"].name == "a.txt" and got["f"].read() == b"hi"
