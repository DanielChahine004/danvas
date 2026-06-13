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


def test_auto_height_panel_keeps_fitting_in_a_grid():
    canvas = pycanvas.Canvas()
    with canvas.grid(cols=2, slot=(300, 200), gap=10, origin=(0, 0)):
        md = canvas.markdown("# hi", name="notes", h="auto")
    # Grid positions it, but h='auto' is preserved (not forced to slot height).
    assert (md.x, md.y) == (0, 0)
    assert md._auto_h is True
