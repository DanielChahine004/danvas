"""Custom.export_html: one panel as a self-contained HTML file.

The file carries the panel's iframe document, a stub of the in-iframe
`canvas` API (send/request are no-ops, onPush still delivers), and a replay
of the panel's state so opening it from disk shows the panel as it looked.
Model3D replays every layer + view; a plain Custom replays its last push.

The wire tests here need nothing; the browser tests open the written file
via file:// in headless Chromium (skipped without playwright). The Model3D
one also needs the network, since the viewer loads xeokit from its CDN.
"""

import base64
import json
import os
import re

import pytest

import danvas
from danvas.components.custom import _standalone_document

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _replay(html):
    """The replay list embedded in an export (decoded)."""
    m = re.search(r"var F=(\[.*?\]);function b64", html, re.S)
    assert m, "no replay script"
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_custom_export_has_shim_document_and_last_push(tmp_path):
    canvas = danvas.Canvas()
    panel = canvas.custom(html="<div id=t>waiting</div>",
                          js="canvas.onPush(function(d){document.getElementById('t').textContent=d.msg});",
                          name="p")
    panel.push({"msg": "first"})
    panel.push({"msg": "hello"})
    out = tmp_path / "p.html"
    html = panel.export_html(out)
    assert out.read_text(encoding="utf-8") == html
    assert "window.canvas={" in html and "standalone:true" in html
    assert "<div id=t>waiting</div>" in html
    # the shim comes BEFORE the panel's own script, so canvas.onPush exists
    assert html.index("standalone:true") < html.index("canvas.onPush(")
    assert _replay(html) == [{"j": {"msg": "hello"}}]       # last push only


def test_export_replays_explicit_frames_and_escapes_script_close(tmp_path):
    canvas = danvas.Canvas()
    panel = canvas.custom(html="<p>x</p>", name="q")
    html = panel.export_html(frames=[{"t": "</script><b>"}, b"\x00\x01binary"])
    # a "</" inside a payload must not end the replay <script> early
    assert "</script><b>" not in html.split("var F=")[1].split("function b64")[0]
    got = _replay(html)
    assert got[0] == {"j": {"t": "</script><b>"}}
    assert base64.b64decode(got[1]["b"]) == b"\x00\x01binary"


def test_model3d_export_frames_are_layers_then_view():
    np = pytest.importorskip("numpy")
    canvas = danvas.Canvas()
    v = canvas.model3d("part")
    v.layer("cloud").points(np.zeros((5, 3)))
    v.layer("cloud").visible = False
    v.view("iso")
    frames = v._export_frames()
    assert isinstance(frames[0], bytes) and frames[0].startswith(b"DVL1")
    assert {"cmd": "visible", "layer": "cloud", "on": False} in frames
    assert frames[-1]["cmd"] == "view" and frames[-1]["preset"] == "iso"
    html = v.export_html()
    assert "xeokit-sdk" in html and "standalone:true" in html


def test_standalone_document_placement():
    full = "<!doctype html><html><head><meta charset=utf-8></head><body><p>hi</p></body></html>"
    doc = _standalone_document(full, [{"a": 1}], title="T<1>")
    assert doc.index("<head>") < doc.index("standalone:true") < doc.index("<p>hi</p>")
    assert doc.index("var F=") < doc.index("</body>")
    assert "<title>T&lt;1></title>" in doc
    frag = "<div>frag</div><script>canvas.onPush(function(){})</script>"
    doc = _standalone_document(frag, [])
    assert doc.startswith("<!doctype html>") and doc.index("standalone:true") < doc.index("<div>frag")


# -- browser: the file really opens and replays --------------------------------

playwright_sync = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def test_custom_export_opens_from_disk_and_replays(browser, tmp_path):
    canvas = danvas.Canvas()
    panel = canvas.custom(
        html="<div id=t>waiting</div>",
        js="canvas.onPush(function(d){document.getElementById('t').textContent='got '+d.n});",
        name="p")
    panel.push({"n": 1})
    panel.push({"n": 42})
    out = tmp_path / "p.html"
    panel.export_html(out)
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(out.as_uri())
    page.wait_for_function("() => document.body.innerText.includes('got 42')", timeout=10_000)
    assert not errors, errors


def test_model3d_export_opens_from_disk_and_renders(browser, tmp_path):
    np = pytest.importorskip("numpy")
    canvas = danvas.Canvas()
    v = canvas.model3d("part", w=600, h=400)
    pts = np.random.default_rng(0).uniform(-1, 1, (100, 3))
    v.layer("cloud").points(pts, color_by=pts[:, 0])
    v.view("iso")
    out = tmp_path / "part.html"
    v.export_html(out)
    page = browser.new_page(viewport={"width": 800, "height": 600})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(out.as_uri())
    try:
        page.wait_for_function(
            "() => document.body.innerText.includes('RENDER COMPLETE')", timeout=40_000)
    except playwright_sync.TimeoutError:
        pytest.skip("xeokit did not load from its CDN (no network?)")
    assert not errors, errors
