"""Tests for the value -> panel dispatcher and the display components."""

import json

import danvas
from danvas import Image, Label, Markdown, Table, panel_for
from danvas.components import Custom


def test_dispatch_strings():
    assert isinstance(panel_for("ok"), Label)                 # short -> Label
    assert isinstance(panel_for("line\nline\nline"), Markdown)  # multiline -> Markdown
    assert isinstance(panel_for("x" * 200), Markdown)          # long -> Markdown


def test_dispatch_smart_strings():
    # Markdown syntax renders as Markdown even when short and single-line.
    assert isinstance(panel_for("use **bold** here"), Markdown)
    assert isinstance(panel_for("# Heading"), Markdown)
    assert isinstance(panel_for("- a bullet"), Markdown)
    assert isinstance(panel_for("see `code`"), Markdown)
    # Plain prose is not misread as Markdown (single * isn't a marker).
    assert isinstance(panel_for("a * b * c = d"), Label)
    assert isinstance(panel_for("just a plain sentence"), Label)
    # Literal HTML renders as HTML; a bare URL becomes a clickable link.
    assert isinstance(panel_for("<h1>hi</h1>"), Custom)
    assert isinstance(panel_for("https://example.com"), Markdown)


def test_dispatch_image_url_and_bytes():
    assert isinstance(panel_for("https://cdn.test/pic.png"), Image)
    assert isinstance(panel_for("data:image/png;base64,iVBOR"), Image)
    assert isinstance(panel_for(b"\x89PNG\r\n\x1a\nfake"), Image)
    # Non-image bytes fall through to a repr label.
    assert isinstance(panel_for(b"not an image"), Label)


def test_dispatch_file_paths(tmp_path):
    import pathlib

    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
    csvf = tmp_path / "a.csv"
    csvf.write_text("name,age\nx,1\ny,2\n")
    mdf = tmp_path / "a.md"
    mdf.write_text("# Title\n- a\n- b")
    jsonf = tmp_path / "a.json"
    jsonf.write_text('[{"k": 1}, {"k": 2}]')

    assert isinstance(panel_for(str(png)), Image)
    assert isinstance(panel_for(str(csvf)), Table)       # CSV -> Table (stdlib)
    assert isinstance(panel_for(str(mdf)), Markdown)
    assert isinstance(panel_for(str(jsonf)), Table)      # JSON records -> Table
    assert isinstance(panel_for(pathlib.Path(png)), Image)  # pathlib.Path
    # A path-shaped string that isn't a real file stays a plain string.
    assert isinstance(panel_for("nope.png"), Label)


def test_dispatch_scalars_and_structures():
    assert isinstance(panel_for(42), Label)
    # Flat dict / list of scalars render as a key/value (or single-column) table
    # via smart dispatch; a nested structure still falls to a JSON Custom panel.
    assert isinstance(panel_for({"a": 1}), Table)
    assert isinstance(panel_for([1, 2, 3]), Table)
    assert isinstance(panel_for({"a": {"b": 1}}), Custom)


def test_dispatch_tabular():
    # Records and matrices route to a Table.
    assert isinstance(panel_for([{"x": 1}, {"x": 2}]), Table)
    assert isinstance(panel_for([[1, 2], [3, 4]]), Table)


def test_dispatch_rich_repr_is_ipython_free():
    class Widget:
        def _repr_html_(self):
            return "<b>hi</b>"

    panel = panel_for(Widget())
    assert isinstance(panel, Custom)
    assert "<b>hi</b>" in panel.register_props()["html"]


def test_dispatch_png_repr_to_image_html():
    class Fig:
        def _repr_png_(self):
            return b"\x89PNG\r\n\x1a\nfake"

    # A bare _repr_png_ (no module hint) is a rich-repr Custom panel wrapping the
    # data-URI <img> — not the Image component — so its html carries the URI.
    panel = panel_for(Fig())
    assert "data:image/png;base64," in panel.register_props()["html"]


def test_existing_component_passes_through():
    lbl = danvas.Label("x", value="v")
    assert panel_for(lbl) is lbl


def test_label_renders_value_as_plain_text():
    # Label is a native React panel showing its value as plain text (React escapes
    # at render); markup is markdown's job. The raw value rides in the data prop.
    lbl = Label("status", value="a < b & **not bold**")
    data = json.loads(lbl.register_props()["data"])
    assert data["text"] == "a < b & **not bold**"   # stored raw, not HTML
    assert "<strong>" not in lbl.register_props()["source"]  # source does no markdown


def test_label_auto_height_enabled_via_insert():
    # h="auto" must flag the panel (insert() keys off `_auto_h`); on the old native
    # Label it warned and did nothing.
    canvas = danvas.Canvas()
    lbl = canvas.label("status", "ready", h="auto")
    assert lbl._auto_h is True


def test_label_update_streams_without_reload():
    # update() pushes into the live iframe ({"post": ...}) instead of replacing the
    # html, so a per-loop status line never reloads the frame.
    sent = []
    lbl = Label("status", value="idle")
    lbl._send_update = lambda payload, *a, **k: sent.append(payload)
    lbl.update("running")
    assert sent == [{"post": "running"}]
    assert lbl.value == "running"


def test_markdown_renders_headings_and_lists():
    # Markdown is a native React panel now; the rendered HTML is exposed via
    # `.html` (and carried to the frontend inside the JSON `data` prop).
    md = Markdown("# Title\n\n- a\n- b\n\n**b** and `c`")
    html = md.html
    assert "<h1>" in html and "<li>" in html
    assert "<strong>b</strong>" in html and "<code>c</code>" in html
    # It renders natively (React), and the HTML rides along in the data prop.
    assert md.component == "React"
    assert "<h1>" in md.register_props()["data"]


def test_table_from_records_has_header_and_cells():
    props = Table([{"name": "a", "v": 1}, {"name": "b", "v": 2}]).register_props()
    # The table is a native React panel: headers + cells (as display strings)
    # ride in the JSON `data` prop, which the browser renders/paginates.
    data = json.loads(props["data"])
    assert data["cols"] == ["name", "v"] and ["a", "1"] in data["rows"]
    # The numeric column is flagged, and the JSX carries sortable headers + the
    # per-column distribution SVG.
    assert data["numeric"] == [False, True]
    assert "pc-head" in props["source"] and "<svg" in props["source"]


def test_table_from_dict_of_columns():
    data = json.loads(Table({"x": [1, 2], "y": [3, 4]}).register_props()["data"])
    assert data["cols"] == ["x", "y"] and ["2", "4"] in data["rows"]


def test_image_from_bytes_sniffs_mime():
    data = json.loads(Image(b"\xff\xd8\xff\x00jpeg").register_props()["data"])
    assert data["src"].startswith("data:image/jpeg;base64,")


def test_show_inserts_with_unique_names_and_replaces():
    c = danvas.Canvas()
    a = c.show("hello")
    b = c.show([{"x": 1}])
    assert a.name != b.name
    assert a in c._components and b in c._components
    # Re-showing under an explicit name replaces in place.
    first = c.show("one", name="slot")
    second = c.show("two", name="slot")
    assert first not in c._components and second in c._components
