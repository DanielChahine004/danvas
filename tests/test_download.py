import json

import pytest

import danvas
from danvas.bridge import Bridge


def _face(dl):
    """The button's face text, read out of the React `data` JSON prop."""
    return json.loads(dl.register_props()["data"])["text"]


def _download(**kw):
    """A Download inserted on a real canvas (so it has the canvas/bridge it now
    routes through — Download is a thin recipe over Canvas.serve_bytes)."""
    canvas = danvas.Canvas()
    dl = canvas.download("data", **kw)
    return canvas, dl


def _resolve_reply(canvas, reply):
    """Resolve the token in a download reply back to (filename, source)."""
    token = reply["url"].rsplit("/", 1)[1]
    filename, source, _role = canvas._bridge.take_download(token)
    return filename, source


def test_text_defaults_to_name_and_is_a_shape_prop():
    dl = danvas.Download("export")
    assert _face(dl) == "export"
    dl2 = danvas.Download("x", text="Get report")
    assert _face(dl2) == "Get report"


def test_static_bytes_source_resolves_to_a_url():
    canvas, dl = _download(source=b"a,b\n1,2\n", filename="data.csv")
    reply = dl._on_download(None)
    assert reply["filename"] == "data.csv"
    assert reply["url"].startswith("/__download__/")
    assert _resolve_reply(canvas, reply) == ("data.csv", b"a,b\n1,2\n")


def test_static_path_source_uses_basename_when_no_filename(tmp_path):
    p = tmp_path / "report.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    canvas = danvas.Canvas()
    dl = canvas.download("report", source=str(p))
    reply = dl._on_download(None)
    filename, source = _resolve_reply(canvas, reply)
    assert filename == "report.pdf"
    assert source == str(p)


def test_provider_runs_each_click_and_can_name_the_file():
    canvas, dl = _download()
    n = {"v": 0}

    @dl.provide
    def _():
        n["v"] += 1
        return (f"export-{n['v']}.csv", f"row {n['v']}".encode())

    first = _resolve_reply(canvas, dl._on_download(None))
    second = _resolve_reply(canvas, dl._on_download(None))
    assert first == ("export-1.csv", b"row 1")
    assert second == ("export-2.csv", b"row 2")


def test_provider_supersedes_static_source():
    canvas, dl = _download(source=b"static")
    dl.provide(lambda: b"dynamic")
    _filename, source = _resolve_reply(canvas, dl._on_download(None))
    assert source == b"dynamic"


def test_missing_content_raises():
    _canvas, dl = _download()
    with pytest.raises(ValueError):
        dl._on_download(None)


def test_missing_path_raises():
    _canvas, dl = _download(source="does/not/exist.pdf")
    with pytest.raises(FileNotFoundError):
        dl._on_download(None)


def test_request_routes_through_react_handler():
    """A click arrives as a request with no event; the catch-all handler answers."""
    _canvas, dl = _download(source=b"x", filename="x.bin")
    reply = dl._handle_request({})
    assert reply["filename"] == "x.bin"
    assert reply["url"].startswith("/__download__/")


def test_download_role_is_carried_to_the_token():
    canvas = danvas.Canvas()
    dl = canvas.download("secret", source=b"x", filename="s.bin", role="admin")
    reply = dl._on_download(None)
    token = reply["url"].rsplit("/", 1)[1]
    assert canvas._bridge.take_download(token)[2] == "admin"


def test_factory_inserts_and_places():
    canvas = danvas.Canvas()
    dl = canvas.download("export", source=b"x", text="Save", x=10, y=20)
    assert dl.component == "React"  # native React panel, like Button
    assert dl.x == 10 and dl.y == 20


def test_bridge_register_take_roundtrip_and_expiry():
    bridge = Bridge()
    token = bridge.register_download("a.txt", b"hello", ttl=300)
    assert bridge.take_download(token) == ("a.txt", b"hello", None)
    # Role rides along on the token.
    rtok = bridge.register_download("b.txt", b"x", ttl=300, role="mgr")
    assert bridge.take_download(rtok) == ("b.txt", b"x", "mgr")
    # Unknown token -> None.
    assert bridge.take_download("nope") is None
    # Expired token -> None (ttl in the past).
    expired = bridge.register_download("c.txt", b"bye", ttl=-1)
    assert bridge.take_download(expired) is None
