"""Pin the serve() exposure-gating truth table.

``Canvas._resolve_exposure`` is the pure function extracted from ``serve()``: it
decides whether a bind is publicly reachable and whether the UI Inspector /
cursor reporting default on. These were the gating decisions previously inlined
in serve() and impossible to test without standing up a server. The defaults
matter for safety (telemetry/Inspector exposed to viewers), so lock them down.
"""

import danvas


def _res(host="127.0.0.1", tunnel=False, ui_inspector=None, cursors=None):
    return danvas.Canvas()._resolve_exposure(host, tunnel, ui_inspector,
                                               None, cursors)


def test_private_local_bind_enables_telemetry_by_default():
    e = _res("127.0.0.1", tunnel=False)
    assert e.public_bind is False
    assert e.ui_inspector is True
    assert e.cursors is True


def test_localhost_counts_as_local():
    e = _res("localhost")
    assert e.public_bind is False
    assert e.ui_inspector is True and e.cursors is True


def test_lan_bind_is_public_and_telemetry_off_by_default():
    e = _res("0.0.0.0", tunnel=False)
    assert e.public_bind is True
    assert e.ui_inspector is False
    assert e.cursors is False


def test_tunnel_is_public_even_on_loopback():
    e = _res("127.0.0.1", tunnel=True)
    assert e.public_bind is True
    assert e.ui_inspector is False
    assert e.cursors is False


def test_explicit_flags_override_the_defaults():
    # Force telemetry on for a public bind...
    e = _res("0.0.0.0", ui_inspector=True, cursors=True)
    assert e.ui_inspector is True and e.cursors is True
    # ...and off for a private one.
    e2 = _res("127.0.0.1", ui_inspector=False, cursors=False)
    assert e2.ui_inspector is False and e2.cursors is False
