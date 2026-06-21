"""Single source of truth for the Python <-> browser wire protocol.

The backend (this package) and the frontend (``danvas/frontend/src/*.js``)
speak a small, hand-maintained protocol over one WebSocket: numeric codes for
binary media frames, string ``type`` tags for JSON frames, and the lock/chrome
flag *wire* keys. Today those constants are restated independently on each side
(``BINARY_*`` in :mod:`danvas.bridge`, ``BIN_*`` in ``bridge.js``; the flag
wire keys in :mod:`danvas._flags` vs the destructured params in
``bridge.js``), so a change to one side silently desyncs the other — the exact
"protocol drift" failure mode that has no loud symptom (a wrong binary code just
routes a frame to the wrong handler; a wrong wire key just corrupts a panel's
lock meta).

This module is the canonical definition. The plan (workstream C, single-source +
codegen):

1.  Everything is declared here, once.
2.  ``scripts/gen_protocol.py`` renders it to
    ``danvas/frontend/src/protocol.generated.js`` so the JS imports the same
    values instead of re-typing them.
3.  :mod:`danvas.bridge` and :mod:`danvas._flags` import the codes/keys from
    here (rather than restating them), and ``tests/test_protocol_sync.py`` fails
    if either side — or the committed generated JS — drifts from this file.

Until the integration step lands, the two sides still hold their own copies;
``test_protocol_sync`` asserts they equal what is declared here, which both
validates this module and acts as the drift guard in the meantime.
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
# visibly), but they're enumerated here so the generated JS can expose named
# constants and a reader has the whole vocabulary in one place. Verified against
# the ``bridge.js`` inbound switch (outbound) and ``Bridge._on_message``
# (inbound). Outbound = server -> browser; inbound = browser -> server.
MESSAGE_TYPES_OUT = (
    "register", "arrow", "shape", "shape_update", "update", "order", "remove",
    "get_snapshot", "load_snapshot", "draw", "presence",
    "cursor", "cursor_gone", "view", "welcome", "chat",
    "response", "complete_result", "shared",
)
MESSAGE_TYPES_IN = (
    "heartbeat", "cursor", "set_name", "chat", "ui",
    "input", "layout", "draw", "request", "snapshot", "panel_error",
)


def as_dict():
    """The whole protocol as one plain dict — what the JS generator renders."""
    return {
        "binary_frame_codes": dict(BINARY_FRAME_CODES),
        "flag_wire_keys": dict(FLAG_WIRE_KEYS),
        "message_types_out": list(MESSAGE_TYPES_OUT),
        "message_types_in": list(MESSAGE_TYPES_IN),
    }