"""Append-only SQLite persistence ledger for ``serve(persist=...)``.

The JSON persist file rewrites the whole canvas state on every (debounced)
change — O(canvas) I/O per edit, and a crash between debounce and flush loses
the last quiet-window of changes. The ledger is the append-only twin: pass a
``persist=`` path ending in ``.db`` / ``.sqlite`` / ``.sqlite3`` and the same
state dict is *appended* as a snapshot row (WAL mode, one small transaction per
flush), alongside a fine-grained ``events`` table recording each user action
(``input`` / ``layout`` / ``draw`` frames) as it arrives — so after a crash the
history is queryable down to the last committed event::

    canvas.serve(persist="board.canvas.db")

    # later, forensics with nothing but sqlite3:
    #   SELECT ts, type, comp, payload FROM events ORDER BY seq DESC LIMIT 20;
    #   SELECT state FROM snapshots ORDER BY seq DESC LIMIT 1;

Restore semantics are identical to the JSON file: on startup the *latest*
snapshot is applied through the same ``_restore_layout`` path. The events table
is a record, not the restore source — replaying user input through handlers on
startup would re-run side effects.

Thread model: appends arrive from the persist timer thread (snapshots) and the
event-loop/dispatch threads (events); one connection guarded by a lock keeps
them serialised (the writes are tiny, so contention is negligible).
"""

import json
import os
import sqlite3
import threading
import time
import uuid
import warnings

# persist= paths with these extensions select the ledger backend; anything
# else keeps the historical JSON file.
LEDGER_EXTENSIONS = (".db", ".sqlite", ".sqlite3")

# Inbound frame types worth recording as events: user actions with meaning.
# High-rate session plumbing (cursor, heartbeat) is deliberately excluded.
EVENT_TYPES = ("input", "layout", "draw", "chat", "graveyard", "restore")

# Snapshots to keep: enough history to inspect "how did the board evolve"
# without unbounded growth (events are small and kept forever).
SNAPSHOT_KEEP = 200

_SCHEMA_VERSION = 1


def is_ledger_path(path):
    """True if ``path`` selects the SQLite ledger backend for persist=."""
    return str(path).lower().endswith(LEDGER_EXTENSIONS)


class Ledger:
    """One canvas's append-only persistence ledger (a local SQLite file).

    Raises ``sqlite3.DatabaseError`` if ``path`` exists but is not a SQLite
    database — the caller decides whether to start fresh (persist does, after
    setting the corrupt file aside).
    """

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        try:
            # WAL keeps readers (an external sqlite3 shell mid-run) from
            # blocking the appends; NORMAL sync is durable to the OS on every
            # commit and loses at most the final commit on power loss — the
            # right trade for a UI ledger written on every user action.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
        except BaseException:
            # A failed open (corrupt/non-SQLite file) must release the handle,
            # or Windows keeps the file locked and it can't be set aside.
            self._conn.close()
            raise

    def _init_schema(self):
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta ("
                " key TEXT PRIMARY KEY, value TEXT)")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS snapshots ("
                " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " ts REAL NOT NULL,"
                " state TEXT NOT NULL)")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " ts REAL NOT NULL,"
                " type TEXT NOT NULL,"
                " comp TEXT,"
                " payload TEXT)")
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(_SCHEMA_VERSION)))

    # -- writes ---------------------------------------------------------------
    def append_snapshot(self, state):
        """Append one full-state snapshot (the persist-flush payload) and prune
        history beyond :data:`SNAPSHOT_KEEP` rows."""
        text = json.dumps(state, default=str)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO snapshots (ts, state) VALUES (?, ?)",
                (time.time(), text))
            self._conn.execute(
                "DELETE FROM snapshots WHERE seq <= ("
                " SELECT MAX(seq) FROM snapshots) - ?", (SNAPSHOT_KEEP,))

    def append_event(self, type, comp, payload):
        """Append one user-action event. Payload is stored as JSON text
        (non-JSON-able values coerced via ``str`` — a record, not a replay)."""
        try:
            text = json.dumps(payload, default=str)
        except (TypeError, ValueError):
            text = None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events (ts, type, comp, payload) VALUES (?, ?, ?, ?)",
                (time.time(), type, comp, text))

    # -- reads ----------------------------------------------------------------
    def latest_state(self):
        """The newest snapshot as a dict, or ``None`` when the ledger is fresh."""
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM snapshots ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def events(self, limit=50, type=None):
        """The newest events (newest first), each
        ``{"seq", "ts", "type", "comp", "payload"}`` — the forensic query,
        available in-process so a script needn't shell out to sqlite3."""
        sql = "SELECT seq, ts, type, comp, payload FROM events"
        args = []
        if type is not None:
            sql += " WHERE type = ?"
            args.append(type)
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [{"seq": r[0], "ts": r[1], "type": r[2], "comp": r[3],
                 "payload": json.loads(r[4]) if r[4] else None} for r in rows]

    def close(self):
        with self._lock:
            self._conn.close()


def open_ledger(path):
    """Open (or create) a ledger, healing corruption the way the JSON backend
    does: an unreadable file is set aside as ``<path>.corrupt-<hex>`` (never
    deleted — it may hold recoverable rows) and a fresh ledger starts."""
    try:
        return Ledger(path)
    except sqlite3.DatabaseError:
        aside = f"{path}.corrupt-{uuid.uuid4().hex[:8]}"
        warnings.warn(
            f"persist: could not open ledger {path!r}; set aside as {aside!r} "
            "and starting fresh", stacklevel=3)
        try:
            os.replace(path, aside)
        except OSError:
            raise  # can't move it out of the way -> surface the original problem
        return Ledger(path)
