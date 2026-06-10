"""Tests for the value -> panel dispatcher and the display components."""

import pycanvas
from pycanvas import Image, Label, Markdown, Table, panel_for
from pycanvas.components import Custom


def test_dispatch_strings():
    assert isinstance(panel_for("ok"), Label)                 # short -> Label
    assert isinstance(panel_for("line\nline\nline"), Markdown)  # multiline -> Markdown
    assert isinstance(panel_for("x" * 200), Markdown)          # long -> Markdown


def test_dispatch_scalars_and_structures():
    assert isinstance(panel_for(42), Label)
    # A bare dict / list of scalars renders as JSON in a Custom panel.
    assert isinstance(panel_for({"a": 1}), Custom)
    assert isinstance(panel_for([1, 2, 3]), Custom)


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

    panel = panel_for(Fig())
    assert "data:image/png;base64," in panel.register_props()["html"]


def test_existing_component_passes_through():
    lbl = pycanvas.Label("x", value="v")
    assert panel_for(lbl) is lbl


def test_markdown_renders_headings_and_lists():
    html = Markdown("# Title\n\n- a\n- b\n\n**b** and `c`").register_props()["html"]
    assert "<h1>" in html and "<li>" in html
    assert "<strong>b</strong>" in html and "<code>c</code>" in html


def test_table_from_records_has_header_and_cells():
    html = Table([{"name": "a", "v": 1}, {"name": "b", "v": 2}]).register_props()["html"]
    assert "<th>name</th>" in html and "<td>a</td>" in html


def test_table_from_dict_of_columns():
    html = Table({"x": [1, 2], "y": [3, 4]}).register_props()["html"]
    assert "<th>x</th>" in html and "<td>4</td>" in html


def test_image_from_bytes_sniffs_mime():
    html = Image(b"\xff\xd8\xff\x00jpeg").register_props()["html"]
    assert "data:image/jpeg;base64," in html


def test_show_inserts_with_unique_names_and_replaces():
    c = pycanvas.Canvas()
    a = c.show("hello")
    b = c.show([{"x": 1}])
    assert a.name != b.name
    assert a in c._components and b in c._components
    # Re-showing under an explicit name replaces in place.
    first = c.show("one", name="slot")
    second = c.show("two", name="slot")
    assert first not in c._components and second in c._components
