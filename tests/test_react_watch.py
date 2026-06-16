"""React.watch(): hot-reload a panel's source/css from disk during development."""

import time

import pytest

import pycanvas


class RecordingBridge:
    def __init__(self):
        self.updates = []

    def broadcast(self, msg, exclude=None):
        self.updates.append(msg)


def _wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_watch_reloads_source_on_change(tmp_path):
    f = tmp_path / "panel.jsx"
    f.write_text("function Component(){ return 1; }", encoding="utf-8")
    panel = pycanvas.React(path=str(f))
    bridge = RecordingBridge()
    panel._bind("p1", bridge)

    stop = panel.watch(interval=0.05)
    try:
        f.write_text("function Component(){ return 2; }", encoding="utf-8")
        assert _wait_for(lambda: any(
            u["payload"].get("source", "").find("return 2") >= 0
            for u in bridge.updates))
    finally:
        stop()
    assert panel._source == "function Component(){ return 2; }"


def test_watch_does_not_push_on_startup(tmp_path):
    f = tmp_path / "panel.jsx"
    f.write_text("function Component(){ return 1; }", encoding="utf-8")
    panel = pycanvas.React(path=str(f))
    bridge = RecordingBridge()
    panel._bind("p1", bridge)

    stop = panel.watch(interval=0.05)
    try:
        time.sleep(0.2)            # no file change
        assert bridge.updates == []   # the initial on-disk state isn't re-pushed
    finally:
        stop()


def test_watch_css_path(tmp_path):
    src = tmp_path / "p.jsx"
    src.write_text("function Component(){ return null; }", encoding="utf-8")
    css = tmp_path / "p.css"
    css.write_text(".a{color:red}", encoding="utf-8")
    panel = pycanvas.React(path=str(src), css=".a{color:red}")
    bridge = RecordingBridge()
    panel._bind("p1", bridge)

    stop = panel.watch(css_path=str(css), interval=0.05)
    try:
        css.write_text(".a{color:blue}", encoding="utf-8")
        assert _wait_for(lambda: any(
            ".a{color:blue}" in u["payload"].get("css", "") for u in bridge.updates))
    finally:
        stop()


def test_watch_without_path_raises():
    panel = pycanvas.React(source="function Component(){ return null; }")
    with pytest.raises(ValueError):
        panel.watch()
