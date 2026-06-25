"""Single source of truth for the Python <-> browser wire protocol.

The backend (this package) and the frontend (``danvas/frontend/src/*.js``)
speak a small, hand-maintained protocol over one WebSocket: numeric codes for
binary media frames, string ``type`` tags for JSON frames, and the lock/chrome
flag *wire* keys. A change to one side that doesn't reach the other silently
desyncs them — the "protocol drift" failure mode that has no loud symptom (a
wrong binary code just routes a frame to the wrong handler; a wrong wire key
just corrupts a panel's lock meta). This module is the canonical definition; the
two sides are wired back to it so they can't drift:

1.  Everything is declared here, once.
2.  ``scripts/gen_protocol.py`` renders it to
    ``danvas/frontend/src/protocol.generated.js``; ``bridge.js`` imports the
    ``BIN_*`` codes from there rather than re-typing them.
3.  On the Python side :mod:`danvas.bridge` imports :data:`BINARY_FRAME_CODES`
    and :mod:`danvas._flags` imports :data:`FLAG_WIRE_KEYS` from here, so neither
    restates the constants.
4.  ``tests/test_protocol_sync.py`` is the guard: it fails if either side — or
    the committed generated JS — drifts from this file. The binary codes and
    flag wire keys are checked by value; the JSON ``type`` tags below are checked
    against the dispatch tables that actually handle them (``bridge.js``'s
    ``handle()`` for outbound, ``Bridge._on_message`` for inbound), so a tag
    added on one side without updating this list fails loudly.
"""

from __future__ import annotations

# -- binary media frame type codes ------------------------------------------
# A binary WebSocket frame is ``[type][idLen][id bytes][payload]`` (see
# ``bridge.encode_binary_frame``); ``type`` is one of these. Numeric and
# order-significant: a mismatch routes a frame to the wrong frontend handler
# with no error, so this is the highest-value thing to keep in lockstep.
BINARY_FRAME_CODES = {
    "VIDEO": 1,   # JPEG-encoded frame bytes
    "AUDIO": 2,   # little-endian int16 PCM samples (interleaved)
    "CUSTOM": 3,  # opaque user bytes -> Custom.push_binary -> canvas.onPush
    "REACT": 4,   # opaque user bytes -> React.push_binary -> canvas.onFrame
    "INPUT": 5,   # browser -> Python raw bytes (canvas.sendBinary -> @on_binary)
}

# -- lock / chrome flag wire keys -------------------------------------------
# The browser-facing key for each Python-side flag. The Python name is
# deliberately distinct from the wire key (e.g. ``draggable`` -> ``movable``);
# the frontend's ``lockMeta``/``registerComponent`` read these wire names. Keep
# in step with :data:`danvas._flags.LAYOUT_FLAGS` (whose ``.wire`` is the same
# value) — the integration step makes ``_flags`` import this rather than restate
# it. Order matches LAYOUT_FLAGS insertion order.
FLAG_WIRE_KEYS = {
    "locked": "locked",
    "draggable": "movable",
    "resizable": "resizable",
    "operable": "interactive",
    "grabbable": "selectable",
    "frame": "frame",
}

# -- JSON frame ``type`` tags -----------------------------------------------
# String tags are lower-risk than the numeric codes (a typo tends to fail
# visibly), but they're enumerated here so a reader has the whole vocabulary in
# one place and ``test_protocol_sync`` can guard them: the outbound list must
# equal the tags ``bridge.js``'s ``handle()`` dispatches on, and the inbound list
# the tags ``Bridge._on_message`` handles. Order mirrors those dispatch tables
# (the test compares as sets, so order is for readers only). Outbound = server ->
# browser; inbound = browser -> server.
MESSAGE_TYPES_OUT = (
    "register", "arrow", "shape", "shape_update", "update", "order", "remove",
    "container_sync", "reflow", "get_snapshot", "get_image", "load_snapshot",
    "draw", "presence", "cursor", "cursor_gone", "view", "welcome",
    "graveyard_update", "shared", "chat", "response",
)
MESSAGE_TYPES_IN = (
    "heartbeat", "cursor", "set_name", "chat", "ui", "input", "layout",
    "graveyard", "restore", "draw", "request", "panel_error", "snapshot",
    "image",
)


def as_dict():
    """The whole protocol as one plain dict — what the JS generator renders."""
    return {
        "binary_frame_codes": dict(BINARY_FRAME_CODES),
        "flag_wire_keys": dict(FLAG_WIRE_KEYS),
        "message_types_out": list(MESSAGE_TYPES_OUT),
        "message_types_in": list(MESSAGE_TYPES_IN),
    }