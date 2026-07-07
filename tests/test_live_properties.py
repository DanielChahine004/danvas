"""Getter/setter symmetry across the component spectrum.

One convention everywhere: reading a property returns the live state, and
assigning routes through the component's own update path — pushed to
browsers, replayed on reconnect, and silent (a programmatic write never
fires the input handlers; that path belongs to the browser).
"""

import pytest

import danvas


def test_visible_setter_is_hide_unhide():
    canvas = danvas.Canvas()
    s = canvas.slider("r")
    assert s.visible
    s.visible = False
    assert not s.visible and s in canvas.components   # hidden, not removed
    s.visible = True
    assert s.visible


def test_visible_setter_requires_a_canvas():
    with pytest.raises(RuntimeError):
        danvas.Slider().visible = False


def test_label_and_markdown_text():
    canvas = danvas.Canvas()
    lab = canvas.label("l", "hi")
    lab.text = "bye"
    assert lab.text == "bye"
    md = canvas.markdown("# a")
    md.text = "# b"
    assert md.text == "# b" and "<h1" in md.html


def test_webview_url_assign_is_navigate():
    canvas = danvas.Canvas()
    web = canvas.webview("https://example.com")
    web.url = "https://example.org"
    assert "example.org" in web.url
    # navigate()'s YouTube rewrite applies to assignment too.
    web.url = "https://www.youtube.com/watch?v=abc123xyz00"
    assert "/embed/abc123xyz00" in web.url


def test_model3d_model_roundtrip():
    canvas = danvas.Canvas()
    m3 = canvas.model3d("m")
    assert m3.model is None
    m3.model = b"glTF-bytes"
    assert m3.model == b"glTF-bytes"


def test_inspector_source_switches_views():
    canvas = danvas.Canvas()
    ins = canvas.inspector()
    assert ins.source == "components"
    ins.source = "system"
    assert ins.source == "system"
    with pytest.raises(ValueError):
        ins.source = "bogus"


def test_download_upload_text():
    canvas = danvas.Canvas()
    dl = canvas.download("dl", source=b"x", text="Get")
    dl.text = "Fetch"
    assert dl.text == "Fetch"
    up = canvas.upload("up", text="Drop")
    up.text = "Drop here"
    assert up.text == "Drop here"


def test_table_selected_assignable_and_validated():
    canvas = danvas.Canvas()
    t = canvas.table({"A": [1, 2, 3]})
    fired = []
    t.on_select(fired.append)
    t.selected = [2, 0]
    assert t.selected == [0, 2]          # normalised, deduped, sorted
    assert fired == []                   # programmatic selection is silent
    t.selected = []
    assert t.selected == []
    with pytest.raises(IndexError):
        t.selected = [9]


def test_image_src_is_the_wire_form():
    # The getter returns the canonical encoded state (data: URI / URL) — the
    # same string a peer SDK reads off the shared property plane; the setter
    # takes anything update() accepts.
    canvas = danvas.Canvas()
    img = canvas.image(b"\x89PNG\r\n\x1a\nfake")
    assert img.src.startswith("data:image/")
    img.src = "https://example.com/x.png"
    assert img.src == "https://example.com/x.png"
    img.fit = "cover"
    assert img.fit == "cover"
    with pytest.raises(ValueError):
        img.fit = "stretch"
