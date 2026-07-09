"""Tunnel-keeper plumbing (no network: the live keeper is exercised by
hand/CI-optional — these lock the state-file and lifecycle contracts)."""

import json
import os

from danvas.tunnel import Tunnel, _pid_alive, _read_state, _state_path


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    path = _state_path(9999)
    assert str(tmp_path) in path and "9999" in path
    assert _read_state(9999) is None            # nothing there yet
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"url": "https://x", "pid": 1, "port": 9999,
                   "provider": "cloudflared"}, f)
    assert _read_state(9999)["url"] == "https://x"
    with open(path, "w", encoding="utf-8") as f:
        f.write("not json")
    assert _read_state(9999) is None            # corrupt file reads as absent


def test_pid_alive():
    assert _pid_alive(os.getpid())
    assert not _pid_alive(2 ** 22 + 12345)      # implausible pid


def test_keeper_tunnel_stop_is_noop():
    # A Tunnel handed out by ensure_tunnel has no process of its own: the
    # keeper owns it, so the serving script stopping must NOT kill the URL.
    t = Tunnel(None, "https://keeper", "cloudflared")
    t.stop()                                    # must not raise / touch anything
    assert t.url == "https://keeper"
