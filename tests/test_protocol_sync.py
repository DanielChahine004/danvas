"""Guard the Python <-> browser wire protocol against drift.

The canonical contract lives in :mod:`danvas._protocol`, and the two sides are
wired back to it: ``bridge.py`` imports the binary codes, ``_flags.py`` imports
the lock wire keys, and ``bridge.ts`` imports the ``BIN_*`` codes from the
generated module. These tests assert those agree with the canonical module, that
the committed generated JS is not stale, and that the JSON ``type`` tag lists
match the dispatch tables that actually handle them — so a tag added on one side
without updating the canonical list fails loudly.

The binary codes and lock wire keys are the high-value targets: a mismatch there
fails *silently* in production (a frame routed to the wrong handler, a corrupted
lock meta), which is exactly what a test should catch instead of a user.
"""

import importlib.util
import inspect
import os
import re

import danvas.bridge as bridge
from danvas import _protocol
from danvas import merge as merge_mod
from danvas._flags import LAYOUT_FLAGS

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRIDGE_TS = os.path.join(_ROOT, "danvas", "frontend", "src", "bridge.ts")
_CUSTOM_VIEW_TSX = os.path.join(_ROOT, "danvas", "frontend", "src", "react",
                                "CustomView.tsx")
_CUSTOM_PY = os.path.join(_ROOT, "danvas", "components", "custom.py")
_CUSTOM_SHIM_TS = os.path.join(_ROOT, "danvas", "frontend", "src", "react",
                               "customShim.ts")
_EXPORT_TS = os.path.join(_ROOT, "danvas", "frontend", "src", "engine",
                          "export.ts")
_GEN_JS = os.path.join(_ROOT, "danvas", "frontend", "src",
                       "protocol.generated.js")
_GEN_SCRIPT = os.path.join(_ROOT, "scripts", "gen_protocol.py")

# Every ``__danvas*`` token: the bare key plus the underscored variants. The keys
# are all lowercase/underscore, so this never matches a camelCase identifier.
_IFRAME_KEY_RE = re.compile(r"__danvas[a-z_]*")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# -- Python side agrees with the canonical module ----------------------------
def test_bridge_binary_codes_match_protocol():
    for name, code in _protocol.BINARY_FRAME_CODES.items():
        assert getattr(bridge, f"BINARY_{name}") == code, (
            f"bridge.BINARY_{name} disagrees with _protocol")


def test_flag_wire_keys_match_protocol():
    layout_wire = {name: flag.wire for name, flag in LAYOUT_FLAGS.items()}
    assert layout_wire == _protocol.FLAG_WIRE_KEYS


# -- JS side agrees with the canonical module --------------------------------
def test_bridge_ts_imports_binary_codes_from_generated():
    # bridge.ts doesn't hardcode the codes — it imports them from the generated
    # module (whose values are guaranteed by the staleness test + the Python-side
    # check). Assert the import is present and that nothing re-declares them as
    # literals (which would silently shadow the import).
    src = _read(_BRIDGE_TS)
    m = re.search(r"import\s*\{([^}]*)\}\s*from\s*'\./protocol\.generated\.js'",
                  src)
    assert m, "bridge.ts must import the BIN_* codes from ./protocol.generated.js"
    imported = {name.strip() for name in m.group(1).split(",")}
    for name in _protocol.BINARY_FRAME_CODES:
        assert f"BIN_{name}" in imported, (
            f"bridge.ts does not import BIN_{name} from the generated module")
    assert not re.search(r"const BIN_\w+\s*=\s*\d+", src), (
        "bridge.ts still hardcodes a BIN_* code — it should import them")


def test_bridge_ts_names_the_flag_wire_keys():
    # registerComponent reads the lock wire keys off the message (msg.movable,
    # msg.resizable, …); every canonical wire key must appear, so a rename on
    # either side trips this.
    src = _read(_BRIDGE_TS)
    for wire in _protocol.FLAG_WIRE_KEYS.values():
        assert f"msg.{wire}" in src, (
            f"bridge.ts never reads the lock wire key msg.{wire}")


# -- JSON type tags match the dispatch tables that handle them ---------------
def test_message_types_out_match_bridge_ts_dispatch():
    # Outbound tags (server -> browser) are exactly what bridge.ts's handle()
    # switch dispatches on. Scope to that function, collect its `case '…':`
    # labels, and compare to the canonical list.
    src = _read(_BRIDGE_TS)
    m = re.search(r"function handle\(msg[^)]*\)[^{]*\{(.*?)\n\}", src, re.DOTALL)
    assert m, "couldn't find the handle(msg) dispatch in bridge.ts"
    # The same switch also dispatches the separate merge control channel
    # (case 'merge_*'); those are checked against MERGE_MESSAGE_TYPES_OUT
    # elsewhere, so exclude them from the canvas-protocol comparison here.
    handled = {c for c in re.findall(r"case '(\w+)':", m.group(1))
               if not c.startswith("merge_")}
    assert handled == set(_protocol.MESSAGE_TYPES_OUT), (
        "MESSAGE_TYPES_OUT disagrees with bridge.ts handle(); "
        f"only in JS: {handled - set(_protocol.MESSAGE_TYPES_OUT)}, "
        f"only in _protocol: {set(_protocol.MESSAGE_TYPES_OUT) - handled}")


