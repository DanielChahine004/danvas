"""API-manners regressions from field use (the 0.5.x practitioner review):
unnamed-panel collisions must be loud, and `cmap=` accepts names everywhere.
"""

import warnings

import numpy as np
import pytest

import danvas
from danvas.components.model3d import Model3D, _cmap_rgba


def test_unnamed_collision_warns():
    c = danvas.Canvas()
    c.markdown(text="one")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        c.markdown(text="two")   # both default to name="markdown"
    assert any("unnamed Markdown" in str(x.message) for x in w), \
        [str(x.message) for x in w]


def test_named_swap_in_place_stays_silent():
    c = danvas.Canvas()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        c.markdown("a", name="sec")
        c.markdown("b", name="sec")   # intended swap-in-place
        c.slider("s")
        c.slider("s")
    assert not w, [str(x.message) for x in w]


def test_cmap_accepts_names_everywhere():
    assert _cmap_rgba(0.0, "gray") == (0, 0, 0, 255)
    assert _cmap_rgba(1.0, "hot") == (255, 255, 255, 255)
    assert _cmap_rgba(0.5, "viridis") == _cmap_rgba(0.5)   # the default
    m3 = Model3D()
    m3.push_binary = lambda d: None
    m3.layer("c").points(np.zeros((4, 3)), color_by=[1, 2, 3, 4],
                         cmap="viridis")
    m3.layer("v").volume(np.ones((2, 2, 2)), cmap="gray")


def test_cmap_error_says_what_is_accepted():
    with pytest.raises(ValueError, match="gray.*callable"):
        _cmap_rgba(0.5, "v")
    with pytest.raises(ValueError, match="name|stops|callable"):
        _cmap_rgba(0.5, [object()])
