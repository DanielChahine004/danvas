"""API-consistency freeze.

Pins the conventions CONTRIBUTING.md declares frozen: every factory takes typed
placement kwargs (live_plot/histogram used to swallow bare ``**kw``), and the
name-shadow warning fires for explicit collisions with Canvas attributes but
never for a component's own default name (``canvas.slider()`` inevitably
"shadows" its factory).
"""

import warnings

import pytest

import danvas


# -- live_plot / histogram: explicit signatures like every other factory --------

def test_live_plot_takes_constructor_and_placement_kwargs():
    canvas = danvas.Canvas()
    lp = canvas.live_plot("loss", traces=["train", "val"], smoothing=0.5,
                          max_points=100, x=10, y=20)
    assert lp._smoothing == 0.5
    assert lp._max == 100
    assert (lp.x, lp.y) == (10, 20)


def test_live_plot_rejects_bad_smoothing():
    canvas = danvas.Canvas()
    with pytest.raises(ValueError):
        canvas.live_plot("loss", smoothing=1.5)


def test_histogram_takes_constructor_and_placement_kwargs():
    canvas = danvas.Canvas()
    hg = canvas.histogram("weights", bins=12, max_steps=50, x=5, y=6)
    assert hg._bins == 12
    assert (hg.x, hg.y) == (5, 6)


def test_histogram_rejects_bad_mode():
    canvas = danvas.Canvas()
    with pytest.raises(ValueError):
        canvas.histogram("w", mode="sideways")


# -- name-shadow warning: explicit collisions only ------------------------------

def test_default_component_name_does_not_warn():
    canvas = danvas.Canvas()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        canvas.slider()                      # name defaults to "slider"
        canvas.button()                      # name defaults to "button"
    assert not any("shadows" in str(w.message) for w in caught)


def test_explicit_name_shadowing_a_canvas_attribute_warns():
    canvas = danvas.Canvas()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        canvas.slider("save")                # canvas.save is a real method
    assert any("shadows" in str(w.message) for w in caught)


def test_shadowed_panel_still_reachable_by_subscription():
    canvas = danvas.Canvas()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = canvas.slider("save")
    assert canvas["save"] is p
    assert callable(canvas.save)             # the method stays the method
