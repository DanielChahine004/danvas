"""Cover the factory -> _make -> insert path, not just the components.

The component classes are exercised in isolation elsewhere (test_slider.py,
test_button.py, …). What was untested is the seam in between: the
``canvas.<component>(...)`` factories and ``_FactoryMixin._make``, which split
the placement/lock kwargs (``_INSERT_KEYS``) off from the constructor kwargs and
route them through ``Canvas.insert``. A regression there (a placement key leaking
into the component, a flag not applied, a name handle not bound) would not be
caught by the per-component tests. These assert the wiring.

No server is started — ``Canvas()`` builds a bridge in ``__init__`` and
``insert`` binds the component offline, so the factory path runs without
``serve()``.
"""

import danvas


def _canvas():
    return danvas.Canvas()


# -- placement flows through **place to insert -------------------------------
def test_factory_applies_position_size_and_handle():
    c = _canvas()
    s = c.slider("speed", min=0, max=180, x=10, y=20, w=300)
    assert s in c.components
    assert s.x == 10 and s.y == 20
    assert s.w == 300
    # The name is the canvas.<name> / canvas["<name>"] handle.
    assert c.speed is s
    assert c["speed"] is s


def test_factory_applies_lock_flags():
    c = _canvas()
    b = c.button("go", locked=True, draggable=False)
    assert b.locked is True
    assert b.draggable is False
    # Untouched flags keep their defaults.
    assert b.resizable is True and b.frame is True


def test_width_alias_routes_through_place():
    c = _canvas()
    s = c.slider("g", width=321)
    assert s.w == 321


# -- _make splits _INSERT_KEYS from constructor kwargs -----------------------
def test_constructor_kwargs_reach_component_not_insert():
    # A float step is a Slider constructor arg; it must land on the component
    # (and make it a float slider), not be swallowed as a placement key.
    c = _canvas()
    s = c.slider("gain", min=0, max=1, default=0.5, step=0.1)
    assert s.value == 0.5
    import json
    data = json.loads(s.register_props()["data"])
    assert data["step"] == 0.1


def test_placement_kwargs_do_not_leak_into_component_props():
    c = _canvas()
    s = c.slider("speed", x=5, y=6, locked=True)
    # x/y/locked are placement/lock keys — they must not appear as shape props.
    assert "x" not in s._props and "y" not in s._props
    assert "locked" not in s._props


def test_relative_placement_key_consumed_by_insert():
    c = _canvas()
    anchor = c.slider("a", x=0, y=0, w=100, h=50)
    s = c.slider("b", below=anchor, gap=10)
    # `below`/`gap` are placement keys: they position the panel and never reach
    # the component as constructor kwargs/props.
    assert s.y == anchor.y + anchor.h + 10
    assert "below" not in s._props and "gap" not in s._props


# -- name eviction: re-using a name swaps in place ---------------------------
def test_reusing_name_replaces_panel_in_place():
    c = _canvas()
    first = c.slider("dial")
    second = c.slider("dial")
    assert first is not second
    # Exactly one panel holds the name, and it's the newcomer.
    held = [comp for comp in c.components if comp.name == "dial"]
    assert held == [second]
    assert c["dial"] is second


# -- a representative across factory arg-shapes ------------------------------
def test_various_factories_register_and_place():
    c = _canvas()
    made = [
        c.toggle(["a", "b"], name="mode", x=1, y=1),
        c.label("lbl", value="hi", x=2, y=2),
        c.markdown("# title", name="md", x=3, y=3),
        c.button("press", x=4, y=4),
    ]
    for i, comp in enumerate(made, start=1):
        assert comp in c.components
        assert comp.x == i and comp.y == i
        # Each is reachable by its handle.
        assert c[comp.name] is comp


def test_show_autonames_and_places():
    c = _canvas()
    comp = c.show("hello", x=7, y=8)
    assert comp in c.components
    assert comp.x == 7 and comp.y == 8
    assert comp.name is not None and c[comp.name] is comp
