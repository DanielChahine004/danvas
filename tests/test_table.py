"""Table normalization, profiles/distributions, and the native React panel."""

import json

import pycanvas
from pycanvas.components.table import _normalize, _column_profile


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


def test_column_profile_reports_dtype_and_missing():
    # Integer column with one missing value: dtype is int, and the null badge
    # reflects the share that's empty; the hover tip carries the stats.
    p = _column_profile([1, 2, None, 4], numeric=True)
    assert p["meta"].startswith("int")
    assert "25% null" in p["meta"]
    assert "min 1" in p["tip"] and "max 4" in p["tip"]


def test_column_profile_floats_and_categoricals():
    assert _column_profile([1.5, 2.0], numeric=True)["meta"] == "float"
    cat = _column_profile(["a", "b", "a"], numeric=False)
    assert cat["meta"] == "str"
    assert "2 unique" in cat["tip"]


def test_table_is_a_native_react_panel_with_data():
    # The table renders as a native React component (sharp at any zoom): its
    # headers/rows/profiles ride in the JSON `data` prop, the interactive JSX in
    # `source`.
    props = pycanvas.Table([{"name": "a", "v": 1}, {"name": "b", "v": 2}]).register_props()
    assert "source" in props and "data" in props and "html" not in props
    data = json.loads(props["data"])
    assert data["cols"] == ["name", "v"]
    assert ["a", "1"] in data["rows"]          # cells are display strings
    assert data["numeric"] == [False, True]    # v is the numeric column
    assert "pc-th-meta" in props["source"] and "pc-head" in props["source"]


def test_distribution_data_carries_clickable_predicates():
    # Each column's distribution rides in `data`: numeric bins carry lo/hi (a
    # click filters to that range), categorical bars carry their value. The JSX
    # holds the active-filter chip.
    props = pycanvas.Table([{"cat": "a", "n": 1}, {"cat": "b", "n": 2},
                            {"cat": "a", "n": 9}]).register_props()
    data = json.loads(props["data"])
    cat, num = data["dists"][0], data["dists"][1]
    assert num["num"] is True and "lo" in num["bars"][0] and "hi" in num["bars"][0]
    assert cat["num"] is False and cat["bars"][0]["val"] == "a"
    assert "pc-chip" in props["source"]


def test_numeric_distribution_caption_has_min_mean_max():
    data = json.loads(pycanvas.Table({"n": [1, 5, 9]}).register_props()["data"])
    cap = data["dists"][0]["cap"]
    assert cap[0] == "1" and cap[-1] == "9" and cap[1].startswith("μ")


def test_table_renders_filter_and_pagination_hooks():
    src = pycanvas.Table([{"x": 1}, {"x": 2}]).register_props()["source"]
    assert "pc-filter" in src and "pc-dist" in src and "pc-pager" in src


def test_table_auto_height_sets_the_react_flag():
    # h="auto" flows through insert to the React auto-height flag (autoH) — the
    # native counterpart to the old iframe fit machinery.
    canvas = pycanvas.Canvas()
    t = canvas.table([{"x": 1}], name="data", h="auto")
    assert t._auto_h is True
    assert t.register_props()["autoH"] is True


def test_table_defaults_to_auto_height():
    # React-based panels (Table included) fit their content by default; pinning a
    # numeric height turns auto-height off.
    t = pycanvas.Table([{"x": 1}])           # no height given → auto-fit
    assert t._auto_h is True
    assert t.register_props()["autoH"] is True
    pinned = pycanvas.Table([{"x": 1}], h=300)
    assert pinned._auto_h is False
    assert pinned.register_props()["autoH"] is False
