"""Canvas-as-hub: any canvas can pull *other* running canvases in from its UI.

Every ``serve()`` is a merge hub by default (``merge=True``): a 🧩 panel appears
bottom-left where you paste another canvas's URL and its panels compose in
alongside this one's, live — interactions route back to the canvas that owns them.

Run two plain canvases in other terminals, e.g.::

    python examples/hello_world.py            # on :8000
    python examples/robot_control.py          # served on another port

then run this hub and, in its 🧩 panel, add ``127.0.0.1:8000`` (and the other).
Their panels appear next to this hub's own label. An *empty* hub works too — just
delete the label below and you have a neutral aggregator you can serve and merge
into on the fly (no restart).
"""
import danvas

canvas = danvas.Canvas()
canvas.label(
    "hint",
    "Merge hub — click the 🧩 panel (bottom-left) and add another canvas's URL "
    "(e.g. 127.0.0.1:8000). Its panels appear here; drag/click them and it drives "
    "the owning canvas.",
    x=40, y=40, w=460, h="auto",
)

# merge=True is the default; shown here only to be explicit.
canvas.serve(port=8080, merge=True)
