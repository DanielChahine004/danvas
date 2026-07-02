"""Role enforcement on the live wire, both directions.

Registration replay has always been role-filtered; these tests pin the rest:

- **egress** — a role-restricted panel's live ``update()``/binary frames go only
  to sockets whose viewer role may see the panel (previously they were broadcast
  to every authenticated socket and merely dropped client-side);
- **replies** — a ``canvas.request`` response goes only to the requesting socket
  (previously broadcast, leaking one viewer's reply to all);
- **ingress** — forged ``input``/``request``/``layout``/binary/``draw``/
  ``graveyard``/``restore`` frames are authorized server-side against the same
  rules the browser UI enforces (role visibility, ``locked``, ``operable``,
  ``lock_for``, per-viewer overlays, ``read_only`` views).
"""

import json
import time

import danvas
from danvas.bridge import BINARY_INPUT, Bridge, encode_binary_frame


class _WS:
    """An opaque connection handle (the bridge keys maps by identity)."""
    def __init__(self, name="ws"):
        self.name = name


class _Inline:
    """A dispatch stand-in that runs submissions synchronously and counts them."""
    def __init__(self):
        self.count = 0

    def submit(self, fn):
        self.count += 1
        fn()


def _bridge():
    """A bridge with two connected viewers (roles admin/viewer), a captured
    ``_emit``, and an inline dispatch so handlers run synchronously."""
    b = Bridge()
    b._loop = object()            # bypass the "not serving" guard in broadcast
    calls = []
    b._emit = lambda targets, msg: calls.append((list(targets), msg))
    b._dispatch = _Inline()
    admin, viewer = _WS("admin"), _WS("viewer")
    b._connections = {admin, viewer}
    b._viewers = {admin: {"id": "ia", "role": "admin"},
                  viewer: {"id": "iv", "role": "viewer"}}
    return b, calls, admin, viewer


def _label(b, roles=None, **flags):
    lbl = danvas.Label("secret", value="s")
    if roles:
        lbl._roles = list(roles)
    for k, v in flags.items():
        setattr(lbl, "_" + k if not k.startswith("_") else k, v)
    lbl._bind("c1", b)
    b.add_component(lbl)
    return lbl


def _react(b, roles=None):
    p = danvas.React("function Component(){return null}", name="p")
    if roles:
        p._roles = list(roles)
    p._bind("r1", b)
    b.add_component(p)
    return p


# -- egress: updates filtered by the panel's roles ----------------------------

def test_update_on_role_restricted_panel_reaches_only_that_role():
    b, calls, admin, viewer = _bridge()
    lbl = _label(b, roles=["admin"])
    lbl.update("classified")
    assert calls[-1][0] == [admin]           # the viewer socket never sees it


def test_update_on_unrestricted_panel_reaches_everyone():
    b, calls, admin, viewer = _bridge()
    lbl = _label(b)
    lbl.update("public")
    assert set(calls[-1][0]) == {admin, viewer}


def test_input_state_echo_is_role_filtered():
    b, calls, admin, viewer = _bridge()
    sld = danvas.Slider("speed")
    sld._roles = ["admin"]
    sld._bind("s1", b)
    b.add_component(sld)
    # A second admin socket should get the echo; the viewer-role socket must not.
    admin2 = _WS("admin2")
    b._connections.add(admin2)
    b._viewers[admin2] = {"id": "ia2", "role": "admin"}
    b._on_message(admin, json.dumps(
        {"type": "input", "id": "s1", "payload": {"value": 5}}))
    echo_targets = calls[-1][0]
    assert admin2 in echo_targets and viewer not in echo_targets
    assert admin not in echo_targets         # the sender is excluded as before


def test_binary_push_passes_panel_roles_to_bridge():
    b, calls, admin, viewer = _bridge()
    p = _react(b, roles=["admin"])
    seen = {}
    b.broadcast_binary = lambda data, exclude=None, roles=None: \
        seen.update(roles=roles)
    p.push_binary(b"\x01\x02")
    assert seen["roles"] == ["admin"]


def test_role_targets_selection():
    b, calls, admin, viewer = _bridge()
    assert set(b._role_targets(None)) == {admin, viewer}
    assert b._role_targets(["admin"]) == [admin]
    assert b._role_targets(["admin"], exclude=admin) == []
    assert b._role_targets(["nobody"]) == []


# -- replies: canvas.request responses are private to the requester ------------

def test_request_reply_goes_only_to_the_requesting_socket():
    b, calls, admin, viewer = _bridge()
    p = _react(b)

    @p.on_request()
    def _(req):
        return {"ok": True}

    b._on_message(admin, json.dumps(
        {"type": "request", "id": "r1", "reqId": "q1", "data": {}}))
    targets, msg = calls[-1]
    assert targets == [admin]
    assert msg == {"type": "response", "reqId": "q1", "result": {"ok": True}}


def test_unauthorized_request_gets_error_reply_not_dispatch():
    b, calls, admin, viewer = _bridge()
    p = _react(b, roles=["admin"])

    @p.on_request()
    def _(req):                              # must never run for the viewer
        raise AssertionError("handler ran for an unauthorized viewer")

    b._on_message(viewer, json.dumps(
        {"type": "request", "id": "r1", "reqId": "q2", "data": {}}))
    targets, msg = calls[-1]
    assert targets == [viewer] and "error" in msg and msg["reqId"] == "q2"


