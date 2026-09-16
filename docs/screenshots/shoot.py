"""Regenerate the README screenshots from REAL running canvases.

Each shot starts the canvas script in a subprocess (serve() patched onto a free
port, no browser), opens it in headless Chromium via Playwright, interacts where
the picture depends on it (the hello-world slider is really dragged, the React
ping button really clicked, the leaderboard really filled in as admin), fits the
camera to the content, and saves a PNG next to this file.

    pip install playwright pillow plotly matplotlib numpy && playwright install chromium
    python docs/screenshots/shoot.py            # all shots
    python docs/screenshots/shoot.py hello      # one shot (prefix match)

tests/test_screenshots.py re-shoots the deterministic scenes and pixel-diffs
them against the committed PNGs, so a renderer regression shows up as a
picture, not just a wire-level assertion.
"""

import io
import os
import socket
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
EXAMPLES = os.path.join(ROOT, "examples")

# serve() is patched to a free port with the browser/tunnel/hot-reload off, so
# any example runs unmodified.
_PATCH = """
import runpy, sys, danvas
_orig = danvas.Canvas.serve
def _serve(self, *a, **k):
    k.update(port=int(sys.argv[2]), open_browser=False, host="127.0.0.1",
             tunnel=False, hot_reload=False)
    return _orig(self, **k)
danvas.Canvas.serve = _serve
runpy.run_path(sys.argv[1], run_name="__main__")
"""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port, proc, deadline=30):
    t0 = time.time()
    while time.time() - t0 < deadline:
        if proc.poll() is not None:
            raise RuntimeError("canvas script exited early")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("canvas never opened its port")


# --- one browser page on the canvas ------------------------------------------

def _open(browser, port, viewport, dark, scale, panels=1, settle=2.0):
    """A fresh browser context on the canvas (not yet settled)."""
    ctx = browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]},
        device_scale_factor=scale,
        color_scheme="dark" if dark else "light")
    ctx.add_init_script(
        "try{localStorage.setItem('pc-theme','%s')}catch(e){}"
        % ("dark" if dark else "light"))
    page = ctx.new_page()
    page.goto(f"http://127.0.0.1:{port}/")
    return page


def _settle(page, panels, settle):
    # count panels in the store (off-screen ones are culled from the DOM)
    page.wait_for_function(
        "n => { const s = window.__danvas && window.__danvas.store;"
        " return !!s && [...s.ids()].filter(i =>"
        " (s.peek(i)||{}).typeName === 'panel').length >= n; }",
        arg=panels, timeout=30_000)
    time.sleep(settle)          # plots, images, iframes
    # Plotly sizes itself from a window resize; headless never sends one.
    page.evaluate("() => window.dispatchEvent(new Event('resize'))")
    time.sleep(0.8)


def _frame(page, fit, offset=(0, 0)):
    """Centre the content and zoom it to ~`fit` of the viewport.

    The app's own fit caps at 100%, which leaves a two-panel canvas tiny; a
    README shot wants it big. `offset` nudges the centred content by screen
    px, to keep it clear of the style palette (top right) and the toolbar.
    """
    if not fit:
        return
    page.evaluate("""([fill, zmax, dx, dy]) => {
      const c = window.__danvas && window.__danvas.camera;
      const b = c && c.currentPageBounds && c.currentPageBounds();
      if (!b) return;
      const vw = innerWidth, vh = innerHeight;
      const z = Math.min(zmax, vw * fill / b.w, vh * fill / b.h);
      c.setCamera({ x: (vw / 2 + dx) / z - (b.x + b.w / 2),
                    y: (vh / 2 + dy) / z - (b.y + b.h / 2), z }, { force: true });
    }""", [fit if isinstance(fit, float) else 0.8, 1.6, offset[0], offset[1]])
    time.sleep(0.6)


def _png(page):
    from PIL import Image
    return Image.open(io.BytesIO(page.screenshot())).convert("RGB")


def _side_by_side(imgs, gap=24, bg=(24, 24, 27)):
    from PIL import Image
    w = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
    h = max(i.height for i in imgs)
    out = Image.new("RGB", (w, h), bg)
    x = 0
    for i in imgs:
        out.paste(i, (x, 0))
        x += i.width + gap
    return out


