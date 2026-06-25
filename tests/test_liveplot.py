"""LivePlot: dynamic traces and TensorBoard-style smoothing."""

import pytest

import danvas
from danvas.components.liveplot import _ema


def test_push_creates_traces_on_the_fly():
    plot = danvas.LivePlot("m")          # no traces declared up front
    plot.push({"a": 1})
    plot.push({"a": 2, "b": 9})            # 'b' is new — appears as its own trace
    payload = plot._payload()
    names = [t["name"] for t in payload["data"]]
    assert names == ["a", "b"]
    a = next(t for t in payload["data"] if t["name"] == "a")
    assert a["y"] == [1, 2]


def test_explicit_x_is_used_as_the_step_axis():
    plot = danvas.LivePlot("m", traces=["loss"])
    plot.push({"loss": 0.5}, x=10)
    plot.push({"loss": 0.4}, x=20)
    (trace,) = plot._payload()["data"]
    assert trace["x"] == [10, 20]


def test_smoothing_emits_raw_plus_smoothed_per_trace():
    plot = danvas.LivePlot("m", traces=["loss"], smoothing=0.5)
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
    plot = danvas.LivePlot("m", traces=["loss"])   # default smoothing=0
    plot.push({"loss": 1.0})
    (trace,) = plot._payload()["data"]
    assert "opacity" not in trace and "line" not in trace


def test_invalid_smoothing_rejected():
    with pytest.raises(ValueError):
        danvas.LivePlot("m", smoothing=1.0)
    with pytest.raises(ValueError):
        danvas.LivePlot("m", smoothing=-0.1)


def test_smoothing_settable_live():
    plot = danvas.LivePlot("m", traces=["loss"])
    plot.push({"loss": 1.0})
    plot.smoothing = 0.3
    assert len(plot._payload()["data"]) == 2   # now raw + smoothed
    with pytest.raises(ValueError):
        plot.smoothing = 2.0


def test_title_reserves_top_margin():
    # Title-less plots keep the tight default top margin...
    plain = danvas.LivePlot("m", traces=["a"])
    plain.push({"a": 1})
    assert plain._payload()["layout"]["margin"]["t"] == 15
    # ...but a user-supplied title gets head-room so it can't clip the plot.
    titled = danvas.LivePlot("m", traces=["a"], layout={"title": {"text": "L"}})
    titled.push({"a": 1})
    assert titled._payload()["layout"]["margin"]["t"] >= 40


def test_ema_debiases_cold_start():
    # With debiasing the very first smoothed point equals the first raw value
    # (no drag toward zero); a flat series stays flat.
    assert _ema([5.0, 5.0, 5.0], 0.9) == pytest.approx([5.0, 5.0, 5.0])
    assert _ema([], 0.5) == []


# -- streaming on the wire: push sends a delta, not the whole buffer -----------

class _CaptureBridge:
    """Records what each push broadcasts. LivePlot streams through the conflated
    path either way now: coalesce=True (fifo, append) or False (latest, replace)."""
    def __init__(self):
        self.sent = []  # (coalesce, payload)

    def broadcast(self, msg, exclude=None):
        self.sent.append((None, msg["payload"]))  # not used by LivePlot streaming

    def broadcast_conflated(self, comp_id, *, msg=None, data=None,
                            exclude=None, tap=True, coalesce=False):
        self.sent.append((coalesce, msg["payload"]))


def _bound(plot):
    bridge = _CaptureBridge()
    plot._bind("p1", bridge)
    return bridge


def test_push_streams_extend_delta_not_full_figure():
    plot = danvas.LivePlot("m", traces=["train", "val"])
    bridge = _bound(plot)
    plot.push({"train": 1.0, "val": 2.0})
    plot.push({"train": 1.5, "val": 2.5})
    # A steady-state push ships only the new point(s), keyed by trace index, via
    # the coalescing path (so it merges rather than queues under backpressure).
    coalesce, payload = bridge.sent[-1]
    assert coalesce is True and "plot_extend" in payload and "plot" not in payload
    ext = payload["plot_extend"]
    assert ext["indices"] == [0, 1]
    assert ext["x"] == [[2], [2]] and ext["y"] == [[1.5], [2.5]]
    assert ext["max"] == plot._max


def test_new_trace_falls_back_to_full_figure():
    plot = danvas.LivePlot("m", traces=["a"])
    bridge = _bound(plot)
    plot.push({"a": 1.0})                       # known trace -> delta
    assert "plot_extend" in bridge.sent[-1][1]
    plot.push({"b": 9.0})                        # new trace -> full snapshot
    assert "plot" in bridge.sent[-1][1] and "plot_extend" not in bridge.sent[-1][1]


