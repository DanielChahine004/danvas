"""canvas.define / canvas.style — shared React components and global CSS.

These register assets on the bridge that are replayed to every browser on connect
and broadcast live; the tests cover the Python-side state and the wire frame
(the in-browser compile/inject is exercised by the frontend, not here).
"""

import pytest

import danvas


def _canvas():
    return danvas.Canvas()


_PILL = ("function StatusPill({ kind, children }) "
         "{ return <span className={'pill ' + kind}>{children}</span> }")


def test_define_registers_component_source():
    c = _canvas()
    ret = c.define("StatusPill", _PILL)
    assert ret is c  # chainable
    assert c._bridge._shared_components["StatusPill"] == _PILL
    msg = c._bridge.shared_message()
    assert msg["type"] == "shared"
    assert msg["components"]["StatusPill"] == _PILL


def test_define_rejects_bad_identifier():
    c = _canvas()
    with pytest.raises(ValueError):
        c.define("not a name", _PILL)
    with pytest.raises(ValueError):
        c.define("123start", _PILL)


def test_define_requires_source():
    c = _canvas()
    with pytest.raises(ValueError):
        c.define("Empty")
    with pytest.raises(ValueError):
        c.define("Blank", "   ")


def test_define_reads_from_path(tmp_path):
    f = tmp_path / "pill.jsx"
    f.write_text(_PILL, encoding="utf-8")
    c = _canvas()
    c.define("StatusPill", path=str(f))
    assert c._bridge._shared_components["StatusPill"] == _PILL


def test_define_redefine_replaces():
    c = _canvas()
    c.define("Box", "function Box(){ return <div/> }")
    c.define("Box", "function Box(){ return <section/> }")
    assert c._bridge._shared_components["Box"] == \
        "function Box(){ return <section/> }"


def test_style_accumulates():
    c = _canvas()
    ret = c.style(".pill { padding: 2px }")
    assert ret is c
    c.style(".pill.ok { color: green }")
    styles = c._bridge._shared_styles
    assert ".pill { padding: 2px }" in styles
    assert ".pill.ok { color: green }" in styles
    # The shared frame carries the combined stylesheet.
    assert c._bridge.shared_message()["styles"] == styles


def test_shared_message_empty_by_default():
    c = _canvas()
    msg = c._bridge.shared_message()
    assert msg["components"] == {} and msg["styles"] == ""
