"""Markdown conversion — pipe tables render on every path, including the
zero-dependency built-in fallback (user feedback: table support silently
depended on which markdown library happened to be installed)."""

import danvas
from danvas.components.markdown import _basic_md

TABLE = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"


def test_panel_renders_pipe_tables_on_any_conversion_path():
    # Holds with the `markdown` package, `markdown-it-py`, or neither — the
    # built-in fallback now covers pipe tables too.
    md = danvas.Markdown(TABLE)
    assert "<table>" in md.html and "<th>a</th>" in md.html


def test_basic_md_pipe_table():
    html = _basic_md(TABLE)
    assert "<table><thead><tr><th>a</th><th>b</th></tr></thead>" in html
    assert "<tr><td>1</td><td>2</td></tr>" in html
    assert html.count("<tr>") == 3


def test_basic_md_table_cells_get_inline_formatting_and_escaping():
    html = _basic_md("| **bold** | x<y |\n|:---|---:|\n| `c` | ok |")
    assert "<th><strong>bold</strong></th>" in html   # inline markdown works
    assert "x&lt;y" in html                           # raw HTML stays escaped
    assert "<td><code>c</code></td>" in html


def test_basic_md_ragged_rows_pad_and_truncate_to_header():
    html = _basic_md("| a | b |\n|---|---|\n| only |\n| 1 | 2 | extra |")
    assert "<tr><td>only</td><td></td></tr>" in html
    assert "extra" not in html


def test_basic_md_pipe_line_without_rule_stays_a_paragraph():
    # A lone |…| line isn't a table (no |---| rule beneath) — paragraph text.
    html = _basic_md("| just some | text |")
    assert "<table>" not in html and "<p>" in html


def test_basic_md_table_directly_after_paragraph():
    # The paragraph gatherer must stop at a table row rather than eat it.
    html = _basic_md("intro line\n" + TABLE)
    assert "<p>intro line</p>" in html and "<table>" in html
