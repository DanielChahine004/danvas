"""HTTP inspection endpoints — external/terminal QC of a live canvas.

/__describe__ is pure Python state, so it answers with no browser connected
(the headless QC path); /__screenshot__.png needs a rendering browser and 503s
without one. Both sit behind the auth gate.
"""

from fastapi.testclient import TestClient

import danvas
from danvas import server


def _app(canvas, **kw):
    return server.create_app(canvas._bridge, open_browser=False, **kw)


def test_describe_endpoint_works_headless():
    canvas = danvas.Canvas()
    canvas.slider("servo", min=0, max=180, default=90)
    canvas.label("status", "idle")

    client = TestClient(_app(canvas))
    resp = client.get("/__describe__")
    assert resp.status_code == 200
    by_name = {r["name"]: r for r in resp.json()}
    assert by_name["servo"]["type"] == "Slider"
    assert by_name["servo"]["value"] == "90"


def test_screenshot_endpoint_503_without_browser():
    canvas = danvas.Canvas()
    canvas.slider("s", min=0, max=10)
    client = TestClient(_app(canvas))
    # No browser connected → can't render → 503, not a hang or 500.
    assert client.get("/__screenshot__.png").status_code == 503


def test_endpoints_behind_auth_gate():
    canvas = danvas.Canvas()
    canvas.label("secret", "hunter2")
    client = TestClient(_app(canvas, password="pw"))
    # Unauthenticated request is bounced by the gate, not served the values.
    assert client.get("/__describe__").status_code == 401


def test_tldraw_license_key_injected_into_index():
    canvas = danvas.Canvas()
    canvas._bridge._tldraw_license_key = "tldraw-test-123"
    html = TestClient(_app(canvas)).get("/").text
    assert "__DANVAS_TLDRAW_LICENSE_KEY__" in html
    assert "tldraw-test-123" in html


def test_index_unchanged_without_license_key():
    canvas = danvas.Canvas()  # no key → development mode, page served as-is
    html = TestClient(_app(canvas)).get("/").text
    assert "__DANVAS_TLDRAW_LICENSE_KEY__" not in html
