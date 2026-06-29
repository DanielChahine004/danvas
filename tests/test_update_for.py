"""Per-recipient props: React.update_for(role=/client_id=).

Sends the panel's props merged with overrides to specific viewers only, without
touching the shared (broadcast) props. A stub bridge records the targeted sends.
"""

import json

import danvas


class StubBridge:
    def __init__(self):
        self.to_role = []     # (role, msg)
        self.to_client = []   # (client_id, msg)
        self.broadcasts = []

    def send_to_role(self, role, msg):
        self.to_role.append((role, msg))

    def send_to_client(self, client_id, msg):
        self.to_client.append((client_id, msg))

    def broadcast(self, msg, exclude=None):
        self.broadcasts.append(msg)


def make():
    panel = danvas.React("function Component(){return null}", name="p",
                           props={"a": 1})
    bridge = StubBridge()
    panel._bind("p1", bridge)
    return panel, bridge


def _data(msg):
    return json.loads(msg["payload"]["data"])


def test_update_for_role_sends_merged_props_to_role():
    panel, bridge = make()
    panel.update_for(role="user", b=2)
    assert bridge.to_role == [("user", bridge.to_role[0][1])]
    role, msg = bridge.to_role[0]
    assert msg["id"] == "p1" and msg["type"] == "update"
    assert _data(msg) == {"a": 1, "b": 2}     # shared prop kept, override added
    assert bridge.to_client == []
    assert bridge.broadcasts == []            # shared state untouched


def test_update_for_does_not_change_shared_props():
    panel, bridge = make()
    panel.update_for(role="user", b=2)
    # A later broadcast update sends only the changed key (a delta) and must NOT
    # carry the per-viewer override (b); the frontend merges it onto shared state.
    panel.update(c=3)
    assert bridge.broadcasts[0]["payload"]["data_patch"] == {"c": 3}


def test_update_for_client_id():
    panel, bridge = make()
    panel.update_for(client_id="v9", balance=500)
    assert bridge.to_client and bridge.to_client[0][0] == "v9"
    assert _data(bridge.to_client[0][1]) == {"a": 1, "balance": 500}


def test_update_for_role_list():
    panel, bridge = make()
    panel.update_for(role=["red", "blue"], x=1)
    assert [r for r, _ in bridge.to_role] == ["red", "blue"]


def test_update_for_both_role_and_client():
    panel, bridge = make()
    panel.update_for(role="user", client_id="v1", k=9)
    assert len(bridge.to_role) == 1 and len(bridge.to_client) == 1


def test_update_for_without_target_is_noop():
    panel, bridge = make()
    assert panel.update_for(z=1) is panel
    assert bridge.to_role == [] and bridge.to_client == []
