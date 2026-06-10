"""Table: show tabular data as a panel.

Accepts a pandas DataFrame/Series (rendered via its own ``to_html``), or plain
Python tabular data: a list of dicts, a list of rows (lists/tuples), or a dict
of columns. pandas is duck-typed, so it isn't a hard dependency.
"""

import html as _html

from .custom import Custom
from ._doc import document

_TABLE_CSS = (
    "table{border-collapse:collapse;width:100%;font-size:12px}"
    "th,td{border:1px solid #e2e8f0;padding:4px 8px;text-align:left;"
    "white-space:nowrap}"
    "th{position:sticky;top:0;background:#f8fafc;font-weight:600}"
    "tr:nth-child(even) td{background:#f8fafc}"
    "td{font-variant-numeric:tabular-nums}"
)


class Table(Custom):
    component = "Custom"
    default_w = 520
    default_h = 360

    def __init__(self, data, name="table", label=None, width=520, height=360):
        super().__init__(html=self._render(data), name=name, label=label,
                         width=width, height=height)

    def update(self, data):
        """Replace the table contents, live."""
        super().update(self._render(data))

    def _render(self, data):
        return document(f"<div style='overflow:auto'>{_to_table_html(data)}</div>",
                        _TABLE_CSS)


def _to_table_html(data):
    """Render supported tabular data to an HTML ``<table>``."""
    # pandas DataFrame / Series (and anything else exposing to_html).
    to_html = getattr(data, "to_html", None)
    if callable(to_html):
        try:
            return to_html(border=0)
        except Exception:
            pass
    # dict of columns: {col: [values...]}.
    if isinstance(data, dict):
        cols = list(data.keys())
        rows = list(zip(*[list(data[c]) for c in cols])) if cols else []
        return _html_table(cols, rows)
    # list of dicts -> columns from the union of keys (first-seen order).
    if isinstance(data, (list, tuple)) and data and isinstance(data[0], dict):
        cols = []
        for row in data:
            for k in row:
                if k not in cols:
                    cols.append(k)
        rows = [[row.get(c, "") for c in cols] for row in data]
        return _html_table(cols, rows)
    # list of rows (lists/tuples): no header.
    if isinstance(data, (list, tuple)):
        rows = [list(r) if isinstance(r, (list, tuple)) else [r] for r in data]
        return _html_table(None, rows)
    raise TypeError(f"can't render {type(data).__name__} as a table")


def _cell(v):
    return _html.escape("" if v is None else str(v))


def _html_table(cols, rows):
    parts = ["<table>"]
    if cols is not None:
        parts.append("<thead><tr>"
                     + "".join(f"<th>{_cell(c)}</th>" for c in cols)
                     + "</tr></thead>")
    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>" + "".join(f"<td>{_cell(v)}</td>" for v in row) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)
