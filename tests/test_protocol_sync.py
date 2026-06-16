"""Guard the Python <-> browser wire protocol against drift.

The canonical contract lives in :mod:`pycanvas._protocol`. Until the integration
step makes every side import from it, the constants are still restated in
``bridge.py`` (binary codes), ``_flags.py`` (lock wire keys), and ``bridge.js``
(both). These tests assert all of those agree with the canonical module — so a
change on any one side without the others fails loudly — and that the committed
generated JS module is not stale.

The binary codes and lock wire keys are the high-value targets: a mismatch there
fails *silently* in production (a frame routed to the wrong handler, a corrupted
lock meta), which is exactly what a test should catch instead of a user.
"""

import importlib.util
import os
import re

import pycanvas.bridge as bridge
from pycanvas import _protocol
from pycanvas._flags import LAYOUT_FLAGS

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRIDGE_JS = os.path.join(_ROOT, "pycanvas", "frontend", "src", "bridge.js")
_GEN_JS = os.path.join(_ROOT, "pycanvas", "frontend", "src",
                       "protocol.generated.js")
_GEN_SCRIPT = os.path.join(_ROOT, "scripts", "gen_protocol.py")


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
def test_bridge_js_binary_codes_match_protocol():
    src = _read(_BRIDGE_JS)
    found = {m.group(1): int(m.group(2))
             for m in re.finditer(r"const BIN_(\w+)\s*=\s*(\d+)", src)}
    for name, code in _protocol.BINARY_FRAME_CODES.items():
        assert found.get(name) == code, (
            f"bridge.js BIN_{name} ({found.get(name)}) != protocol {code}")


def test_bridge_js_uses_the_flag_wire_keys():
    # The registerComponent destructure is where the frontend names the lock
    # wire keys; every canonical wire key must appear there, so a rename on
    # either side trips this.
    src = _read(_BRIDGE_JS)
    # Span to the destructure's closing `})` — non-greedy so the inner `{}` of
    # `props = {}` doesn't end the match early.
    m = re.search(r"function registerComponent\(\{(.*?)\}\s*\)", src, re.DOTALL)
    assert m, "couldn't find registerComponent destructure in bridge.js"
    params = {p.strip().split("=")[0].strip() for p in m.group(1).split(",")}
    for wire in _protocol.FLAG_WIRE_KEYS.values():
        assert wire in params, (
            f"bridge.js registerComponent is missing wire key {wire!r}")


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
