"""Canvas-as-hub: pull *other* running canvases in — from the UI or from code.

Every ``serve()`` is a merge hub by default (``merge=True``). Two equivalent ways
to compose another canvas in, both live and for every viewer:

* **UI** — the 🧩 panel (bottom-left): paste a canvas URL to add it.
* **Code** — ``canvas.merge(url)`` (its twin), e.g. from a button below, or before
  ``serve()`` to pre-compose; ``canvas.unmerge(url)`` drops it; ``canvas.merges``
  reads the set. A password-protected source takes ``password=``.

Merged panels compose alongside this hub's own; interactions on them route back to
the canvas that owns them. Run another canvas first, e.g.
``python examples/hello_world.py`` (on :8000), then run this and either click the
button or add its URL in the 🧩 panel.
"""
import danvas

canvas = danvas.Canvas()
canvas.label(
    "hint",
    "Merge hub — click **Bring in :8000** below (or use the 🧩 panel) to compose "
    "another running canvas in. Both do the same thing, for every viewer.",
    x=40, y=40, w=480, h="auto",
)

pull = canvas.button("pull", text="Bring in :8000", x=40, y=170, w=200)


@pull.on_click
def _():
    # the code twin of the 🧩 panel's "add" — merge a pre-decided canvas by URL
    canvas.merge("127.0.0.1:8000")


# You could also pre-compose before serving:  canvas.merge("127.0.0.1:8000")
canvas.serve(port=8080)