# -- ingress: input / binary authorization -------------------------------------

def _control(b, roles=None):
    """A Slider — a plain input control whose handlers ride ``_callbacks``."""
    sld = danvas.Slider("knob")
    if roles:
        sld._roles = list(roles)
    sld._bind("k1", b)
    b.add_component(sld)
    return sld


def _fires(b, comp, ws, payload=None):
    """Send an input frame for ``comp`` from ``ws``; report whether a handler ran."""
    ran = []
    comp._callbacks = [lambda v: ran.append(v)]
    b._on_message(ws, json.dumps(
        {"type": "input", "id": comp.id, "payload": payload or {"value": 1}}))
    return bool(ran)


def test_input_requires_role_visibility():
    b, calls, admin, viewer = _bridge()
    sld = _control(b, roles=["admin"])
    assert not _fires(b, sld, viewer)
    assert _fires(b, sld, admin)


def test_input_blocked_when_locked_or_inoperable():
    b, calls, admin, viewer = _bridge()
    sld = _control(b)
    sld._locked = True
    assert not _fires(b, sld, admin)
    sld._locked = False
    sld._operable = False
    assert not _fires(b, sld, admin)
    sld._operable = True
    assert _fires(b, sld, admin)


def test_input_blocked_for_lock_for_roles_only():
    b, calls, admin, viewer = _bridge()
    sld = _control(b)
    sld._lock_for = ["viewer"]
    assert not _fires(b, sld, viewer)
    assert _fires(b, sld, admin)


def test_input_honours_per_role_overlay_lock():
    b, calls, admin, viewer = _bridge()
    sld = _control(b)
    sld.set_layout(roles="viewer", locked=True)   # locked for that role only
    assert not _fires(b, sld, viewer)
    assert _fires(b, sld, admin)


def test_binary_input_requires_authorization():
    b, calls, admin, viewer = _bridge()
    p = _react(b, roles=["admin"])
    got = []
    p.on_binary(lambda data: got.append(data))
    frame = encode_binary_frame(BINARY_INPUT, p.id, b"payload")
    b._on_binary_input(viewer, frame)
    assert got == []
    b._on_binary_input(admin, frame)
    assert got == [b"payload"]


# -- ingress: layout / draw / graveyard / restore -------------------------------

def test_layout_requires_role_visibility():
    b, calls, admin, viewer = _bridge()
    lbl = _label(b, roles=["admin"])
    b._on_message(viewer, json.dumps(
        {"type": "layout", "id": lbl.id, "x": 5, "y": 6}))
    assert lbl.x is None                     # dropped: geometry untouched
    b._on_message(admin, json.dumps(
        {"type": "layout", "id": lbl.id, "x": 5, "y": 6}))
    assert (lbl.x, lbl.y) == (5, 6)


def test_layout_still_flows_for_locked_but_visible_panels():
    # Machine-generated layout reports (auto-flow, content-fit) ride the same
    # message type, so lock flags are deliberately NOT enforced on layout.
    b, calls, admin, viewer = _bridge()
    lbl = _label(b)
    lbl._draggable = False
    b._on_message(viewer, json.dumps(
        {"type": "layout", "id": lbl.id, "x": 7, "y": 8}))
    assert (lbl.x, lbl.y) == (7, 8)


def test_draw_dropped_for_read_only_view():
    b, calls, admin, viewer = _bridge()
    b._view_per_role = {"viewer": {"read_only": True}}
    diff = {"added": {"d1": {"type": "draw"}}, "updated": {}, "removed": {}}
    b._on_message(viewer, json.dumps({"type": "draw", "diff": diff}))
    assert b._drawings == {}                 # forged ink from a read-only viewer
    b._on_message(admin, json.dumps({"type": "draw", "diff": diff}))
    assert "d1" in b._drawings


def test_graveyard_and_restore_require_role_visibility():
    b, calls, admin, viewer = _bridge()
    b.register_live = lambda comp, only_roles=None: None  # fake loop can't fan out
    lbl = _label(b, roles=["admin"])
    b._on_message(viewer, json.dumps({"type": "graveyard", "id": lbl.id}))
    assert lbl.id not in b._graveyarded      # viewer can't delete what it can't see
    b._on_message(admin, json.dumps({"type": "graveyard", "id": lbl.id}))
    assert lbl.id in b._graveyarded
    b._on_message(viewer, json.dumps({"type": "restore", "id": lbl.id}))
    assert lbl.id in b._graveyarded          # nor resurrect it
    b._on_message(admin, json.dumps({"type": "restore", "id": lbl.id}))
    assert lbl.id not in b._graveyarded and lbl.visible


# -- no-auth canvases are unchanged --------------------------------------------

def test_unrestricted_canvas_accepts_input_from_unknown_socket():
    # No passwords, no roles: a socket the bridge has no viewer entry for (e.g.
    # the merge host) must keep working exactly as before.
    b, calls, admin, viewer = _bridge()
    sld = _control(b)
    stranger = _WS("stranger")
    b._connections.add(stranger)
    assert _fires(b, sld, stranger)
