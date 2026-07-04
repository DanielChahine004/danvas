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


def test_binary_media_and_input_cross_the_socket():
    # Media down: video-style bytes ride _send_binary (not dropped anymore).
    c, sent = _canvas()
    blobs = []
    c._client._send_binary = lambda d: blobs.append(bytes(d))
    feed = c.video("cam", encode=False)
    feed.update(b"\xff\xd8fakejpeg")
    assert blobs and blobs[-1][0] == 1            # VIDEO envelope
    assert blobs[-1].endswith(b"fakejpeg")
    # Input up: a routed binary INPUT envelope dispatches @on_binary.
    got = []
    feed.on_binary(lambda d: got.append(bytes(d)))
    cid = feed.id.encode()
    c._client._binary_hook(bytes([5, len(cid)]) + cid + b"mic-bytes")
    import time
    for _ in range(100):
        if got:
            break
        time.sleep(0.01)
    assert got == [b"mic-bytes"]


def test_file_pull_serves_this_processes_download_tokens():
    from danvas.remote import _dispatch_hub_frame
    c, sent = _canvas()
    blobs = []
    c._client._send_binary = lambda d: blobs.append(bytes(d))
    url = c.serve_bytes(b"REPORT-DATA", "report.pdf")
    token = url.rsplit("/", 1)[-1]
    _dispatch_hub_frame(c._bridge, {"type": "file_pull", "token": token,
                                    "reqId": "r1"})
    metas = [m for m in sent if m.get("type") == "file_meta"]
    assert metas[-1]["ok"] is True and metas[-1]["filename"] == "report.pdf"
    assert blobs and blobs[-1][0] == 6            # FILE envelope
    assert blobs[-1].endswith(b"REPORT-DATA")
    # a token we don't own is declined
    _dispatch_hub_frame(c._bridge, {"type": "file_pull", "token": "nope",
                                    "reqId": "r2"})
    assert [m for m in sent if m.get("type") == "file_meta"
            and m.get("reqId") == "r2"][-1]["ok"] is False


def test_file_push_delivers_to_this_processes_upload_endpoint():
    from danvas.remote import _dispatch_hub_frame, _hub_binary
    c, sent = _canvas()
    got = []
    url = c.receive_files(lambda f: got.append(f))
    token = url.rsplit("/", 1)[-1]
    _dispatch_hub_frame(c._bridge, {"type": "file_push", "token": token,
                                    "reqId": "u1", "name": "d.csv",
                                    "content_type": "text/csv"})
    rid = b"u1"
    _hub_binary(c._bridge, bytes([6, len(rid)]) + rid + b"CSV,1,2")
    import time
    for _ in range(100):
        if got:
            break
        time.sleep(0.01)
    assert got and got[0].data == b"CSV,1,2" and got[0].name == "d.csv"
    acks = [m for m in sent if m.get("type") == "file_ack"]
    assert acks[-1]["ok"] is True and acks[-1]["size"] == 7
    # a push for an endpoint we don't own is declined
    _dispatch_hub_frame(c._bridge, {"type": "file_push", "token": "alien",
                                    "reqId": "u2", "name": "x"})
    _hub_binary(c._bridge, bytes([6, 2]) + b"u2" + b"zz")
    assert [m for m in sent if m.get("type") == "file_ack"
            and m.get("reqId") == "u2"][-1]["ok"] is False
