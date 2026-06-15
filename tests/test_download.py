import json

import pytest

import pycanvas
from pycanvas.bridge import Bridge


class FakeBridge:
    """Minimal bridge that records downloads the panel registers."""

    def __init__(self):
        self.sent = []
        self.registered = []
        self._token_seq = 0

    def broadcast(self, msg, exclude=None):
        self.sent.append(msg)

    def broadcast_binary(self, data):
        pass

    def register_download(self, filename, source, ttl=300):
        self._token_seq += 1
        token = f"tok{self._token_seq}"
        self.registered.append((token, filename, source))
        return token


def _face(dl):
    """The button's face text, read out of the React `data` JSON prop."""
    return json.loads(dl.register_props()["data"])["text"]


def test_text_defaults_to_name_and_is_a_shape_prop():
    dl = pycanvas.Download("export")
    assert _face(dl) == "export"
    dl2 = pycanvas.Download("x", text="Get report")
    assert _face(dl2) == "Get report"


def test_static_bytes_source_resolves_to_a_url(tmp_path):
    dl = pycanvas.Download("data", source=b"a,b\n1,2\n", filename="data.csv")
    dl._bind("d1", FakeBridge())

    reply = dl._on_download(None)

    token, filename, source = dl._bridge.registered[-1]
    assert filename == "data.csv"
    assert source == b"a,b\n1,2\n"
    assert reply == {"url": f"/__download__/{token}", "filename": "data.csv"}


def test_static_path_source_uses_basename_when_no_filename(tmp_path):
    p = tmp_path / "report.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    dl = pycanvas.Download("report", source=str(p))
    dl._bind("d1", FakeBridge())

    dl._on_download(None)

    _token, filename, source = dl._bridge.registered[-1]
    assert filename == "report.pdf"
    assert source == str(p)


def test_provider_runs_each_click_and_can_name_the_file():
    dl = pycanvas.Download("export")
    dl._bind("d1", FakeBridge())
    n = {"v": 0}

    @dl.provide
    def _():
        n["v"] += 1
        return (f"export-{n['v']}.csv", f"row {n['v']}".encode())

    dl._on_download(None)
    dl._on_download(None)

    first, second = dl._bridge.registered[-2], dl._bridge.registered[-1]
    assert first[1] == "export-1.csv" and first[2] == b"row 1"
    assert second[1] == "export-2.csv" and second[2] == b"row 2"


def test_provider_supersedes_static_source():
    dl = pycanvas.Download("export", source=b"static")
    dl._bind("d1", FakeBridge())
    dl.provide(lambda: b"dynamic")

    dl._on_download(None)

    assert dl._bridge.registered[-1][2] == b"dynamic"


def test_missing_content_raises():
    dl = pycanvas.Download("export")
    dl._bind("d1", FakeBridge())
    with pytest.raises(ValueError):
        dl._on_download(None)


def test_missing_path_raises():
    dl = pycanvas.Download("report", source="does/not/exist.pdf")
    dl._bind("d1", FakeBridge())
    with pytest.raises(FileNotFoundError):
        dl._on_download(None)


def test_request_routes_through_react_handler():
    """A click arrives as a request with no event; the catch-all handler answers."""
    dl = pycanvas.Download("data", source=b"x", filename="x.bin")
    dl._bind("d1", FakeBridge())
    reply = dl._handle_request({})
    assert reply["filename"] == "x.bin"
    assert reply["url"].startswith("/__download__/")


def test_factory_inserts_and_places():
    canvas = pycanvas.Canvas()
    dl = canvas.download("export", source=b"x", text="Save", x=10, y=20)
    assert dl.component == "React"  # native React panel, like Button
    assert dl.x == 10 and dl.y == 20


def test_bridge_register_take_roundtrip_and_expiry():
    bridge = Bridge()
    token = bridge.register_download("a.txt", b"hello", ttl=300)
    assert bridge.take_download(token) == ("a.txt", b"hello")
    # Unknown token -> None.
    assert bridge.take_download("nope") is None
    # Expired token -> None (ttl in the past).
    expired = bridge.register_download("b.txt", b"bye", ttl=-1)
    assert bridge.take_download(expired) is None
