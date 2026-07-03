"""serve(persist="*.db"): the append-only SQLite ledger backend.

Same restore semantics as the JSON file (latest snapshot through
_restore_layout), plus what JSON can't do: snapshots accumulate instead of
overwriting, and user actions (input/layout/draw) are recorded to a queryable
events table.
"""

import json
import sqlite3

import pytest

import danvas
from danvas import _ledger


def _build(canvas):
    s = canvas.slider("vol", min=0, max=10)
    canvas.insert(s, x=10, y=20, w=200, h=80)
    b = canvas.button("go")
    canvas.insert(b, x=300, y=400)
    return s, b


def test_ledger_path_detection():
    assert _ledger.is_ledger_path("board.canvas.db")
    assert _ledger.is_ledger_path("BOARD.SQLITE")
    assert _ledger.is_ledger_path("x.sqlite3")
    assert not _ledger.is_ledger_path("board.canvas.json")


def test_flush_appends_snapshots_and_restores_latest(tmp_path):
    path = str(tmp_path / "board.canvas.db")
    c1 = danvas.Canvas()
    s1, _ = _build(c1)
    c1._persist_setup(path)
    assert c1.ledger is not None
    assert c1._bridge._on_mutation is not None       # autosave armed

    c1._bridge._dispatch_layout(s1, {"x": 111, "y": 222})
    c1._persist_flush()
    c1._bridge._dispatch_layout(s1, {"x": 500, "y": 600})
    c1._persist_flush()

    # Append-only: both snapshots exist; the latest wins on restore.
    con = sqlite3.connect(path)
    n = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    con.close()
    assert n == 2

    c2 = danvas.Canvas()
    s2, _ = _build(c2)
    c2._persist_setup(path)
    assert (s2.x, s2.y) == (500, 600)


def test_user_actions_are_recorded_as_events(tmp_path):
    path = str(tmp_path / "board.canvas.db")
    c = danvas.Canvas()
    s, _ = _build(c)
    c._persist_setup(path)
    # Simulate inbound frames through the tap (what _on_message fires).
    c._bridge._tap_frame("in", {"type": "input", "id": s.id,
                                "payload": {"value": 7}})
    c._bridge._tap_frame("in", {"type": "layout", "id": s.id, "x": 5, "y": 6})
    c._bridge._tap_frame("in", {"type": "heartbeat"})       # plumbing: excluded
    c._bridge._tap_frame("in", {"type": "cursor", "x": 1})  # plumbing: excluded

    events = c.ledger.events()
    kinds = [e["type"] for e in events]
    assert kinds == ["layout", "input"]                     # newest first
    assert events[1]["comp"] == s.id
    assert events[1]["payload"]["payload"] == {"value": 7}
    assert c.ledger.events(type="input")[0]["type"] == "input"


def test_outbound_frames_are_not_recorded(tmp_path):
    path = str(tmp_path / "board.canvas.db")
    c = danvas.Canvas()
    _build(c)
    c._persist_setup(path)
    c._bridge._tap_frame("out", {"type": "update", "id": "x", "payload": {}})
    assert c.ledger.events() == []


def test_drawings_round_trip(tmp_path):
    path = str(tmp_path / "board.canvas.db")
    c1 = danvas.Canvas()
    _build(c1)
    c1._persist_setup(path)
    c1._bridge._apply_draw(
        {"added": {"shape:d": {"id": "shape:d"}}, "updated": {}, "removed": {}})
    c1._persist_flush()

    c2 = danvas.Canvas()
    _build(c2)
    c2._persist_setup(path)
    assert "shape:d" in c2._bridge._drawings


def test_input_values_restore_from_ledger(tmp_path):
    path = str(tmp_path / "b.canvas.db")
    c1 = danvas.Canvas()
    s1 = c1.slider("vol", min=0, max=10)
    s1.update(8)
    c1._persist_setup(path)
    c1._persist_flush()

    c2 = danvas.Canvas()
    s2 = c2.slider("vol", min=0, max=10)
    c2._persist_setup(path)
    assert s2.value == 8


def test_snapshot_pruning_caps_history(tmp_path):
    led = _ledger.Ledger(str(tmp_path / "x.db"))
    for i in range(_ledger.SNAPSHOT_KEEP + 25):
        led.append_snapshot({"n": i})
    con = sqlite3.connect(str(tmp_path / "x.db"))
    n = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    con.close()
    assert n <= _ledger.SNAPSHOT_KEEP
    assert led.latest_state()["n"] == _ledger.SNAPSHOT_KEEP + 24  # newest kept
    led.close()


def test_corrupt_ledger_is_set_aside_and_starts_fresh(tmp_path):
    path = tmp_path / "board.canvas.db"
    path.write_text("this is not a sqlite file at all, padded to be long "
                    "enough that sqlite rejects the header outright........",
                    encoding="utf-8")
    with pytest.warns(UserWarning, match="set aside"):
        led = _ledger.open_ledger(str(path))
    assert led.latest_state() is None                 # fresh ledger works
    led.append_snapshot({"ok": 1})
    assert led.latest_state() == {"ok": 1}
    led.close()
    assert list(tmp_path.glob("*.corrupt-*"))         # original preserved


def test_json_backend_untouched_by_ledger_code(tmp_path):
    # A .json path must keep the historical behavior: no ledger object, plain
    # JSON file on flush.
    path = str(tmp_path / "board.canvas.json")
    c = danvas.Canvas()
    _build(c)
    c._persist_setup(path)
    assert c.ledger is None
    c._persist_flush()
    assert json.loads(open(path).read())["layout"]["components"]


def test_ledger_event_payload_survives_non_jsonable(tmp_path):
    led = _ledger.Ledger(str(tmp_path / "x.db"))
    led.append_event("input", "c1", {"value": {1, 2, 3}})   # a set: coerced
    assert led.events()[0]["type"] == "input"
    led.close()
