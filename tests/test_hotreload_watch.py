"""serve(hot_reload=True, watch=[...]) — the monitor watches extra files.

The hot-reload monitor always watches top-level .py files; ``watch`` globs add
others (e.g. a panel's JSX loaded via path=), resolved under the script's
directory. _snapshot is the pure mtime-collector behind the monitor's poll loop.
"""

import os

from danvas.hotreload import _snapshot


def _touch(path, content=""):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_snapshot_watches_only_py_by_default(tmp_path):
    _touch(tmp_path / "app.py")
    _touch(tmp_path / "panel.jsx")
    _touch(tmp_path / "style.css")

    snap = _snapshot(str(tmp_path), [])

    assert {os.path.basename(p) for p in snap} == {"app.py"}


def test_snapshot_includes_watched_globs(tmp_path):
    _touch(tmp_path / "app.py")
    _touch(tmp_path / "panel.jsx")
    _touch(tmp_path / "style.css")

    snap = _snapshot(str(tmp_path), ["*.jsx", "*.css"])

    assert {os.path.basename(p) for p in snap} == {"app.py", "panel.jsx", "style.css"}


def test_snapshot_globs_reach_subdirectories(tmp_path):
    _touch(tmp_path / "app.py")
    sub = tmp_path / "panels"
    sub.mkdir()
    _touch(sub / "a.json")
    _touch(sub / "b.json")

    snap = _snapshot(str(tmp_path), ["panels/*.json"])

    assert {os.path.basename(p) for p in snap} == {"app.py", "a.json", "b.json"}


def test_snapshot_tracks_mtime_changes(tmp_path):
    f = tmp_path / "panel.jsx"
    _touch(f, "v1")
    _touch(tmp_path / "app.py")

    before = _snapshot(str(tmp_path), ["*.jsx"])
    key = next(k for k in before if k.endswith("panel.jsx"))
    os.utime(f, (before[key] + 5, before[key] + 5))    # bump mtime
    after = _snapshot(str(tmp_path), ["*.jsx"])

    assert after[key] != before[key]                   # the change is seen
