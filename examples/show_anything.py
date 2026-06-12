"""canvas.show(value) — let PyCanvas auto-pick the panel for any value.

You don't have to choose a component. ``show()`` inspects the value and inserts
the panel that renders it best — a DataFrame becomes a table, a figure an image,
Markdown text renders, a dict prints as JSON, and anything with a Jupyter rich
repr (``_repr_html_`` / ``_repr_png_``) shows its rich view. Works in plain
scripts (no IPython needed) and in notebooks alike.

Run:  python examples/show_anything.py
"""

import pycanvas

canvas = pycanvas.Canvas()

# A string of Markdown -> rendered text.
canvas.show("# Dashboard\nA few values, each auto-rendered.", x=40, y=40,
            label="notes", draggable=False, resizable=False, grabbable=False)

# Records (list of dicts) -> a Table.
canvas.show(
    [
        {"sensor": "temp", "value": 21.4, "unit": "C"},
        {"sensor": "humidity", "value": 48, "unit": "%"},
        {"sensor": "pressure", "value": 1013, "unit": "hPa"},
    ]*30000,
    x=440, y=40, label="readings",
)

# A nested structure -> pretty JSON.
canvas.show({"status": "ok", "uptime_s": 3600, "tags": ["a", "b"]},
            x=40, y=320, label="state")

# A plain scalar -> a Label.
canvas.show(42.0, x=440, y=320, label="count")
canvas.show([42.0, 890, 324,3214,214,124,12], x=440, y=320, label="count")

# Re-show under the same name to replace a panel in place (e.g. in a loop):
canvas.show("starting…", name="live", x=40, y=520, label="status")
canvas.show("ready ✔", name="live", x=40, y=520, label="status")  # replaces it

print("Each value was auto-rendered by canvas.show — no component chosen by hand.")
canvas.serve(port=8000, hot_reload=True)
