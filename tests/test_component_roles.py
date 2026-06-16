"""Live role changes on a panel: add_role / remove_role and the roles property.

These mutate a panel's role allowlist after it's bound, pushing the panel to
newly allowed roles and dropping it from removed ones. A stub bridge records the
calls (the real bridge's pushes no-op until it's serving).
"""

import pycanvas


class StubBridge:
    def __init__(self):
        self.registered = []   # (component, only_roles)
        self.removed = []      # (role, msg)

    def register_live(self, component, only_roles=None):
        self.registered.append((component, only_roles))

    def send_to_role(self, role, msg):
        self.removed.append((role, msg))


def make(roles):
    s = pycanvas.Slider("s")
    s._roles = list(roles)
    bridge = StubBridge()
    s._bind("s1", bridge)
    return s, bridge


def test_add_role_appends_and_pushes_only_new_role():
    s, bridge = make(["admin"])
    s.add_role("Red")
    assert s.roles == ["admin", "Red"]
    assert bridge.registered == [(s, {"Red"})]


def test_add_role_ignores_duplicates_and_skips_push():
    s, bridge = make(["admin", "Red"])
    s.add_role("Red")
    assert s.roles == ["admin", "Red"]
    assert bridge.registered == []   # nothing new => no live push


def test_add_multiple_roles_at_once():
    s, bridge = make([])
    s.add_role("a", "b")
    assert s.roles == ["a", "b"]
    assert bridge.registered == [(s, {"a", "b"})]


def test_remove_role_drops_live_while_still_restricted():
    s, bridge = make(["admin", "Red"])
    s.remove_role("Red")
    assert s.roles == ["admin"]
    assert bridge.removed == [("Red", {"type": "remove", "id": "s1"})]


def test_remove_last_role_keeps_panel_for_everyone():
    s, bridge = make(["Red"])
    s.remove_role("Red")
    assert s.roles == []           # empty allowlist => visible to all roles
    assert bridge.removed == []    # so it isn't yanked from anyone


def test_remove_unknown_role_is_a_noop():
    s, bridge = make(["admin"])
    s.remove_role("ghost")
    assert s.roles == ["admin"]
    assert bridge.removed == []


def test_roles_property_returns_a_copy():
    s, _ = make(["admin"])
    s.roles.append("x")
    assert s.roles == ["admin"]


def test_add_role_returns_self_for_chaining():
    s, _ = make([])
    assert s.add_role("a").remove_role("a") is s
