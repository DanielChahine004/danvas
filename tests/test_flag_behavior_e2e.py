"""End-to-end contract tests for the panel lock/chrome flags.

These are the regression net for the class of bug that kept recurring: a Python
flag (``grabbable``/``resizable``/``decorative``…) that the *frontend* quietly
didn't honour, so the documented behaviour silently lied and could only be fixed
by editing the built frontend. A pure-Python unit test can't catch that — it
needs a real browser driving the real ``dist`` — so this module boots a canvas,
opens it in headless Chromium, and asserts the actual on-canvas behaviour.

Skipped automatically when Playwright (or its Chromium build) isn't installed, so
it never breaks a bare ``pip install`` environment; run ``python -m playwright
install chromium`` once to enable it.
"""

import socket
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

import danvas  # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# Shared mutable state the panels' handlers write to, so the test process can
# assert what fired (it IS the server process — serve runs in a thread here).
_slider_hits = []


@pytest.fixture(scope="module")
def canvas_url():
    """Boot a canvas exercising each flag, served in-process; yield its URL."""
    canvas = danvas.Canvas()
    # Locator panels are Labels (rendered inline, so their text is in the parent
    # DOM and findable by the browser — a Custom panel's text lives in its iframe).
    # A normal, resizable panel — marquee-selectable, has resize handles.
    canvas.label("normal", value="NORMALPANEL", x=200, y=140, w=200)
    # grabbable=False — invisible to selection (click AND marquee).
    canvas.label("nograb", value="NOGRABPANEL", x=600, y=140, w=200,
                 grabbable=False)
    # resizable=False — selectable but no resize handles.
    canvas.label("pinned", value="PINNEDPANEL", x=600, y=420, w=200,
                 resizable=False)
    # A slider with a decorative overlay floating directly on top of it: the
    # overlay must be click-through so the slider still drives its handler.
    slider = canvas.slider("speed", min=0, max=100, default=10,
                           x=200, y=440, w=220, h=90)

    @slider.on_change
    def _(v):
        _slider_hits.append(v)

    canvas.custom(name="overlay", html="<div>OVERLAYGLYPH</div>",
                  x=200, y=430, w=220, h=110, decorative=True)

    port = _free_port()
    canvas.serve(port=port, open_browser=False, hot_reload=False, block=False, broker=False)
    time.sleep(1.5)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        canvas.stop()


# -- helpers run in the browser --------------------------------------------

_RECT_JS = """(text) => {
  const cards = Array.from(document.querySelectorAll('.pc-card'));
  const el = cards.find(c => (c.textContent || '').includes(text));
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
}"""
_SELBOX_JS = "() => document.querySelectorAll('[data-pc-selbox]').length"
_HANDLE_JS = "() => document.querySelectorAll('[data-pc-handle]').length"


def _marquee_around(pg, rect, pad=24):
    """Drag a left-button marquee that fully encloses ``rect``."""
    pg.mouse.move(rect["x"] + rect["w"] + pad, rect["y"] + rect["h"] + pad)
    pg.mouse.down()
    pg.mouse.move(rect["x"] - pad, rect["y"] - pad, steps=8)
    pg.mouse.up()
    pg.wait_for_timeout(300)


def _clear(pg):
    # Click empty space far from any panel to drop the selection.
    pg.mouse.click(40, 650)
    pg.wait_for_timeout(150)


@pytest.fixture(scope="module")
def page(canvas_url):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:                       # no browser binary
            pytest.skip(f"Chromium not available: {exc}")
        pg = browser.new_page(viewport={"width": 1280, "height": 760})
        pg.goto(canvas_url)
        pg.wait_for_timeout(2500)                      # connect + initial fit
        yield pg
        browser.close()


# -- the contract assertions -------------------------------------------------

def test_normal_panel_is_marquee_selectable_and_resizable(page):
    _clear(page)
    rect = page.evaluate(_RECT_JS, "NORMALPANEL")
    assert rect, "normal panel not found on canvas"
    _marquee_around(page, rect)
    assert page.evaluate(_SELBOX_JS) == 1              # selected
    assert page.evaluate(_HANDLE_JS) > 0               # has resize handles


def test_grabbable_false_panel_is_invisible_to_marquee(page):
    _clear(page)
    rect = page.evaluate(_RECT_JS, "NOGRABPANEL")
    assert rect, "nograb panel not found on canvas"
    _marquee_around(page, rect)
    assert page.evaluate(_SELBOX_JS) == 0              # never selected


def test_resizable_false_panel_selects_without_handles(page):
    _clear(page)
    rect = page.evaluate(_RECT_JS, "PINNEDPANEL")
    assert rect, "pinned panel not found on canvas"
    _marquee_around(page, rect)
    assert page.evaluate(_SELBOX_JS) == 1              # still selectable
    assert page.evaluate(_HANDLE_JS) == 0              # but no resize handles


def test_decorative_overlay_is_click_through_to_panel_beneath(page):
    _clear(page)
    _slider_hits.clear()
    # The slider sits UNDER the decorative overlay. Drag on the slider's track;
    # if the overlay weren't click-through the gesture would be swallowed.
    box = page.evaluate("""() => {
      const inp = document.querySelector('input[type=range]');
      if (!inp) return null;
      const r = inp.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    }""")
    assert box, "slider input not found"
    tx = box["x"] + box["w"] * 0.85
    ty = box["y"] + box["h"] / 2
    page.mouse.move(tx, ty)
    page.mouse.down()
    page.mouse.move(tx + 6, ty, steps=3)
    page.mouse.up()
    page.wait_for_timeout(500)
    assert _slider_hits, "drag did not reach the slider through the decorative overlay"
