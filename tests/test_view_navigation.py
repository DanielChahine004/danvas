"""Navigation mode via ``canvas.set_view(navigation=...)``.

Navigation mode is a view key, so it obeys the same global < per-role <
per-client precedence and is replayed to each viewer via the view dict in
``welcome``.  These tests exercise validation, storage, and the view-layer
merge — no running server needed.
"""

import pytest

import danvas
from danvas.bridge import Bridge
from danvas.canvas import _coerce_navigation


# -- _coerce_navigation unit tests --------------------------------------------

def test_coerce_string_free():
    assert _coerce_navigation("free") == {"mode": "free", "zoom": 1.0}


def test_coerce_string_scroll_y():
    assert _coerce_navigation("scroll_y") == {"mode": "scroll_y", "zoom": 1.0}


def test_coerce_string_scroll_x():
    assert _coerce_navigation("scroll_x") == {"mode": "scroll_x", "zoom": 1.0}


def test_coerce_tuple_custom_zoom():
    assert _coerce_navigation(("scroll_y", 0.75)) == {"mode": "scroll_y", "zoom": 0.75}


def test_coerce_list_form():
    assert _coerce_navigation(["scroll_x", 1.5]) == {"mode": "scroll_x", "zoom": 1.5}


def test_coerce_invalid_mode_raises():
    with pytest.raises(ValueError, match="navigation"):
        _coerce_navigation("diagonal")


def test_coerce_tuple_invalid_mode_raises():
    with pytest.raises(ValueError, match="navigation"):
        _coerce_navigation(("horizontal", 1.0))


def test_coerce_bad_type_raises():
    with pytest.raises(TypeError, match="navigation"):
        _coerce_navigation(42)


# -- set_view(navigation=) stores correct state -------------------------------

def test_set_view_navigation_string_global():
    canvas = danvas.Canvas()
    canvas.set_view(navigation="scroll_y")
    assert canvas._bridge._view == {"navigation": {"mode": "scroll_y", "zoom": 1.0}}


def test_set_view_navigation_tuple_global():
    canvas = danvas.Canvas()
    canvas.set_view(navigation=("scroll_x", 0.75))
    assert canvas._bridge._view == {"navigation": {"mode": "scroll_x", "zoom": 0.75}}


def test_set_view_navigation_per_role():
    canvas = danvas.Canvas()
    canvas.set_view(navigation="scroll_y", roles="kiosk")
    assert canvas._bridge._view_per_role == {
        "kiosk": {"navigation": {"mode": "scroll_y", "zoom": 1.0}}
    }
    assert canvas._bridge._view is None     # global untouched


def test_set_view_navigation_per_client():
    canvas = danvas.Canvas()
    canvas.set_view(navigation="scroll_x", client_id="abc123")
    assert canvas._bridge._view_per_client == {
        "abc123": {"navigation": {"mode": "scroll_x", "zoom": 1.0}}
    }
    assert canvas._bridge._view is None


def test_set_view_navigation_merges_with_other_keys():
    canvas = danvas.Canvas()
    canvas.set_view(ui=False)
    canvas.set_view(navigation="scroll_y")
    assert canvas._bridge._view == {
        "ui": False,
        "navigation": {"mode": "scroll_y", "zoom": 1.0},
    }


def test_set_view_navigation_reset_to_free():
    canvas = danvas.Canvas()
    canvas.set_view(navigation="scroll_y")
    canvas.set_view(navigation="free")
    assert canvas._bridge._view == {"navigation": {"mode": "free", "zoom": 1.0}}


def test_set_view_invalid_navigation_raises():
    canvas = danvas.Canvas()
    with pytest.raises(ValueError, match="navigation"):
        canvas.set_view(navigation="sideways")


# -- _view_for merges navigation through the overlay layers -------------------

def test_view_for_global_navigation():
    bridge = Bridge()
    bridge._view = {"navigation": {"mode": "scroll_y", "zoom": 1.0}}
    result = bridge._view_for("v1", None)
    assert result == {"navigation": {"mode": "scroll_y", "zoom": 1.0}}


def test_view_for_role_navigation_overrides_global():
    bridge = Bridge()
    bridge._view = {"navigation": {"mode": "scroll_y", "zoom": 1.0}, "ui": True}
    bridge._view_per_role = {"kiosk": {"navigation": {"mode": "scroll_x", "zoom": 0.5}}}
    result = bridge._view_for("v1", "kiosk")
    assert result["navigation"] == {"mode": "scroll_x", "zoom": 0.5}
    assert result["ui"] is True  # non-navigation key from global


def test_view_for_client_navigation_wins():
    bridge = Bridge()
    bridge._view = {"navigation": {"mode": "scroll_y", "zoom": 1.0}}
    bridge._view_per_client = {"v1": {"navigation": {"mode": "free", "zoom": 1.0}}}
    result = bridge._view_for("v1", None)
    assert result["navigation"] == {"mode": "free", "zoom": 1.0}


def test_view_for_other_role_sees_global_only():
    bridge = Bridge()
    bridge._view = {"ui": True}
    bridge._view_per_role = {"kiosk": {"navigation": {"mode": "scroll_y", "zoom": 1.0}}}
    result = bridge._view_for("v2", "admin")
    assert "navigation" not in result
    assert result == {"ui": True}
