"""Regenerate the README screenshots from REAL running canvases.

Each shot starts the canvas script in a subprocess (serve() patched onto a free
port, no browser), opens it in headless Chromium via Playwright, optionally
interacts (the hello-world slider is really dragged), fits the camera to the
content, and saves a PNG next to this file.

    pip install playwright plotly matplotlib numpy && playwright install chromium
    python docs/screenshots/shoot.py            # all shots
    python docs/screenshots/shoot.py hello      # one shot
"""

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


def shoot(name, script, viewport=(1600, 1000), panels=1, settle=2.0,
          interact=None, dark=True, fit=True, offset=(0, 0), scale=2):
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", _PATCH, script, str(port)],
                            cwd=EXAMPLES)
    try:
        _wait_port(port, proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]},
                device_scale_factor=scale,
                color_scheme="dark" if dark else "light")
            ctx.add_init_script(
                "try{localStorage.setItem('pc-theme','%s')}catch(e){}"
                % ("dark" if dark else "light"))
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
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
            if interact:
                interact(page)
            if fit:
                # Centre the content and zoom it to ~`fill` of the viewport
                # (the app's own fit caps at 100%, which leaves a two-panel
                # canvas tiny; a README shot wants it big).
                # `offset` nudges the centred content by screen px, to keep
                # it clear of the style palette (top right) and the toolbar.
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
            out = os.path.join(HERE, name + ".png")
            page.screenshot(path=out)
            print("wrote", os.path.relpath(out, ROOT))
            browser.close()
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _drag_hello_slider(page):
    # Really move the slider (a DOM input event on the range input) so the
    # STATUS label updates through the Python handler, not a preset value.
    rng = page.locator("[data-pc-panel-id] input[type=range]").first
    rng.evaluate("e => { e.value = 113;"
                 " e.dispatchEvent(new Event('input', {bubbles: true}));"
                 " e.dispatchEvent(new Event('change', {bubbles: true})); }")
    page.wait_for_function(
        "() => document.body.innerText.includes('servo at 113')",
        timeout=10_000)
    time.sleep(0.3)


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


SHOTS = {
    "hello_world": dict(script=os.path.join(HERE, "hello_world.py"),
                        viewport=(1400, 640), panels=2, fit=0.55,
                        interact=_drag_hello_slider),
    "catalogue": dict(script=os.path.join(HERE, "catalogue_grid.py"),
                      viewport=(1600, 1100), panels=17, settle=4.0,
                      fit=0.78, offset=(-110, 30)),
    "signal_flow": dict(script=os.path.join(EXAMPLES, "locked_and_arrows.py"),
                        viewport=(1400, 800), panels=5, settle=2.0,
                        interact=_drive_signal_flow, fit=0.6, offset=(-60, 90)),
    "page_layout": dict(script=os.path.join(EXAMPLES, "page_layout.py"),
                        viewport=(1200, 1300), panels=8, settle=2.5,
                        interact=_scroll_top, fit=False),
}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(SHOTS)
    for n in wanted:
        key = next(k for k in SHOTS if k.startswith(n))
        shoot(key, **SHOTS[key])
