"""Per-role and per-client view scoping for ``canvas.set_view``.

``set_view`` can target everyone (global default), a login ``role`` (matching
``serve(passwords=)``), or one ``client_id``. On connect those layers merge
global < per-role < per-client, so a more specific scope wins.
"""

import pycanvas
from pycanvas.bridge import Bridge


def test_set_view_roles_records_per_role_state():
    canvas = pycanvas.Canvas()
    # No server loop yet, so the push no-ops and only the stored state changes.
    canvas.set_view(read_only=True, ui=False, roles=["user"])

    assert canvas._bridge._view_per_role == {
        "user": {"read_only": True, "ui": False}
    }
    assert canvas._bridge._view is None              # global untouched
    assert canvas._bridge._view_per_client == {}     # per-client untouched


def test_set_view_roles_accepts_a_bare_string():
    canvas = pycanvas.Canvas()
    canvas.set_view(locked=True, roles="kiosk")

    assert canvas._bridge._view_per_role == {"kiosk": {"locked": True}}


def test_set_view_roles_merges_keys_across_calls():
    canvas = pycanvas.Canvas()
    canvas.set_view(ui=False, roles=["user"])
    canvas.set_view(read_only=True, roles=["user"])

    assert canvas._bridge._view_per_role["user"] == {
        "ui": False, "read_only": True
    }


def test_view_for_layers_global_then_role_then_client():
    bridge = Bridge()
    bridge._view = {"grid": True, "ui": True}
    bridge._view_per_role = {"user": {"ui": False}}
    bridge._view_per_client = {"v1": {"read_only": True}}

    # A user viewer: global grid kept, role flips ui off, client adds read_only.
    assert bridge._view_for("v1", "user") == {
        "grid": True, "ui": False, "read_only": True
    }
    # An admin viewer (no role/client layer): just the global view.
    assert bridge._view_for("v2", "admin") == {"grid": True, "ui": True}


def test_view_for_returns_none_when_no_layer_applies():
    bridge = Bridge()
    assert bridge._view_for("nobody", None) is None


def test_send_to_role_no_loop_is_a_noop():
    bridge = Bridge()  # _loop is None until serving
    bridge._viewers["WS"] = {"id": "v1", "name": "Fox", "role": "user"}
    # Must not raise when nothing is serving yet.
    bridge.send_to_role("user", {"type": "view", "view": {"ui": False}})
