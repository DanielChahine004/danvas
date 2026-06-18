"""Auto-layout containers: canvas.grid / column / row."""

import pycanvas


def test_grid_flows_left_to_right_then_wraps():
    canvas = pycanvas.Canvas()
    with canvas.grid(cols=2, slot=(100, 50), gap=10, origin=(0, 0)):
        a = canvas.label("a")
        b = canvas.label("b")
        c = canvas.label("c")   # wraps to the second row
    assert (a.x, a.y) == (0, 0)
    assert (b.x, b.y) == (110, 0)          # next column: 100 + gap
    assert (c.x, c.y) == (0, 60)           # next row: 50 + gap
    # Slot size fills in the panel dimensions.
    assert (a.w, a.h) == (100, 50)


def test_column_stacks_by_natural_height():
    canvas = pycanvas.Canvas()
    # A label is 84 tall by default, a button 84, a slider 96 — the column keeps
    # each panel's own height and advances the cursor by it (not a uniform slot).
    with canvas.column(width=320, gap=10, origin=(40, 40)):
        a = canvas.label("a")              # default_h 84
        b = canvas.button("b")             # default_h 84
        c = canvas.slider("c")             # default_h 96
    assert (a.x, a.y, a.w) == (40, 40, 320)
    assert (b.x, b.y) == (40, 40 + 84 + 10)
    assert (c.x, c.y) == (40, 40 + 84 + 10 + 84 + 10)
    assert c.h == 96                       # natural height preserved


def test_column_width_none_keeps_each_panels_own_width():
    canvas = pycanvas.Canvas()
    with canvas.column(gap=10, origin=(0, 0)):
        a = canvas.label("a")              # default_w 240
    assert a.w == 240


def test_row_flows_by_natural_width():
    canvas = pycanvas.Canvas()
    with canvas.row(height=50, gap=10, origin=(0, 0)):
        a = canvas.label("a")              # default_w 240
        b = canvas.label("b")
    assert [p.y for p in (a, b)] == [0, 0]
    assert [p.x for p in (a, b)] == [0, 240 + 10]
    assert a.h == 50                       # common height applied


def test_explicit_position_overrides_the_grid():
    canvas = pycanvas.Canvas()
    with canvas.grid(cols=2, slot=(100, 50), gap=10, origin=(0, 0)):
        canvas.label("a")                  # claims slot 0
        b = canvas.label("b", x=500, y=500)  # explicit — bypasses the grid
        c = canvas.label("c")              # takes the *next* slot (1), not 0
    assert (b.x, b.y) == (500, 500)
    assert (c.x, c.y) == (110, 0)


def test_explicit_size_is_kept_position_from_grid():
    canvas = pycanvas.Canvas()
    with canvas.grid(cols=3, slot=(100, 50), gap=10, origin=(0, 0)):
        a = canvas.label("a", w=333)       # own width, grid height + position
    assert (a.x, a.y, a.w, a.h) == (0, 0, 333, 50)


def test_relative_anchor_wins_over_grid():
    canvas = pycanvas.Canvas()
    anchor = canvas.label("anchor", x=0, y=0, w=100, h=40)
    with canvas.grid(cols=2, slot=(100, 50), gap=10, origin=(900, 900)):
        b = canvas.label("b", below=anchor)
    assert (b.x, b.y) == (0, 0 + 40 + 16)  # anchored, not grid-placed


def test_layout_stack_unwinds_after_block():
    canvas = pycanvas.Canvas()
    with canvas.grid(cols=2):
        assert canvas._layout_stack
    assert not canvas._layout_stack
    # Outside the block, panels auto-cascade again (no Python-side position).
    loose = canvas.label("loose")
    assert loose.x is None and loose.y is None


def test_role_scoped_column_writes_overlay_not_base():
    canvas = pycanvas.Canvas()
    with canvas.column(roles="admin", gap=10, origin=(100, 100)):
        a = canvas.label("a", w=200, h=50)
        b = canvas.label("b", w=200, h=50)
    reg = canvas._bridge.register_message
    # admins replay the column positions...
    ra, rb = reg(a, role="admin"), reg(b, role="admin")
    assert (ra["x"], ra["y"]) == (100, 100)
    assert (rb["x"], rb["y"]) == (100, 160)        # stacked: 50 + gap
    # ...but the shared base is left unset, so other roles auto-cascade.
    base = reg(a, role="other")
    assert "x" not in base and "y" not in base
    assert a.x is None and a.y is None             # no Python-side base position


def test_role_scoped_row_is_isolated_per_role():
    canvas = pycanvas.Canvas()
    with canvas.row(roles="viewer", gap=10, origin=(0, 0)):
        p = canvas.label("p", w=80, h=40)
        q = canvas.label("q", w=80, h=40)
    reg = canvas._bridge.register_message
    rp, rq = reg(p, role="viewer"), reg(q, role="viewer")
    assert (rp["x"], rp["y"]) == (0, 0)
    assert (rq["x"], rq["y"]) == (90, 0)           # beside: 80 + gap
    # A different role sees no position from this viewer-scoped row.
    assert "x" not in reg(p, role="admin")


