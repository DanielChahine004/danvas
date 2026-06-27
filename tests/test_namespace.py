"""The Inspector's globals namespace is captured automatically.

A Canvas defaults its ``_namespace`` (what the Inspector's "globals" view lists)
to the globals of wherever it was created, so the variable explorer works without
an explicit ``serve(namespace=globals())``. Explicit namespaces still win.
"""

import danvas

_MODULE_SENTINEL = 1234        # a module-level var this test file owns


def test_canvas_auto_captures_caller_globals():
    canvas = danvas.Canvas()
    assert canvas._namespace is not None
    # It's *this* test module's globals (a live reference), not danvas's.
    assert canvas._namespace.get("_MODULE_SENTINEL") == 1234
    assert not (canvas._namespace.get("__name__") or "").startswith("danvas")


def test_auto_namespace_is_live():
    canvas = danvas.Canvas()
    canvas._namespace["_added_after"] = "hi"     # same dict as this module's globals
    try:
        assert globals().get("_added_after") == "hi"
    finally:
        globals().pop("_added_after", None)


def test_inspector_inherits_canvas_namespace():
    canvas = danvas.Canvas()
    insp = danvas.Inspector(name="insp")
    canvas.insert(insp)
    assert insp._namespace is canvas._namespace


def test_explicit_inspector_namespace_is_kept():
    canvas = danvas.Canvas()
    insp = danvas.Inspector(name="insp", namespace={"x": 1})
    canvas.insert(insp)
    assert insp._namespace == {"x": 1}           # not overwritten by the canvas