def test_latest_queue_sends_full_snapshot_not_delta():
    # Under "latest" the bridge drops stale pending frames, so an append delta
    # would lose points; the policy needs whole-figure replace semantics.
    plot = danvas.LivePlot("m", traces=["a"])
    plot.queue = "latest"
    bridge = _bound(plot)
    plot.push({"a": 1.0})
    coalesce, payload = bridge.sent[-1]
    assert coalesce is False and "plot" in payload and "plot_extend" not in payload


def test_smoothing_delta_extends_raw_and_smoothed_traces():
    plot = danvas.LivePlot("m", traces=["loss"], smoothing=0.6)
    bridge = _bound(plot)
    plot.push({"loss": 10.0})
    plot.push({"loss": 20.0})
    ext = bridge.sent[-1][1]["plot_extend"]
    # One logical trace -> two Plotly traces: faint raw (idx 0) + smoothed (idx 1).
    assert ext["indices"] == [0, 1]
    assert ext["y"][0] == [20.0]                              # raw is the value
    assert ext["y"][1][0] == pytest.approx(_ema([10.0, 20.0], 0.6)[-1])  # EMA


# -- batch push: many points per trace in one call ----------------------------

def test_push_batch_with_explicit_x_appends_all_points():
    plot = danvas.LivePlot("m", traces=["a"])
    bridge = _bound(plot)
    plot.push({"a": [1.0, 2.0, 3.0]}, x=[10, 20, 30])
    (trace,) = plot._payload()["data"]
    assert trace["x"] == [10.0, 20.0, 30.0] and trace["y"] == [1.0, 2.0, 3.0]
    # one extend frame carrying all three points, not three frames
    ext = bridge.sent[-1][1]["plot_extend"]
    assert ext["x"] == [[10.0, 20.0, 30.0]] and ext["y"] == [[1.0, 2.0, 3.0]]


def test_push_batch_auto_indexes_each_point():
    plot = danvas.LivePlot("m", traces=["a"])
    _bound(plot)
    plot.push({"a": [5.0, 6.0]})            # no x -> auto 1, 2
    plot.push({"a": [7.0]})                 # continues from 3
    (trace,) = plot._payload()["data"]
    assert trace["x"] == [1, 2, 3] and trace["y"] == [5.0, 6.0, 7.0]


def test_push_batch_spans_multiple_traces():
    plot = danvas.LivePlot("m", traces=["a", "b"])
    _bound(plot)
    plot.push({"a": [1.0, 2.0], "b": [3.0, 4.0]}, x=[0, 1])
    data = {t["name"]: t["y"] for t in plot._payload()["data"]}
    assert data["a"] == [1.0, 2.0] and data["b"] == [3.0, 4.0]


def test_push_batch_length_mismatch_raises():
    plot = danvas.LivePlot("m", traces=["a"])
    _bound(plot)
    with pytest.raises(ValueError):
        plot.push({"a": [1.0, 2.0, 3.0]}, x=[10, 20])      # x too short
    with pytest.raises(ValueError):
        plot.push({"a": [1.0, 2.0]}, x=5)                  # scalar x, batch values


def test_push_batch_trims_to_rolling_max():
    plot = danvas.LivePlot("m", traces=["a"], max_points=3)
    bridge = _bound(plot)
    plot.push({"a": [1.0, 2.0, 3.0, 4.0, 5.0]}, x=[1, 2, 3, 4, 5])
    (trace,) = plot._payload()["data"]
    assert trace["y"] == [3.0, 4.0, 5.0]                   # buffer keeps last 3
    ext = bridge.sent[-1][1]["plot_extend"]
    assert ext["y"] == [[3.0, 4.0, 5.0]] and ext["x"] == [[3, 4, 5]]  # so does the delta


def test_push_single_point_unchanged_by_batch_support():
    plot = danvas.LivePlot("m", traces=["a"])
    bridge = _bound(plot)
    plot.push({"a": 9.0}, x=42)
    ext = bridge.sent[-1][1]["plot_extend"]
    assert ext["x"] == [[42]] and ext["y"] == [[9.0]]


def test_state_payload_still_replays_full_buffer():
    # Reconnecting clients get the whole series in one shot (deltas are only for
    # the live append path), so a late joiner sees the full curve.
    plot = danvas.LivePlot("m", traces=["a"])
    _bound(plot)
    for y in (1.0, 2.0, 3.0):
        plot.push({"a": y})
    data = plot.state_payload()["plot"]["data"]
    assert data[0]["y"] == [1.0, 2.0, 3.0]


