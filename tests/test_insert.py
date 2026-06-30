"""Insert-time conveniences: queue= forwarding and relative placement."""

import json
import warnings

import pytest

import danvas


def test_factories_accept_queue_kwarg():
    canvas = danvas.Canvas()
    lbl = canvas.label("status", queue="latest")
    assert lbl.queue == "latest"
    # None (the default) keeps the component's own policy.
    feed = danvas.VideoFeed("cam")           # VideoFeed defaults to "latest"
    canvas.insert(feed)
    assert feed.queue == "latest"


def test_insert_rejects_bad_queue():
    canvas = danvas.Canvas()
    with pytest.raises(ValueError):
        canvas.label("status", queue="newest")


# -- decorative: the floating-overlay convenience ---------------------------

def test_decorative_composes_the_three_overlay_flags():
    canvas = danvas.Canvas()
    orb = canvas.custom(name="orb", html="<b>x</b>", decorative=True)
    # decorative == grabbable=False + operable=False + frame=False.
    assert (orb.grabbable, orb.operable, orb.frame) == (False, False, False)


def test_decorative_lets_an_explicit_flag_override_it():
    canvas = danvas.Canvas()
    # Pinning a flag explicitly wins over the decorative default for that flag,
    # leaving the other two composed.
    orb = canvas.custom(name="orb", html="<b>x</b>", decorative=True, frame=True)
    assert orb.frame is True
    assert (orb.grabbable, orb.operable) == (False, False)


def test_non_decorative_panels_keep_the_flag_defaults():
    canvas = danvas.Canvas()
    lbl = canvas.label("plain", value="hi")
    assert (lbl.grabbable, lbl.operable, lbl.frame) == (True, True, True)


def test_explicit_grabbable_false_without_decorative_still_applies():
    # The None-default refactor must not change the plain explicit path.
    canvas = danvas.Canvas()
    btn = canvas.button("b", grabbable=False)
    assert btn.grabbable is False
    assert (btn.operable, btn.frame) == (True, True)


# -- component namespace: canvas[name] is the canonical accessor -------------

def test_getitem_resolves_a_shadowing_name_that_attr_access_cannot():
    canvas = danvas.Canvas()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")            # the shadow warning is expected
        insp = canvas.inspector(name="inspector")  # name collides with the method
    # canvas.inspector returns the METHOD (shadowed); canvas["inspector"] the panel.
    assert callable(canvas.inspector)
    assert canvas["inspector"] is insp


def test_in_operator_reports_membership_by_name():
    canvas = danvas.Canvas()
    canvas.label("status", value="hi")
    assert "status" in canvas
    assert "nope" not in canvas


def test_getitem_unknown_name_lists_available():
    canvas = danvas.Canvas()
    canvas.label("status", value="hi")
    with pytest.raises(KeyError) as ei:
        canvas["ghost"]
    msg = str(ei.value)
    assert "ghost" in msg and "status" in msg     # names the miss + what's available


def test_below_and_right_of_derive_position():
    canvas = danvas.Canvas()
    a = canvas.label("a", x=100, y=200, w=300, h=80)
    b = canvas.label("b", below=a)
    assert (b.x, b.y) == (100, 200 + 80 + 16)
    c = canvas.label("c", right_of=a, gap=20)
    assert (c.x, c.y) == (100 + 300 + 20, 200)


def test_above_and_left_of_offset_by_own_size():
    canvas = danvas.Canvas()
    a = canvas.label("a", x=500, y=500, w=200, h=100)
    b = canvas.label("b", above=a, w=150, h=60, gap=10)
    assert (b.x, b.y) == (500, 500 - 10 - 60)
    c = canvas.label("c", left_of=a, w=150, gap=10)
    assert (c.x, c.y) == (500 - 10 - 150, 500)


def test_two_anchors_each_set_their_axis():
    canvas = danvas.Canvas()
    a = canvas.label("a", x=0, y=0, w=100, h=50)
    b = canvas.label("b", x=400, y=0, w=100, h=50)
    c = canvas.label("c", below=a, right_of=b, gap=10)
    assert (c.x, c.y) == (400 + 100 + 10, 0 + 50 + 10)


def test_anchor_by_name_and_explicit_coordinate_wins():
    canvas = danvas.Canvas()
    canvas.label("a", x=100, y=100, w=100, h=50)
    b = canvas.label("b", below="a", x=999)
    assert (b.x, b.y) == (999, 100 + 50 + 16)


def test_unplaced_anchor_defers():
    # An anchor without a position no longer raises; the relative placement is
    # deferred and applied once the anchor's position is reported by the browser.
    canvas = danvas.Canvas()
    a = canvas.label("a")               # auto-cascade: no Python-side position
    b = canvas.label("b", below=a)      # used to raise; now defers silently
    assert b.x is None and b.y is None  # no position yet — deferred
    # Simulate the browser reporting a's position (the on_layout path).
    a._apply_remote_layout({"x": 80, "y": 80, "w": 240, "h": 32})
    assert b.x == 80 and b.y == 80 + 32 + 16   # deferred placement applied


def test_unknown_anchor_still_raises():
    canvas = danvas.Canvas()
    with pytest.raises(ValueError, match="not a component"):
        canvas.label("c", below="ghost")


def test_matplotlib_figure_released_after_render():
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from danvas.components.image import _to_data_uri

    fig, ax = plt.subplots()
    ax.plot([0, 1], [1, 0])
    num = fig.number
    uri = _to_data_uri(fig)
    assert uri.startswith("data:image/png;base64,")
    # The figure must be gone from pyplot's registry (the leak), but the object
    # itself must still render.
    assert not plt.fignum_exists(num)
    assert _to_data_uri(fig).startswith("data:image/png;base64,")


def test_clear_removes_all_panels_and_arrows():
    canvas = danvas.Canvas()
    a = canvas.label("a", x=0, y=0)
    b = canvas.label("b", x=100, y=0)
    canvas.connect(a, b, name="ab")
    assert len(canvas.components) == 2
    assert len(canvas.arrows) == 1

    result = canvas.clear()

    assert result is canvas           # fluent return
    assert canvas.components == []
    assert canvas.arrows == []
    assert canvas._named == {}


def test_restore_layout_warns_on_missing_panel():
    canvas = danvas.Canvas()
    canvas.label("present", x=0, y=0)
    saved = canvas._layout()
    saved["components"].append({"name": "ghost", "id": "deadbeef",
                                "x": 50, "y": 50, "w": 200, "h": 100,
                                "rotation": 0})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        canvas._restore_layout(saved)

    assert any("ghost" in str(w.message) for w in caught)


def test_save_blocking_false_returns_future(tmp_path):
    canvas = danvas.Canvas()
    canvas.label("hello", value="world")
    path = tmp_path / "canvas.json"

    # No browser connected: formation-only save still completes.
    fut = canvas.save(str(path), blocking=False)
    result = fut.result(timeout=2.0)   # raises on timeout or exception

    assert result is canvas
    data = json.loads(path.read_text())
    assert "layout" in data
    assert "drawings" not in data      # no browser → no drawings captured