def shoot(name, script, viewport=(1600, 1000), panels=1, settle=2.0,
          interact=None, dark=True, fit=True, offset=(0, 0), scale=2,
          scene=None, out_dir=None):
    """Capture one scene to <out_dir>/<name>.png; returns the path.

    `scene(browser, port, opts) -> PIL.Image` takes over for shots that need
    more than one browser (roles, multi-user); otherwise one page is opened,
    settled, interacted with, framed and screenshotted.
    """
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", _PATCH, script, str(port)],
                            cwd=EXAMPLES)
    out = os.path.join(out_dir or HERE, name + ".png")
    try:
        _wait_port(port, proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            opts = dict(viewport=viewport, dark=dark, scale=scale)
            if scene:
                scene(browser, port, opts).save(out)
            else:
                page = _open(browser, port, **opts)
                _settle(page, panels, settle)
                if interact:
                    interact(page)
                _frame(page, fit, offset)
                page.screenshot(path=out)
            browser.close()
        print("wrote", os.path.relpath(out, ROOT))
        return out
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


# --- interactions ------------------------------------------------------------

def _set_range(page, panel_label, value):
    # A DOM input event on a named panel's range input, through Python's handler.
    page.evaluate("""([label, v]) => {
      const el = [...document.querySelectorAll('[data-pc-panel-id]')]
        .find(p => p.innerText.trim().toUpperCase().startsWith(label.toUpperCase()));
      const r = el && el.querySelector('input[type=range]');
      r.value = v;
      r.dispatchEvent(new Event('input', {bubbles: true}));
      r.dispatchEvent(new Event('change', {bubbles: true}));
    }""", [panel_label, value])


def _drag_hello_slider(page):
    # Really move the slider so the STATUS label updates through the Python
    # handler, not a preset value.
    _set_range(page, "servo_1", 113)
    page.wait_for_function(
        "() => document.body.innerText.includes('servo at 113')", timeout=10_000)
    time.sleep(0.3)


def _drive_signal_flow(page):
    _set_range(page, "input", 60)
    _set_range(page, "gain", 4)
    page.wait_for_function(
        "() => document.body.innerText.includes('60 x 4 = 240')", timeout=10_000)
    time.sleep(0.3)


def _scroll_top(page):
    # scroll_y navigation: put the column at its top (the page's first panel).
    page.evaluate("""() => {
      const c = window.__danvas && window.__danvas.camera;
      if (c && c.centerForScroll) c.centerForScroll(true);
    }""")
    time.sleep(0.5)


def _click_ping(page):
    # the React panel's own button, twice -> Python's @on('ping') -> STATUS
    btn = page.locator("[data-pc-panel-id] button", has_text="ping").first
    btn.click()
    btn.click()
    page.wait_for_function(
        "() => document.body.innerText.includes('ping #2')", timeout=10_000)
    time.sleep(0.3)


def _login(page, password):
    box = page.locator("input[type=password]")
    box.wait_for(timeout=15_000)
    box.fill(password)
    box.press("Enter")


# --- multi-browser scenes ----------------------------------------------------

def roles_scene(browser, port, opts):
    # Admin logs in, adds three teams through the React form; a viewer logs in
    # with the other password and sees the board only. One image, two logins.
    o = dict(opts, viewport=(860, 760))
    admin = _open(browser, port, **o)
    _login(admin, "admin")
    _settle(admin, 2, 1.5)
    for team, pts in (("Otters", 1200), ("Kestrels", 980), ("Moles", 640)):
        admin.locator("[data-pc-panel-id] input[placeholder='Team name']").fill(team)
        admin.locator("[data-pc-panel-id] input[placeholder='Points']").fill(str(pts))
        admin.locator("[data-pc-panel-id] button", has_text="Submit").click()
        admin.wait_for_function(
            "t => document.body.innerText.includes(t)", arg=team, timeout=10_000)
    viewer = _open(browser, port, **o)
    _login(viewer, "view")
    _settle(viewer, 1, 1.5)
    viewer.wait_for_function(
        "() => document.body.innerText.includes('Moles')", timeout=10_000)
    for p in (admin, viewer):
        _frame(p, 0.62, (-70, 10))
    return _side_by_side([_png(admin), _png(viewer)])


def multiuser_scene(browser, port, opts):
    # Two browsers on one canvas: the roster reads "2 viewers" and each sees
    # the other's live cursor. Shot from the first viewer's side.
    o = dict(opts, viewport=(1400, 640))
    a = _open(browser, port, **o)
    _settle(a, 2, 1.0)
    b = _open(browser, port, **o)
    _settle(b, 2, 1.0)
    for p in (a, b):
        _frame(p, 0.55)
    a.wait_for_function(
        "() => document.body.innerText.includes('2 viewers')", timeout=10_000)
    # b moves its mouse over the STATUS panel so a renders b's cursor there
    for i in range(8):
        b.mouse.move(900 + i * 8, 330 + i * 4)
        time.sleep(0.05)
    a.mouse.move(500, 300)
    time.sleep(1.0)
    return _png(a)


# --- the shot list -----------------------------------------------------------

SHOTS = {
    "hello_world": dict(script=os.path.join(HERE, "hello_world.py"),
                        viewport=(1400, 640), panels=2, fit=0.55,
                        interact=_drag_hello_slider),
    "catalogue": dict(script=os.path.join(HERE, "catalogue_grid.py"),
                      viewport=(1600, 1100), panels=17, settle=4.0,
                      fit=0.78, offset=(-110, 30)),
    "custom_react": dict(script=os.path.join(HERE, "custom_react.py"),
                         viewport=(1400, 760), panels=5, settle=3.0,
                         interact=_click_ping, fit=0.72, offset=(-80, 40)),
    "shapes": dict(script=os.path.join(EXAMPLES, "managed_shapes.py"),
                   viewport=(1400, 1000), panels=2, settle=2.5,
                   interact=_scroll_top, fit=False),
    "signal_flow": dict(script=os.path.join(EXAMPLES, "locked_and_arrows.py"),
                        viewport=(1400, 800), panels=5, settle=2.0,
                        interact=_drive_signal_flow, fit=0.6, offset=(-60, 90)),
    "roles": dict(script=os.path.join(EXAMPLES, "leaderboard.py"),
                  scene=roles_scene),
    "multiuser": dict(script=os.path.join(HERE, "hello_world.py"),
                      scene=multiuser_scene),
    "page_layout": dict(script=os.path.join(EXAMPLES, "page_layout.py"),
                        viewport=(1200, 1300), panels=8, settle=2.5,
                        interact=_scroll_top, fit=False),
}

# Scenes with no clock, random data, or live stream in the picture: these are
# the pixel-diff regression baselines (tests/test_screenshots.py).
DETERMINISTIC = ("hello_world", "signal_flow", "page_layout")

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(SHOTS)
    for n in wanted:
        key = next(k for k in SHOTS if k.startswith(n))
        shoot(key, **SHOTS[key])
