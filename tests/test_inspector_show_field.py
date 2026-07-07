"""Inspector ⧉ pop-out: a detail-row field becomes its own panel.

The browser sends {action: "show_field", key, field}; the inspector resolves
the field's live value (attribute / _props / dict item — same priority as the
detail view) and canvas.show()s it beside itself, named after row.field so a
second click refreshes in place instead of stacking duplicates.
"""

import danvas


def _inspector_with_rows(canvas):
    ins = canvas.inspector()
    ins._handle_input({"action": "refresh"})       # builds _row_targets
    return ins


def test_show_field_pops_a_panel_with_the_live_value():
    canvas = danvas.Canvas()
    canvas.slider("r", min=1, max=9, default=5)
    ins = _inspector_with_rows(canvas)
    before = len(canvas.components)

    ins._handle_input({"action": "show_field", "key": "r", "field": "CONTRACT"})

    assert len(canvas.components) == before + 1
    panel = canvas["__inspect__r.CONTRACT"]
    assert panel._props.get("label") == "r.CONTRACT"
    assert panel._ephemeral is True


def test_show_field_same_field_replaces_not_stacks():
    canvas = danvas.Canvas()
    canvas.slider("r")
    ins = _inspector_with_rows(canvas)

    ins._handle_input({"action": "show_field", "key": "r", "field": "value"})
    n = len(canvas.components)
    ins._handle_input({"action": "show_field", "key": "r", "field": "value"})
    assert len(canvas.components) == n           # replaced in place


def test_show_field_unknown_key_or_field_is_a_noop():
    canvas = danvas.Canvas()
    canvas.slider("r")
    ins = _inspector_with_rows(canvas)
    n = len(canvas.components)
    ins._handle_input({"action": "show_field", "key": "ghost", "field": "x"})
    ins._handle_input({"action": "show_field", "key": "r", "field": None})
    assert len(canvas.components) == n


def test_show_field_handlers_dict_falls_back_to_repr():
    # panel.handlers holds HandlerInfo objects (not JSON) — the pop-out must
    # still show something rather than error.
    canvas = danvas.Canvas()
    s = canvas.slider("r")
    s.on_change(lambda v: None)
    ins = _inspector_with_rows(canvas)
    before = len(canvas.components)
    ins._handle_input({"action": "show_field", "key": "r", "field": "handlers"})
    assert len(canvas.components) == before + 1


def test_register_props_bake_current_rows():
    # register_message calls register_props_for (NOT register_props) — the
    # bake must live on the *_for override or every inspector registers with
    # the empty "[]" from __init__ and opens blank until a manual Refresh.
    import json
    canvas = danvas.Canvas()
    canvas.slider("r")
    ins = canvas.inspector()
    props = ins.register_props_for(None, None)
    rows = json.loads(json.loads(props["data"])["rows"])
    assert any(r.get("name") == "r" for r in rows)
