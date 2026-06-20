"""Bridge core: per-viewer register replay (prop + layout overlays), the
targeted-send selection (broadcast / send_to_role / send_to_client), and
viewer-identity reuse on reconnect (_make_viewer).

These exercise the machinery behind the `shared < role < client` model and the
reconnect-stable identity, without needing a running event loop.
"""

import json

import pytest
import pycanvas
from pycanvas.bridge import Bridge


def _panel(bridge, **props):
    p = pycanvas.React("function Component(){return null}", name="p", props=props)
    p._bind("p1", bridge)
    return p


def _reg_data(bridge, panel, role=None, client_id=None):
    msg = bridge.register_message(panel, role=role, client_id=client_id)
    return json.loads(msg["props"]["data"])


# -- register_message: per-viewer PROP overlays (shared < role < client) -------

def test_register_merges_prop_overlays_by_precedence():
    b = Bridge()
    p = _panel(b, k="shared")
    p.update(roles="admin", k="role")
    p.update(client_id="c1", k="client")
    assert _reg_data(b, p)["k"] == "shared"                          # base
    assert _reg_data(b, p, role="admin")["k"] == "role"              # role > base
    assert _reg_data(b, p, role="admin", client_id="c1")["k"] == "client"  # client wins
    assert _reg_data(b, p, role="other")["k"] == "shared"            # isolated


def test_register_prop_overlay_does_not_leak_into_base():
    b = Bridge()
    p = _panel(b, k="shared")
    p.update(roles="admin", only_admin=1)
    assert "only_admin" not in _reg_data(b, p)                       # base clean
    assert "only_admin" not in _reg_data(b, p, role="other")


# -- register_message: per-viewer LAYOUT overlays ------------------------------

def test_register_merges_layout_overlay_by_role():
    b = Bridge()
    p = _panel(b)
    p.set_layout(x=10, y=20, w=100)               # shared base
    p.set_layout(roles="admin", x=999)            # admin overlay (x only)
    base = b.register_message(p)
    adm = b.register_message(p, role="admin")
    assert (base["x"], base["y"]) == (10, 20)
    assert adm["x"] == 999 and adm["y"] == 20      # overlay x, base y merged
    assert adm["props"]["w"] == 100                # size inherited from base
    assert b.register_message(p, role="other")["x"] == 10  # others see base


# -- opacity -------------------------------------------------------------------

def test_opacity_absent_at_default():
    b = Bridge()
    p = _panel(b)
    msg = b.register_message(p)
    assert "opacity" not in msg          # default 1.0 is omitted from the wire


def test_opacity_present_when_non_default():
    b = Bridge()
    p = _panel(b)
    p.set_layout(opacity=0.4)
    msg = b.register_message(p)
    assert msg["opacity"] == pytest.approx(0.4)


def test_opacity_setter_roundtrip():
    b = Bridge()
    p = _panel(b)
    p.opacity = 0.5
    assert p.opacity == pytest.approx(0.5)
    msg = b.register_message(p)
    assert msg["opacity"] == pytest.approx(0.5)


def test_opacity_overlay_by_role():
    b = Bridge()
    p = _panel(b)
    p.set_layout(opacity=0.8)                  # shared
    p.set_layout(roles="vip", opacity=0.2)     # role override
    base = b.register_message(p)
    vip  = b.register_message(p, role="vip")
    assert base["opacity"] == pytest.approx(0.8)
    assert vip["opacity"]  == pytest.approx(0.2)


def test_opacity_reset_to_default_omits_from_wire():
    b = Bridge()
    p = _panel(b)
    p.opacity = 0.3
    p.opacity = 1.0                            # back to default
    assert "opacity" not in b.register_message(p)


# -- targeted-send selection (the _emit refactor) ------------------------------

class _WS:
    """An opaque connection handle (the bridge keys maps by identity)."""
    def __init__(self, name):
        self.name = name


def _selecting_bridge():
    b = Bridge()
    b._loop = object()            # bypass the "not serving" guard in broadcast
    calls = []
    b._emit = lambda targets, msg: calls.append((list(targets), msg))
    a, c = _WS("a"), _WS("c")
    b._connections = {a, c}
    b._viewers = {a: {"id": "ia", "role": "admin"},
                  c: {"id": "ic", "role": "viewer"}}
    return b, calls, a, c


def test_broadcast_targets_all_and_honors_exclude():
    b, calls, a, c = _selecting_bridge()
    b.broadcast({"t": 1})
    assert set(calls[-1][0]) == {a, c}
    b.broadcast({"t": 2}, exclude=a)
    assert calls[-1][0] == [c]


def test_send_to_role_and_client_pick_one():
    b, calls, a, c = _selecting_bridge()
    b.send_to_role("admin", {"t": 3})
    assert calls[-1][0] == [a]
    b.send_to_client("ic", {"t": 4})
    assert calls[-1][0] == [c]
    b.send_to_client("missing", {"t": 5})
    assert calls[-1][0] == []                       # no match -> nothing scheduled


def test_emit_is_noop_before_serving():
    b = Bridge()
    assert b._loop is None
    b._emit([_WS("x")], {"t": 1})                   # must not raise


# -- viewer identity reuse on reconnect (_make_viewer) -------------------------

def test_make_viewer_reuses_valid_requested_identity():
    b = Bridge()
    v = b._make_viewer(role="admin", device="mobile",
                       requested={"id": "a1b2c3d4", "name": "Fox", "color": "#ef4444"})
    assert v == {"id": "a1b2c3d4", "name": "Fox", "color": "#ef4444",
                 "cursor": None, "device": "mobile", "role": "admin"}


def test_make_viewer_rejects_bad_id_and_color():
    b = Bridge()
    bad_id = b._make_viewer(requested={"id": "../etc", "name": "x", "color": "#fff"})
    assert bad_id["id"] != "../etc" and bad_id["role"] is None       # minted fresh
    bad_color = b._make_viewer(requested={"id": "abc123", "name": "W", "color": "red"})
    assert bad_color["id"] == "abc123" and bad_color["color"].startswith("#")


def test_make_viewer_role_is_server_set_not_client():
    b = Bridge()
    # A requested identity can carry id/name/color but never its own role.
    v = b._make_viewer(role="viewer",
                       requested={"id": "abcd1234", "name": "x",
                                  "color": "#000000", "role": "admin"})
    assert v["role"] == "viewer"


def test_make_viewer_without_request_is_fresh_and_unique():
    b = Bridge()
    a = b._make_viewer()
    assert a["id"] and a["name"] and a["role"] is None