def test_message_types_in_match_bridge_py_dispatch():
    # Inbound tags (browser -> server) are exactly the kinds Bridge._on_message
    # handles. Read its source so the check tracks the real dispatch, not a
    # hand-kept copy.
    body = inspect.getsource(bridge.Bridge._on_message)
    handled = set(re.findall(r'kind == "(\w+)"', body))
    assert handled == set(_protocol.MESSAGE_TYPES_IN), (
        "MESSAGE_TYPES_IN disagrees with Bridge._on_message; "
        f"only in _on_message: {handled - set(_protocol.MESSAGE_TYPES_IN)}, "
        f"only in _protocol: {set(_protocol.MESSAGE_TYPES_IN) - handled}")


# -- merge control-plane tags match the merge server's dispatch/emit ---------
# The merge channel is separate from the canvas protocol (see _protocol.py), so
# it's guarded here against the merge server's own code: inbound tags must be the
# merge_* kinds MergeBridge._route_from_browser handles, and outbound tags must be
# exactly the merge_* frames the merge server emits. The frontend's consumption of
# the outbound tags is checked below.
def test_merge_message_types_in_match_route_from_browser():
    body = inspect.getsource(merge_mod._MergeHost.route)
    handled = set(re.findall(r'"(merge_\w+)"', body))
    assert handled == set(_protocol.MERGE_MESSAGE_TYPES_IN), (
        "MERGE_MESSAGE_TYPES_IN disagrees with MergeBridge._route_from_browser; "
        f"only in handler: {handled - set(_protocol.MERGE_MESSAGE_TYPES_IN)}, "
        f"only in _protocol: {set(_protocol.MERGE_MESSAGE_TYPES_IN) - handled}")


def test_merge_message_types_out_match_merge_server_emit():
    src = inspect.getsource(merge_mod)
    produced = set(re.findall(r'"type":\s*"(merge_\w+)"', src))
    assert produced == set(_protocol.MERGE_MESSAGE_TYPES_OUT), (
        "MERGE_MESSAGE_TYPES_OUT disagrees with merge.py's emit sites; "
        f"only in merge.py: {produced - set(_protocol.MERGE_MESSAGE_TYPES_OUT)}, "
        f"only in _protocol: {set(_protocol.MERGE_MESSAGE_TYPES_OUT) - produced}")


def test_merge_message_types_out_consumed_by_frontend():
    # The browser side of the merge channel: bridge.ts's handle() switch must
    # dispatch on every outbound merge tag (added with the merge UI). Guards the
    # server->browser direction the same way MESSAGE_TYPES_OUT does for the canvas.
    src = _read(_BRIDGE_TS)
    for tag in _protocol.MERGE_MESSAGE_TYPES_OUT:
        assert f"case '{tag}':" in src, (
            f"bridge.ts handle() never dispatches the merge tag {tag!r}")


# -- iframe postMessage keys match the canonical set on both sides -----------
# There's no structured frame for the Custom-iframe channel — the __danvas_* key
# IS the message type — so a key on one side that the other never names silently
# drops the message. Scan the producer (the Python shim) and the consumers (the
# frontend relay + CustomView) and require each side's key set to equal the
# canonical IFRAME_MESSAGE_KEYS exactly: an extra key (typo / undeclared) or a
# missing one (declared but unused on a side) both fail here.
def test_iframe_keys_shim_side_match_protocol():
    # The in-iframe helper moved from custom.py to the frontend
    # (customShim.ts injects it with the browser-local id); custom.py keeps
    # only the owner-side content-fit script. Together the two producer files
    # must still cover the canonical key set exactly.
    canonical = set(_protocol.IFRAME_MESSAGE_KEYS.values())
    used = set(_IFRAME_KEY_RE.findall(_read(_CUSTOM_SHIM_TS)))
    used |= set(_IFRAME_KEY_RE.findall(_read(_CUSTOM_PY)))
    assert used == canonical, (
        "customShim.ts + custom.py iframe keys disagree with "
        "_protocol.IFRAME_MESSAGE_KEYS; "
        f"only in the shim side: {used - canonical}, "
        f"only in _protocol: {canonical - used}")


def test_iframe_keys_frontend_side_match_protocol():
    canonical = set(_protocol.IFRAME_MESSAGE_KEYS.values())
    used = set(_IFRAME_KEY_RE.findall(_read(_BRIDGE_TS)))
    used |= set(_IFRAME_KEY_RE.findall(_read(_CUSTOM_VIEW_TSX)))
    # the export raster round-trip's parent half lives in engine/export.ts
    used |= set(_IFRAME_KEY_RE.findall(_read(_EXPORT_TS)))
    assert used == canonical, (
        "frontend iframe keys (bridge.ts + CustomView.tsx + export.ts) "
        "disagree with _protocol.IFRAME_MESSAGE_KEYS; only in frontend: "
        f"{used - canonical}, only in _protocol: {canonical - used}")


# -- the generated JS module is not stale ------------------------------------
def test_generated_js_is_up_to_date():
    assert os.path.exists(_GEN_JS), (
        "protocol.generated.js missing — run `python scripts/gen_protocol.py`")
    spec = importlib.util.spec_from_file_location("gen_protocol", _GEN_SCRIPT)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    expected = gen.render()
    actual = _read(_GEN_JS).replace("\r\n", "\n")
    assert actual == expected, (
        "protocol.generated.js is stale — re-run `python scripts/gen_protocol.py`")
