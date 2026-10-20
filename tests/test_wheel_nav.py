"""Wheel navigation in a real browser: a trackpad two-finger swipe pans (any
direction), a pinch (ctrl+wheel) and a notched mouse wheel zoom, wheel events
forwarded out of a Custom iframe follow the same rule, and the Settings
override (localStorage pc_wheel) forces zoom or pan.

Synthetic events come from CDP Input.dispatchMouseEvent: small whole-number
deltas stand in for a trackpad, 100 px steps for a notched wheel.
"""

import pytest

from test_frontend_smoke import canvas_url, playwright_sync  # noqa: F401  (fixture)


@pytest.fixture(scope="module")
def page(canvas_url):  # noqa: F811
    url, _src = canvas_url
    with playwright_sync.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # browsers not installed
            pytest.skip(f"chromium unavailable: {exc}")
        pg = browser.new_page(viewport={"width": 1400, "height": 900})
        pg.goto(url)
        pg.wait_for_selector("[data-pc-panel-id]", timeout=15000)
        pg.wait_for_timeout(800)
        pg.cdp = pg.context.new_cdp_session(pg)
        yield pg
        browser.close()


def _gesture(pg, events, at=(1300, 850), cam=(0, 0)):
    pg.evaluate(f"window.__danvas.camera.setCamera({{x: {cam[0]}, y: {cam[1]}, z: 1}}, {{force: true}})")
    pg.wait_for_timeout(500)  # > the 400 ms gesture gap: a fresh gesture
    before = pg.evaluate("window.__danvas.store.camera()")
    for ev in events:
        dx, dy, mods = (list(ev) + [0])[:3]
        pg.cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": at[0], "y": at[1],
            "deltaX": dx, "deltaY": dy, "modifiers": mods})
        pg.wait_for_timeout(25)
    pg.wait_for_timeout(150)
    after = pg.evaluate("window.__danvas.store.camera()")
    if abs(after["z"] - before["z"]) > 1e-6:
        return "zoom"
    if abs(after["x"] - before["x"]) > 0.5 or abs(after["y"] - before["y"]) > 0.5:
        return "pan"
    return "none"


def test_trackpad_swipes_pan_in_every_direction(page):
    assert _gesture(page, [(0, d) for d in (3, 7, 12, 9)]) == "pan"
    assert _gesture(page, [(d, 0) for d in (5, 11, 8)]) == "pan"
    assert _gesture(page, [(6, 6)] * 4) == "pan"


def test_swipe_moves_the_view_the_way_a_page_scrolls(page):
    page.evaluate("window.__danvas.camera.setCamera({x: 0, y: 0, z: 1}, {force: true})")
    page.wait_for_timeout(500)
    for d in (4, 8, 8):
        page.cdp.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 1300, "y": 850,
                                                   "deltaX": 0, "deltaY": d})
        page.wait_for_timeout(25)
    page.wait_for_timeout(150)
    cam = page.evaluate("window.__danvas.store.camera()")
    assert cam["y"] < 0, cam  # fingers up (positive deltaY) = content moves up


def test_mouse_wheel_and_pinch_zoom(page):
    assert _gesture(page, [(0, 100)]) == "zoom"
    assert _gesture(page, [(0, 4, 2)] * 3) == "zoom"  # modifiers 2 = ctrl (pinch)


def test_custom_iframe_forwards_with_the_same_rule(page):
    # bring the Custom panel on screen (it's far down the smoke board)
    rec = page.evaluate("""() => { const s = window.__danvas.store;
        const r = s.getIds().map(i => s.peek(i)).find(r => r && r.typeName === 'panel' && r.id.endsWith(':custom'));
        return {x: r.x, y: r.y} }""")
    cam = (100 - rec["x"], 100 - rec["y"])  # panel's top-left at screen (100, 100)
    page.evaluate(f"window.__danvas.camera.setCamera({{x: {cam[0]}, y: {cam[1]}, z: 1}}, {{force: true}})")
    box = page.locator("iframe").first.bounding_box(timeout=10000)
    at = (box["x"] + 30, box["y"] + 30)
    assert _gesture(page, [(0, d) for d in (3, 7, 12)], at=at, cam=cam) == "pan"
    assert _gesture(page, [(0, 100)], at=at, cam=cam) == "zoom"


def test_settings_override_forces_a_mode(page):
    try:
        page.evaluate("localStorage.setItem('pc_wheel', 'pan')")
        page.reload()
        page.wait_for_selector("[data-pc-panel-id]", timeout=15000)
        page.wait_for_timeout(600)
        assert _gesture(page, [(0, 100)]) == "pan"
        assert _gesture(page, [(0, 4, 2)]) == "zoom"  # pinch still zooms
        page.evaluate("localStorage.setItem('pc_wheel', 'zoom')")
        page.reload()
        page.wait_for_selector("[data-pc-panel-id]", timeout=15000)
        page.wait_for_timeout(600)
        assert _gesture(page, [(4, 6)] * 3) == "zoom"
    finally:
        page.evaluate("localStorage.removeItem('pc_wheel')")
