"""Table rendering and the h='auto' fit-loop fix."""

import pycanvas
from pycanvas.components.table import _normalize


def test_normalize_list_of_dicts_unions_keys():
    cols, rows = _normalize([{"a": 1, "b": 2}, {"a": 3, "c": 4}])
    assert cols == ["a", "b", "c"]
    assert rows == [[1, 2, ""], [3, "", 4]]


def test_flat_dict_renders_as_key_value_rows():
    # A scalar-valued dict (e.g. hyperparameters) becomes a 2-column key/value
    # table — much more readable than one very wide row.
    cols, rows = _normalize({"lr": 3e-4, "batch": 64, "opt": "adam"})
    assert cols == ["key", "value"]
    assert rows == [["lr", 3e-4], ["batch", 64], ["opt", "adam"]]


def test_dict_of_sequences_stays_columnar():
    # When every value is a sequence it's still treated as columns of data.
    cols, rows = _normalize({"a": [1, 2], "b": [3, 4]})
    assert cols == ["a", "b"]
    assert rows == [[1, 3], [2, 4]]


def test_empty_dict_is_an_empty_key_value_table():
    assert _normalize({}) == (["key", "value"], [])


def test_table_renders_filter_and_distribution_hooks():
    t = pycanvas.Table([{"x": 1}, {"x": 2}])
    html = t.register_props()["html"]
    assert "pc-filter" in html and "pc-dist" in html


def test_auto_height_table_sizes_from_content_not_panel():
    # Regression: under h="auto" the wrap must stop filling 100vh, else the
    # measured height tracks the panel height and the fit loop oscillates. The
    # CSS carries the `body.pc-auto-h` override that breaks that feedback...
    t = pycanvas.Table([{"x": 1}])
    html = t.register_props()["html"]
    assert "body.pc-auto-h .pc-wrap{height:auto}" in html
    assert "body.pc-auto-h .pc-scroll{overflow:visible" in html


def test_auto_height_panels_expose_the_hook_class():
    # ...and an h="auto" panel's injected fit script adds that class on <body>.
    canvas = pycanvas.Canvas()
    t = canvas.table([{"x": 1}], name="data", h="auto")
    html = t.register_props()["html"]
    assert "classList.add('pc-auto-h')" in html
    assert t._auto_h is True


def test_non_auto_table_still_has_the_rule_but_no_hook_class():
    # The CSS rule is harmless when inactive; without h="auto" the class that
    # activates it is never added, so fixed-height tables keep their scroll area.
    t = pycanvas.Table([{"x": 1}])           # not auto-height
    html = t.register_props()["html"]
    assert "classList.add('pc-auto-h')" not in html
