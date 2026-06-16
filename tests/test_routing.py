"""Action routing with field coercion: @panel.on(event, fields=...).

Covers the new fields= coercion on the shared _EventRouter (React/Custom): named
payload fields are coerced before the handler runs, a bad value drops the message
instead of crashing the handler, and the existing routing/viewer behaviour is
unchanged.
"""

import pycanvas


def _panel():
    # event_key="action" so the handlers route on the payload's "action" field,
    # the convention an app like examples/hackathon.py uses.
    return pycanvas.React(source="function Component(){ return null }",
                          name="p", event_key="action")


def test_on_routes_by_event():
    p = _panel()
    seen = []
    p.on("add")(lambda msg: seen.append(msg["x"]))
    p.on("sub")(lambda msg: seen.append(-msg["x"]))
    p._handle_input({"action": "add", "x": 1})
    p._handle_input({"action": "sub", "x": 2})
    assert seen == [1, -2]


def test_fields_coerces_present_values():
    p = _panel()
    got = {}
    @p.on("award", fields={"id": str, "points": int})
    def _(msg):
        got.update(msg)
    # Values arrive as strings off the wire; the handler should see real types.
    p._handle_input({"action": "award", "id": "t1", "points": "5"})
    assert got["points"] == 5 and isinstance(got["points"], int)
    assert got["id"] == "t1"


def test_fields_bad_value_drops_message():
    p = _panel()
    calls = []
    @p.on("set", fields={"qty": int})
    def _(msg):
        calls.append(msg)
    p._handle_input({"action": "set", "qty": "not-a-number"})
    assert calls == []          # handler never ran — message dropped, not crashed


def test_fields_missing_field_left_as_is():
    p = _panel()
    calls = []
    @p.on("set", fields={"qty": int})
    def _(msg):
        calls.append(msg)
    # No qty in the payload: coercion skips it and the handler still runs.
    p._handle_input({"action": "set", "item": "Laptop"})
    assert calls == [{"action": "set", "item": "Laptop"}]


def test_fields_handler_receives_viewer_when_declared():
    p = _panel()
    seen = {}
    @p.on("go", fields={"n": int})
    def _(msg, viewer):
        seen["n"] = msg["n"]
        seen["role"] = viewer.get("role")
    p._handle_input({"action": "go", "n": "3"}, viewer={"role": "admin"})
    assert seen == {"n": 3, "role": "admin"}


def test_fields_does_not_affect_other_events():
    p = _panel()
    seen = []
    p.on("typed", fields={"n": int})(lambda m: seen.append(("typed", m["n"])))
    p.on("plain")(lambda m: seen.append(("plain", m["n"])))
    p._handle_input({"action": "plain", "n": "raw"})   # no coercion on this route
    assert seen == [("plain", "raw")]
