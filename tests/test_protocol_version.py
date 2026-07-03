"""The frozen protocol version: declared once, advertised in welcome, documented.

PROTOCOL.md is the human-readable freeze of the wire contract; _protocol.py is
the machine-readable half. These tests pin the two together and check the
version actually reaches a connecting client, so a bump (or a forgotten doc
update) fails loudly.
"""

import os
import re

from danvas import _protocol
from danvas.bridge import Bridge

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_version_is_a_positive_int_and_exported():
    assert isinstance(_protocol.PROTOCOL_VERSION, int)
    assert _protocol.PROTOCOL_VERSION >= 1
    assert _protocol.as_dict()["protocol_version"] == _protocol.PROTOCOL_VERSION


def test_protocol_md_documents_the_current_version():
    path = os.path.join(_ROOT, "PROTOCOL.md")
    assert os.path.exists(path), "PROTOCOL.md (the frozen spec) is missing"
    text = open(path, encoding="utf-8").read()
    # The title carries the version, and the body states it explicitly.
    assert f"(v{_protocol.PROTOCOL_VERSION})" in text
    assert f"The current version is **{_protocol.PROTOCOL_VERSION}**" in text


def test_generated_js_carries_the_version():
    path = os.path.join(_ROOT, "danvas", "frontend", "src",
                        "protocol.generated.js")
    text = open(path, encoding="utf-8").read()
    m = re.search(r"export const PROTOCOL_VERSION = (\d+)", text)
    assert m and int(m.group(1)) == _protocol.PROTOCOL_VERSION


def test_welcome_frame_advertises_the_version():
    # The welcome frame is built inline in Bridge.handle_connection; rather than
    # spinning a real socket, pin the source: the frame must carry "protocol"
    # sourced from PROTOCOL_VERSION (an SDK's version check reads this field).
    import inspect
    src = inspect.getsource(Bridge.handle_connection)
    assert '"protocol": PROTOCOL_VERSION' in src