# -- coalescing backpressure (the bridge merge that fixes the fifo backlog) -----

def test_coalesce_extend_concatenates_per_trace_index():
    from danvas.bridge import Bridge

    def frame(xi, ys):
        return {"type": "update", "id": "p",
                "payload": {"plot_extend": {"indices": [0, 1],
                                            "x": [[xi], [xi]],
                                            "y": [[ys[0]], [ys[1]]], "max": None}}}
    merged = Bridge._merge_live(None, frame(1, (10, 20)))
    merged = Bridge._merge_live(merged, frame(2, (11, 21)))
    ext = merged["payload"]["plot_extend"]
    assert ext["x"] == [[1, 2], [1, 2]] and ext["y"] == [[10, 11], [20, 21]]


def test_coalesce_appends_delta_onto_pending_snapshot():
    # A full snapshot waiting to be sent absorbs a following delta, so order is
    # preserved when a clear/new-trace frame and a push race.
    from danvas.bridge import Bridge
    snap = {"type": "update", "id": "p",
            "payload": {"plot": {"data": [{"x": [1], "y": [10], "name": "a"}],
                                 "layout": {}}}}
    delta = {"type": "update", "id": "p",
             "payload": {"plot_extend": {"indices": [0], "x": [[2, 3]],
                                         "y": [[11, 12]], "max": None}}}
    merged = Bridge._merge_live(Bridge._merge_live(None, snap), delta)
    assert merged["payload"]["plot"]["data"][0]["x"] == [1, 2, 3]


def test_coalesce_snapshot_supersedes_pending_delta():
    from danvas.bridge import Bridge
    delta = {"type": "update", "id": "p",
             "payload": {"plot_extend": {"indices": [0], "x": [[1]], "y": [[9]],
                                         "max": None}}}
    snap = {"type": "update", "id": "p",
            "payload": {"plot": {"data": [], "layout": {}}}}
    merged = Bridge._merge_live(Bridge._merge_live(None, delta), snap)
    assert "plot" in merged["payload"] and "plot_extend" not in merged["payload"]


def test_coalesce_trims_to_rolling_max():
    from danvas.bridge import Bridge
    merged = None
    for i in range(5):
        f = {"type": "update", "id": "p",
             "payload": {"plot_extend": {"indices": [0], "x": [[i]],
                                         "y": [[i]], "max": 3}}}
        merged = Bridge._merge_live(merged, f)
    assert merged["payload"]["plot_extend"]["x"] == [[2, 3, 4]]


def test_merge_live_does_not_alias_source_frame():
    # The stored pending frame is a private copy: mutating the source after it's
    # stored must not corrupt what will be sent.
    from danvas.bridge import Bridge
    src = {"type": "update", "id": "p",
           "payload": {"plot_extend": {"indices": [0], "x": [[9]], "y": [[9]],
                                       "max": None}}}
    merged = Bridge._merge_live(None, src)
    src["payload"]["plot_extend"]["x"][0].append(999)
    assert merged["payload"]["plot_extend"]["x"] == [[9]]


def test_push_uses_coalescing_for_fifo_and_replace_for_latest():
    # fifo (default) opts into coalescing; latest keeps drop-stale replace.
    seen = {}

    class Bridge2:
        def broadcast_conflated(self, cid, *, msg=None, data=None,
                                exclude=None, tap=True, coalesce=False):
            seen["coalesce"] = coalesce

    p = danvas.LivePlot("m", traces=["a"])
    p._bind("p1", Bridge2())
    p.push({"a": 1.0})
    assert seen["coalesce"] is True
    p.queue = "latest"
    p.push({"a": 2.0})
    assert seen["coalesce"] is False


# -- Plotly hover toolbar is enabled (zoom/pan/save-PNG for analysis) ----------
# No JS test harness, so these guard the embedded Plotly config against a silent
# re-disable, the way test_protocol_sync guards the wire contract.
def test_plot_modebar_enabled():
    from danvas.components import plot as plot_mod
    assert "displayModeBar: false" not in plot_mod._SOURCE   # toolbar no longer suppressed
    assert "displaylogo: false" in plot_mod._SOURCE           # Plotly link hidden


def test_liveplot_modebar_enabled_and_trimmed():
    from danvas.components import liveplot as lp_mod
    assert "displayModeBar: false" not in lp_mod._SOURCE
    assert "displaylogo: false" in lp_mod._SOURCE
    # lasso/box-select do nothing on a line stream — kept out of the bar.
    assert "modeBarButtonsToRemove" in lp_mod._SOURCE
