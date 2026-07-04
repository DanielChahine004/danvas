"""Single source of truth for the Python <-> browser wire protocol.

The backend (this package) and the frontend (``danvas/frontend/src/*.js``)
speak a small, hand-maintained protocol over one WebSocket: numeric codes for
binary media frames, string ``type`` tags for JSON frames, and the lock/chrome
flag *wire* keys. A *second*, separate channel exists too: a sandboxed Custom
panel and the parent page talk over ``window.postMessage`` with the
``__danvas_*`` discriminator keys (no WebSocket — the iframe can't open one). A
change to one side that doesn't reach the other silently desyncs them — the
"protocol drift" failure mode that has no loud symptom (a wrong binary code just
routes a frame to the wrong handler; a wrong wire key just corrupts a panel's
lock meta; a wrong postMessage key just drops the message). This module is the
canonical definition for both channels; the two sides are wired back to it so
they can't drift:

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

# -- protocol version --------------------------------------------------------
# The frozen wire-contract version, advertised in the ``welcome`` frame so any
# client (a browser, a merge hub, or a non-Python SDK speaking this protocol)
# can detect a server it doesn't understand. Versioning policy (see PROTOCOL.md,
# the human-readable spec rendered from this module):
#
# * ADDITIVE changes — a new message type, a new optional field on an existing
#   frame — do NOT bump the version. Clients must ignore unknown frame types and
#   unknown fields (both sides already do).
# * BREAKING changes — removing/renaming a frame type or field, changing a
#   binary code, changing the binary envelope — bump the version. These should
#   be vanishingly rare now the protocol is frozen.
PROTOCOL_VERSION = 1

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
    "FILE": 6,    # hub <-> owner file transfer (id field = reqId, not a panel)
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
    "graveyard_update", "shared", "chat", "response", "hosting",
)
MESSAGE_TYPES_IN = (
    "heartbeat", "cursor", "set_name", "chat", "ui", "input", "layout",
    "graveyard", "restore", "draw", "request", "panel_error", "snapshot",
    "image", "set_props", "subscribe", "unsubscribe",
)

# -- merge control-plane tags (browser <-> merge server) --------------------
# A *separate* channel from the canvas protocol above: these ride between a
# browser and the standing *merge server* (danvas.merge), not a normal canvas.
# The merge server relays a per-connection set of source canvases and speaks this
# small control vocabulary on top of the base canvas frames it forwards (register/
# update/remove/arrow going down; input/layout going up — those stay in the lists
# above). Kept off ``MESSAGE_TYPES_*`` so ``test_protocol_sync`` still checks those
# against ``Bridge._on_message`` / ``bridge.ts handle()``; the merge set is checked
# against ``MergeBridge._route_from_browser`` (inbound) and the merge server's own
# emit sites + the frontend merge handler (outbound). Inbound = browser -> merge
# server; outbound = merge server -> browser.
MERGE_MESSAGE_TYPES_IN = (
    "merge_add", "merge_auth", "merge_remove", "merge_offset",
)
MERGE_MESSAGE_TYPES_OUT = (
    "merge_sources", "merge_auth_required", "merge_auth_failed",
)


# -- Custom-panel iframe postMessage protocol -------------------------------
# A *sandboxed* Custom iframe and the parent page can't share a typed channel, so
# they talk over ``window.postMessage`` with these discriminator keys: the key
# present on the message object IS the message type. The iframe half is built as a
# string in :mod:`danvas.components.custom` (the injected ``window.canvas`` shim);
# the parent half lives in the frontend's ``bridge.ts`` (the global relay) and
# ``react/CustomView.tsx`` (push + theme). Because there's no structured frame, a
# key typed differently on the two sides *silently drops the message* — the same
# drift failure the wire codes above have. Declared once here (symbolic name ->
# wire key); ``gen_protocol.py`` exports it as ``IFRAME_MSG`` and
# ``test_protocol_sync.py`` scans both sides and fails if either uses a key that
# isn't in this set, or omits one.
IFRAME_MESSAGE_KEYS = {
    "SEND": "__danvas",                          # iframe->parent send / parent->iframe push (onPush)
    "BINARY": "__danvas_binary",                 # iframe->parent sendBinary
    "REQUEST": "__danvas_request",               # iframe->parent request(data)
    "RESPONSE": "__danvas_response",             # parent->iframe request reply
    "SETVIEW": "__danvas_setview",               # iframe->parent setView()
    "VIEWPORT": "__danvas_viewport",             # iframe<->parent viewport sub + value push
    "CHAT": "__danvas_chat",                     # iframe->parent chat actions
    "CHAT_REPLY": "__danvas_chat_reply",         # parent->iframe chat history() reply
    "CHAT_MSG": "__danvas_chat_msg",             # parent->iframe new chat line
    "CHAT_IDENTITY": "__danvas_chat_identity",   # parent->iframe identity change
    "CAMERA": "__danvas_camera",                 # iframe->parent requestCamera/release
    "MIC": "__danvas_mic",                       # iframe->parent requestMicrophone/release
    "ERROR": "__danvas_error",                   # iframe->parent JS error report
    "WHEEL": "__danvas_wheel",                   # iframe->parent wheel -> canvas zoom
    "PAN": "__danvas_pan",                       # iframe->parent pan delta
    "MENU": "__danvas_menu",                     # iframe->parent context-menu request
    "KEY": "__danvas_key",                       # iframe->parent tool-shortcut key
    "FIT": "__danvas_fit",                       # iframe->parent content-fit size
    "THEME": "__danvas_theme",                   # parent->iframe theme vars + dark flag
}


def as_dict():
    """The whole protocol as one plain dict — what the JS generator renders."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "binary_frame_codes": dict(BINARY_FRAME_CODES),
        "flag_wire_keys": dict(FLAG_WIRE_KEYS),
        "message_types_out": list(MESSAGE_TYPES_OUT),
        "message_types_in": list(MESSAGE_TYPES_IN),
        "merge_message_types_out": list(MERGE_MESSAGE_TYPES_OUT),
        "merge_message_types_in": list(MERGE_MESSAGE_TYPES_IN),
        "iframe_message_keys": dict(IFRAME_MESSAGE_KEYS),
    }