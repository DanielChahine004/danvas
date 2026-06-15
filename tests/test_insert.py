"""Insert-time conveniences: queue= forwarding and relative placement."""

import json
import warnings

import pytest

import pycanvas


def test_factories_accept_queue_kwarg():
    canvas = pycanvas.Canvas()
    lbl = canvas.label("status", queue="latest")
    assert lbl.queue == "latest"
    # None (the default) keeps the component's own policy.
    feed = pycanvas.VideoFeed("cam")           # VideoFeed defaults to "latest"
    canvas.insert(feed)
    assert feed.queue == "latest"


def test_insert_rejects_bad_queue():
    canvas = pycanvas.Canvas()
    with pytest.raises(ValueError):
        canvas.label("status", queue="newest")


def test_below_and_right_of_derive_position():
    canvas = pycanvas.Canvas()
    a = canvas.label("a", x=100, y=200, w=300, h=80)
    b = canvas.label("b", below=a)
    assert (b.x, b.y) == (100, 200 + 80 + 16)
    c = canvas.label("c", right_of=a, gap=20)
    assert (c.x, c.y) == (100 + 300 + 20, 200)


def test_above_and_left_of_offset_by_own_size():
    canvas = pycanvas.Canvas()
    a = canvas.label("a", x=500, y=500, w=200, h=100)
    b = canvas.label("b", above=a, w=150, h=60, gap=10)
    assert (b.x, b.y) == (500, 500 - 10 - 60)
    c = canvas.label("c", left_of=a, w=150, gap=10)
    assert (c.x, c.y) == (500 - 10 - 150, 500)


def test_two_anchors_each_set_their_axis():
    canvas = pycanvas.Canvas()
    a = canvas.label("a", x=0, y=0, w=100, h=50)
    b = canvas.label("b", x=400, y=0, w=100, h=50)
    c = canvas.label("c", below=a, right_of=b, gap=10)
    assert (c.x, c.y) == (400 + 100 + 10, 0 + 50 + 10)


def test_anchor_by_name_and_explicit_coordinate_wins():
    canvas = pycanvas.Canvas()
    canvas.label("a", x=100, y=100, w=100, h=50)
    b = canvas.label("b", below="a", x=999)
    assert (b.x, b.y) == (999, 100 + 50 + 16)


def test_unplaced_anchor_raises():
    canvas = pycanvas.Canvas()
    a = canvas.label("a")               # auto-cascade: no Python-side position
    with pytest.raises(ValueError, match="no position"):
        canvas.label("b", below=a)
    with pytest.raises(ValueError, match="not a component"):
        canvas.label("c", below="ghost")


def test_matplotlib_figure_released_after_render():
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from pycanvas.components.image import _to_data_uri

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
    canvas = pycanvas.Canvas()
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
    canvas = pycanvas.Canvas()
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
    canvas = pycanvas.Canvas()
    canvas.label("hello", value="world")
    path = tmp_path / "canvas.json"

    # No browser connected: formation-only save still completes.
    fut = canvas.save(str(path), blocking=False)
    result = fut.result(timeout=2.0)   # raises on timeout or exception

    assert result is canvas
    data = json.loads(path.read_text())
    assert "layout" in data
    assert "drawings" not in data      # no browser → no drawings captured