def test_auto_height_panel_keeps_fitting_in_a_grid():
    canvas = pycanvas.Canvas()
    with canvas.grid(cols=2, slot=(300, 200), gap=10, origin=(0, 0)):
        md = canvas.markdown("# hi", name="notes", h="auto")
    # Grid positions it, but h='auto' is preserved (not forced to slot height).
    assert (md.x, md.y) == (0, 0)
    assert md._auto_h is True


def test_label_defaults_to_auto_height():
    # A standalone label fits its content (no tall empty box)...
    canvas = pycanvas.Canvas()
    a = canvas.label("a")
    assert a._auto_h is True
    # ...but an explicit height pins it (auto-height off).
    b = canvas.label("b", h=120)
    assert b._auto_h is False
    assert b.h == 120


def test_label_default_auto_height_yields_to_grid_slot():
    # Unlike an explicit h="auto", a label's *default* auto-height defers to a
    # grid slot height so the grid stays uniform.
    canvas = pycanvas.Canvas()
    with canvas.grid(cols=2, slot=(100, 50), gap=10, origin=(0, 0)):
        a = canvas.label("a")
    assert a.h == 50
    assert a._auto_h is False


def _capture_broadcast(canvas):
    """Record every frame the bridge would broadcast (the wire is inert in tests,
    so a shared column/row refit's reflow request only shows up here)."""
    sent = []
    canvas._bridge.broadcast = lambda msg, **kw: sent.append(msg)
    return sent


def test_shared_column_refit_requests_a_browser_reflow():
    # A shared column packs in the browser, where the panels' real measured sizes
    # live — Python just sends the ordered group + origin/gap; the browser does
    # the placement (and re-runs it once a pending content-fit settles).
    canvas = pycanvas.Canvas()
    with canvas.column(width=200, gap=10, origin=(40, 40)) as col:
        a = canvas.label("a", h=50)
        b = canvas.label("b", h=50)
        c = canvas.label("c", h=50)
    sent = _capture_broadcast(canvas)
    col.refit()
    assert len(sent) == 1
    m = sent[0]
    assert m["type"] == "reflow" and m["kind"] == "column"
    assert m["ids"] == [a.id, b.id, c.id]            # insertion order = pack order
    assert (m["x0"], m["y0"], m["gap"]) == (40, 40, 10)


def test_shared_row_refit_requests_a_row_reflow():
    canvas = pycanvas.Canvas()
    with canvas.row(height=50, gap=10, origin=(0, 0)) as r:
        a = canvas.label("a", w=80)
        b = canvas.label("b", w=80)
    sent = _capture_broadcast(canvas)
    r.refit()
    assert sent[0]["kind"] == "row"
    assert sent[0]["ids"] == [a.id, b.id]


def test_shared_refit_excludes_panels_removed_since_insert():
    canvas = pycanvas.Canvas()
    with canvas.column(gap=10, origin=(0, 0)) as col:
        a = canvas.label("a")
        b = canvas.label("b")
        c = canvas.label("c")
    canvas.remove(b)
    sent = _capture_broadcast(canvas)
    col.refit()
    assert sent[0]["ids"] == [a.id, c.id]            # the gone panel drops out


def test_grid_refit_repacks_locally_by_slot():
    # A grid keeps uniform fixed slots, so it re-packs in Python (no browser
    # round-trip) straight back onto the slot grid.
    canvas = pycanvas.Canvas()
    with canvas.grid(cols=2, slot=(100, 50), gap=10, origin=(0, 0)) as g:
        a = canvas.label("a")
        b = canvas.label("b")
        c = canvas.label("c")
    a.set_layout(x=999, y=999)                       # knock one out of place
    g.refit()
    assert (a.x, a.y) == (0, 0)
    assert (b.x, b.y) == (110, 0)
    assert (c.x, c.y) == (0, 60)


def test_role_scoped_column_refit_rewrites_the_overlay():
    # A per-viewer-scoped column can't be expressed as a shared reflow, so it
    # re-packs locally from the panels' base sizes, re-emitting the role overlay.
    canvas = pycanvas.Canvas()
    with canvas.column(roles="admin", gap=10, origin=(100, 100)) as col:
        a = canvas.label("a", w=200, h=50)
        b = canvas.label("b", w=200, h=50)
    a.set_layout(h=120)
    col.refit()
    reg = canvas._bridge.register_message
    rb = reg(b, role="admin")
    assert (rb["x"], rb["y"]) == (100, 100 + 120 + 10)   # admin overlay re-packed
    # The shared base stays unset — refit kept the scoping.
    assert b.y is None
