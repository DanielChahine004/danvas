"""LivePlot: dynamic traces and TensorBoard-style smoothing."""

import pytest

import pycanvas
from pycanvas.components.liveplot import _ema


def test_push_creates_traces_on_the_fly():
    plot = pycanvas.LivePlot("m")          # no traces declared up front
    plot.push({"a": 1})
    plot.push({"a": 2, "b": 9})            # 'b' is new — appears as its own trace
    payload = plot._payload()
    names = [t["name"] for t in payload["data"]]
    assert names == ["a", "b"]
    a = next(t for t in payload["data"] if t["name"] == "a")
    assert a["y"] == [1, 2]


def test_explicit_x_is_used_as_the_step_axis():
    plot = pycanvas.LivePlot("m", traces=["loss"])
    plot.push({"loss": 0.5}, x=10)
    plot.push({"loss": 0.4}, x=20)
    (trace,) = plot._payload()["data"]
    assert trace["x"] == [10, 20]


def test_smoothing_emits_raw_plus_smoothed_per_trace():
    plot = pycanvas.LivePlot("m", traces=["loss"], smoothing=0.5)
    for y in (1.0, 0.0, 1.0, 0.0):
        plot.push({"loss": y})
    data = plot._payload()["data"]
    # One faint raw trace (hidden from legend) + one bold smoothed trace.
    assert len(data) == 2
    raw, smoothed = data
    assert raw["showlegend"] is False and raw["opacity"] < 1
    assert smoothed.get("showlegend", True) is True
    assert raw["line"]["color"] == smoothed["line"]["color"]   # shared hue
    # Smoothing pulls the oscillation toward the mean (0.5), so the last
    # smoothed point sits strictly between the final raw value (0.0) and 0.5.
    assert 0.0 < smoothed["y"][-1] < 0.5


def test_smoothing_off_is_a_single_plain_trace():
    plot = pycanvas.LivePlot("m", traces=["loss"])   # default smoothing=0
    plot.push({"loss": 1.0})
    (trace,) = plot._payload()["data"]
    assert "opacity" not in trace and "line" not in trace


def test_invalid_smoothing_rejected():
    with pytest.raises(ValueError):
        pycanvas.LivePlot("m", smoothing=1.0)
    with pytest.raises(ValueError):
        pycanvas.LivePlot("m", smoothing=-0.1)


def test_smoothing_settable_live():
    plot = pycanvas.LivePlot("m", traces=["loss"])
    plot.push({"loss": 1.0})
    plot.smoothing = 0.3
    assert len(plot._payload()["data"]) == 2   # now raw + smoothed
    with pytest.raises(ValueError):
        plot.smoothing = 2.0


def test_title_reserves_top_margin():
    # Title-less plots keep the tight default top margin...
    plain = pycanvas.LivePlot("m", traces=["a"])
    plain.push({"a": 1})
    assert plain._payload()["layout"]["margin"]["t"] == 15
    # ...but a user-supplied title gets head-room so it can't clip the plot.
    titled = pycanvas.LivePlot("m", traces=["a"], layout={"title": {"text": "L"}})
    titled.push({"a": 1})
    assert titled._payload()["layout"]["margin"]["t"] >= 40


def test_ema_debiases_cold_start():
    # With debiasing the very first smoothed point equals the first raw value
    # (no drag toward zero); a flat series stays flat.
    assert _ema([5.0, 5.0, 5.0], 0.9) == pytest.approx([5.0, 5.0, 5.0])
    assert _ema([], 0.5) == []
