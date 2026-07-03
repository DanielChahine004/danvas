"""danvas.connect(): the native Canvas API over a dial-in connection.

RemoteCanvas hosts the REAL component classes on a socket-backed bridge, so
factories, live setters, handlers, and lookup behave exactly as in a served
script — the frames just go up one pipe. These tests capture that pipe.
"""

import time

import danvas
from danvas.remote import RemoteCanvas


def _canvas():
    """A RemoteCanvas with the socket replaced by a frame recorder."""
    c = RemoteCanvas("127.0.0.1:8000", label="rig")
    sent = []
    c._client._send = lambda msg: sent.append(msg)
    return c, sent


def _of(sent, kind, cid=None):
    return [m for m in sent
            if m.get("type") == kind and (cid is None or m.get("id") == cid)]


def test_native_factory_emits_register():
    c, sent = _canvas()
    s = c.slider("servo", min=0, max=180)
    regs = _of(sent, "register", s.id)
    assert regs and regs[0]["component"]        # real register frame
    assert c["servo"] is s                       # native name lookup
    assert s in c.components


def test_native_setters_and_layout_emit_updates():
    c, sent = _canvas()
    s = c.slider("servo", min=0, max=180)
    sent.clear()
    s.min = 10                                   # native property setter
    assert _of(sent, "update", s.id)
    sent.clear()
    s.set_layout(x=300, y=40)
    assert sent                                  # layout rode the pipe
    assert (s.x, s.y) == (300, 40)               # local object followed


def test_hub_input_fires_native_on_change():
    c, sent = _canvas()
    s = c.slider("servo", min=0, max=180)
    got = []
    s.on_change(lambda v: got.append(v))
    # the hub routes a browser's input to this source (namespace pre-stripped)
    c._on_hub_frame({"type": "input", "id": s.id, "payload": {"value": 42}})
    for _ in range(100):                         # real dispatch thread
        if got:
            break
        time.sleep(0.01)
    assert got == [42]
    assert s.value == 42


def test_hub_layout_fires_native_on_layout_and_moves_object():
    c, sent = _canvas()
    s = c.slider("servo", min=0, max=180)
    got = []
    s.on_layout(lambda comp: got.append((comp.x, comp.y)))
    c._on_hub_frame({"type": "layout", "id": s.id, "x": 111, "y": 222})
    for _ in range(100):
        if got:
            break
        time.sleep(0.01)
    assert (s.x, s.y) == (111, 222)


def test_replay_reconstructs_all_panels():
    c, sent = _canvas()
    s = c.slider("servo", min=0, max=180)
    lbl = c.label("status", "idle")
    frames = list(c._replay_frames())
    ids = [m["id"] for m in frames if m["type"] == "register"]
    assert set(ids) == {s.id, lbl.id}


def test_remove_and_shared_plane_passthrough():
    c, sent = _canvas()
    s = c.slider("servo", min=0, max=180)
    c.remove(s)
    assert _of(sent, "remove", s.id)
    sent.clear()
    c.set_props("foreign-panel", min=5)          # another process's panel
    assert sent[-1] == {"type": "set_props", "id": "foreign-panel",
                        "props": {"min": 5}}
    c.subscribe("foreign-btn", lambda p: None)
    assert sent[-1] == {"type": "subscribe", "id": "foreign-btn"}
    # foreign panels mirror through .shared
    c._client._handle({"type": "register", "id": "foreign-panel",
                       "component": "React", "props": {}})
    assert "foreign-panel" in c.shared


def test_serve_is_refused_with_a_pointer():
    c, _ = _canvas()
    import pytest
    with pytest.raises(RuntimeError, match="joins an already-served"):
        c.serve()


def test_connect_is_exported():
    assert callable(danvas.connect)
    assert danvas.RemoteCanvas is RemoteCanvas


def test_connect_is_still_the_arrow_verb():
    # RemoteCanvas.dial() is the session verb precisely so Canvas.connect(a, b)
    # keeps its danvas meaning — an arrow — and rides the socket like any frame.
    c, sent = _canvas()
    a = c.slider("a", min=0, max=1)
    b = c.label("b", "x")
    arrow = c.connect(a, b, text="a->b")
    assert arrow in c.arrows
    frames = [m for m in sent if m.get("type") == "arrow"]
    assert frames and frames[-1]["start"] == a.id and frames[-1]["end"] == b.id
    assert callable(c.dial)                      # the session verb, renamed
