"""Histogram: a distribution that evolves over recorded steps."""

import numpy as np
import pytest

import pycanvas


def test_add_records_steps_and_fixes_bins():
    h = pycanvas.Histogram("weights", bins=10, value_range=(-1, 1))
    h.add(np.zeros(100), step=0)
    h.add(np.ones(100), step=1)
    assert [s for s, _ in h._records] == [0, 1]
    # Edges are fixed from value_range, so every step shares the same 10 bins.
    assert len(h._edges) == 11
    assert h._edges[0] == -1 and h._edges[-1] == 1


def test_step_defaults_to_record_index():
    h = pycanvas.Histogram("w", bins=5)
    h.add([0, 1, 2, 3])
    h.add([0, 1, 2, 3])
    assert [s for s, _ in h._records] == [0, 1]


def test_max_steps_bounds_the_buffer():
    h = pycanvas.Histogram("w", bins=4, value_range=(0, 1), max_steps=3)
    for step in range(6):
        h.add(np.random.random(20), step=step)
    assert len(h._records) == 3
    assert [s for s, _ in h._records] == [3, 4, 5]


def test_figure_builds_for_both_modes():
    plotly = pytest.importorskip("plotly")
    for mode in ("heatmap", "overlay"):
        h = pycanvas.Histogram("w", bins=6, value_range=(0, 1), mode=mode)
        h.add(np.random.random(50), step=0)
        h.add(np.random.random(50), step=1)
        fig = h._figure()
        assert isinstance(fig, plotly.graph_objects.Figure)
        assert len(fig.data) >= 1


def test_bad_mode_rejected():
    with pytest.raises(ValueError):
        pycanvas.Histogram("w", mode="violin")


def test_factory_places_and_registers():
    canvas = pycanvas.Canvas()
    h = canvas.histogram("grads", bins=8, x=100, y=120, w=400, h=300)
    assert canvas["grads"] is h
    assert (h.x, h.y, h.w, h.h) == (100, 120, 400, 300)
    assert h.component == "Custom"   # reuses the Custom (pcHtml) shape
