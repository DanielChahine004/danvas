"""Animated README clips, recorded from REAL canvases (see shoot.py).

A clip is a sequence of screenshots taken while the runner drives the page
(a slider swept, a panel dragged, a button clicked, a stream ticking),
assembled into a palette-optimised GIF. GIF is the one animated format both
GitHub and PyPI render in a README.

    pip install playwright pillow plotly matplotlib numpy && playwright install chromium
    python docs/screenshots/gifs.py            # all clips
    python docs/screenshots/gifs.py hello      # one clip (prefix match)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shoot  # noqa: E402
from shoot import HERE, EXAMPLES, _open, _settle, _frame, _set_range, _png, _side_by_side  # noqa: E402

FPS = 10
WIDTH = 880          # output width (px); frames are captured at scale 1 and resized


class Recorder:
    """Collects frames; `hold(n)` repeats the last one (a pause costs no capture)."""

    def __init__(self):
        self.frames = []

    def add(self, img):
        self.frames.append(img)

    def hold(self, n):
        if self.frames:
            self.frames.extend([self.frames[-1]] * n)


def _save_gif(frames, path, fps=FPS, width=WIDTH):
    from PIL import Image
    frames = [f.resize((width, round(f.height * width / f.width)), Image.LANCZOS)
              for f in frames]
    # One palette for the whole clip (per-frame palettes flicker), built from a
    # montage of sampled frames so colours that only appear later still fit.
    idx = sorted({0, len(frames) // 3, 2 * len(frames) // 3, len(frames) - 1})
    sample = frames[:1] if len(frames) < 4 else [frames[i] for i in idx]
    w, h = sample[0].size
    montage = Image.new("RGB", (w, h * len(sample)))
    for i, f in enumerate(sample):
        montage.paste(f, (0, i * h))
    palette = montage.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    q[0].save(path, save_all=True, append_images=q[1:], duration=round(1000 / fps),
              loop=0, optimize=True, disposal=1)


def record(name, script, clip, viewport=(1000, 560), dark=True, out_dir=None,
           width=WIDTH, args=None, channel=None, headless=True):
    """Run `clip(browser, port, opts, rec)` against a live canvas; write name.gif.

    `headless=False` opens a real window (not needed by any current clip;
    headless Chromium renders the 3D viewer's WebGL fine)."""
    port = shoot._free_port()
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", shoot._PATCH, script, str(port)],
                            cwd=EXAMPLES)
    out = os.path.join(out_dir or HERE, name + ".gif")
    try:
        shoot._wait_port(port, proc)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=args or [], channel=channel, headless=headless)
            rec = Recorder()
            clip(browser, port, dict(viewport=viewport, dark=dark, scale=1), rec)
            browser.close()
        _save_gif(rec.frames, out, width=width)
        print("wrote", os.path.relpath(out, shoot.ROOT),
              f"({len(rec.frames)} frames, {os.path.getsize(out) // 1024} KB)")
        return out
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


# --- helpers -----------------------------------------------------------------

def _content_clip(page, margin=(40, 40, 40, 40), top0=False):
    """Screen rect (px) of the canvas content plus (top, right, bottom, left)
    margins, clamped to the viewport — so a clip is tight on the panels, not
    the chrome. `top0` extends the clip to the top edge (keeps the roster pill)."""
    r = page.evaluate("""([mt, mr, mb, ml, top0]) => {
      const c = window.__danvas.camera, b = c.currentPageBounds();
      const cam = window.__danvas.store.camera();
      const x = (b.x + cam.x) * cam.z, y = (b.y + cam.y) * cam.z;
      const w = b.w * cam.z, h = b.h * cam.z;
      const x0 = Math.max(0, x - ml), y0 = top0 ? 0 : Math.max(0, y - mt);
      const x1 = Math.min(innerWidth, x + w + mr), y1 = Math.min(innerHeight, y + h + mb);
      return {x: x0, y: y0, width: x1 - x0, height: y1 - y0};
    }""", [*margin, top0])
    return r


def _shot(page, margin=(40, 40, 40, 40), top0=False):
    import io
    from PIL import Image
    clip = _content_clip(page, margin, top0)
    return Image.open(io.BytesIO(page.screenshot(clip=clip))).convert("RGB")


def _sweep(page, rec, label, values, wait_text=None, shot=None):
    """Set a slider through `values`, one frame each (through Python's handler)."""
    shot = shot or _shot
    for v in values:
        _set_range(page, label, v)
        if wait_text:
            page.wait_for_function(
                "t => document.body.innerText.includes(t)", arg=wait_text(v), timeout=5_000)
        else:
            time.sleep(0.04)
        rec.add(shot(page))


def _panel_box(page, label):
    return page.evaluate("""(label) => {
      const el = [...document.querySelectorAll('[data-pc-panel-id]')]
        .find(p => p.innerText.trim().toUpperCase().startsWith(label.toUpperCase()));
      const r = el.getBoundingClientRect();
      return {x: r.x, y: r.y, w: r.width, h: r.height};
    }""", label)


def _drag_panel(page, rec, label, dx, dy, steps=12, shot=None):
    """Drag a panel by its title area, one frame per step (arrows reroute live)."""
    shot = shot or _shot
    b = _panel_box(page, label)
    x0, y0 = b["x"] + 40, b["y"] + 14          # the label row, clear of controls
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + dx * i / steps, y0 + dy * i / steps)
        time.sleep(0.03)
        rec.add(shot(page))
    page.mouse.up()


# --- clips -------------------------------------------------------------------

def hello_clip(browser, port, opts, rec):
    page = _open(browser, port, **opts)
    _settle(page, 2, 1.0)
    _frame(page, 0.62, (-110, 0))       # clear of the style palette (top right)
    rec.add(_shot(page))
    rec.hold(6)
    _sweep(page, rec, "servo_1", list(range(92, 150, 3)),
           wait_text=lambda v: f"servo at {v}")
    rec.hold(4)
    _sweep(page, rec, "servo_1", list(range(147, 112, -3)) + [113],
           wait_text=lambda v: f"servo at {v}")
    rec.hold(12)


def multiuser_clip(browser, port, opts, rec):
    # Two browsers side by side. The right one drags; the left one follows,
    # and shows the right one's cursor. Then the roles swap.
    o = dict(opts, viewport=(900, 420))
    a = _open(browser, port, **o)
    _settle(a, 2, 1.0)
    b = _open(browser, port, **o)
    _settle(b, 2, 1.0)
    for p in (a, b):
        _frame(p, 0.5, (10, -30))       # between the bottom-left buttons and the palette
    a.wait_for_function("() => document.body.innerText.includes('2 viewers')", timeout=10_000)

    def both():
        m = (0, 30, 30, 30)
        return _side_by_side([_shot(a, m, top0=True), _shot(b, m, top0=True)], gap=16)

    a.mouse.move(120, 420)
    b.mouse.move(120, 420)
    rec.add(both())
    rec.hold(6)
    # b walks its pointer onto its slider (a renders the cursor), then sweeps
    box = _panel_box(b, "servo_1")
    for i in range(1, 9):
        b.mouse.move(120 + (box["x"] + 80 - 120) * i / 8, 420 + (box["y"] + box["h"] / 2 - 420) * i / 8)
        time.sleep(0.03)
        rec.add(both())
    for v in range(93, 160, 4):
        _set_range(b, "servo_1", v)
        a.wait_for_function("t => document.body.innerText.includes(t)",
                            arg=f"servo at {v}", timeout=5_000)
        rec.add(both())
    rec.hold(6)
    box = _panel_box(a, "servo_1")
    for i in range(1, 7):
        a.mouse.move(120 + (box["x"] + 200 - 120) * i / 6, 420 + (box["y"] + box["h"] / 2 - 420) * i / 6)
        time.sleep(0.03)
        rec.add(both())
    for v in range(153, 60, -6):
        _set_range(a, "servo_1", v)
        b.wait_for_function("t => document.body.innerText.includes(t)",
                            arg=f"servo at {v}", timeout=5_000)
        rec.add(both())
    rec.hold(12)


def live_plot_clip(browser, port, opts, rec):
    page = _open(browser, port, **dict(opts, viewport=(1100, 560)))
    _settle(page, 2, 2.5)
    _frame(page, 0.58, (-30, -50))
    t0 = time.time()
    while time.time() - t0 < 5.0:
        rec.add(_shot(page, (30, 30, 30, 30)))
        time.sleep(max(0.0, 1 / FPS - 0.06))


def arrows_clip(browser, port, opts, rec):
    # Signal flow: push a value down the arrows, unlock the stages, drag GAIN
    # (its arrows follow), lock again.
    page = _open(browser, port, **dict(opts, viewport=(1200, 640)))
    _settle(page, 5, 1.5)
    _frame(page, 0.55, (30, 50))
    # the "result" arc bends well above the panels: generous top margin
    shot = lambda p: _shot(p, (150, 30, 30, 30))
    rec.add(shot(page))
    rec.hold(5)
    _sweep(page, rec, "input", list(range(24, 64, 4)),
           wait_text=lambda v: f"{v} x 2 = {v * 2}", shot=shot)
    rec.hold(5)
    page.locator("[data-pc-panel-id] button", has_text="unlocked").first.click()
    time.sleep(0.4)
    rec.add(shot(page))
    rec.hold(3)
    _drag_panel(page, rec, "gain", 120, 95, steps=14, shot=shot)
    page.mouse.click(20, 120)                 # blank canvas: drop the selection
    time.sleep(0.3)
    rec.add(shot(page))
    rec.hold(4)
    page.locator("[data-pc-panel-id] button", has_text="locked").first.click()
    time.sleep(0.4)
    rec.add(shot(page))
    rec.hold(12)


def custom_react_clip(browser, port, opts, rec):
    page = _open(browser, port, **dict(opts, viewport=(1000, 820)))
    _settle(page, 5, 2.5)
    _frame(page, 0.66, (-110, -70))
    btn = page.locator("[data-pc-panel-id] button", has_text="ping").first
    t0 = time.time()
    clicks = 0
    while time.time() - t0 < 5.0:
        if clicks < 3 and time.time() - t0 > 1.2 + clicks * 1.3:
            btn.click()
            clicks += 1
            page.wait_for_function("t => document.body.innerText.includes(t)",
                                   arg=f"ping #{clicks}", timeout=5_000)
        rec.add(_shot(page, (30, 30, 6, 30)))
        time.sleep(max(0.0, 1 / FPS - 0.06))


CLIPS = {
    "hello_world": dict(script=os.path.join(HERE, "hello_world.py"), clip=hello_clip),
    "multiuser": dict(script=os.path.join(HERE, "hello_world.py"), clip=multiuser_clip),
    "live_plot": dict(script=os.path.join(HERE, "live_plot.py"), clip=live_plot_clip),
    "arrows": dict(script=os.path.join(EXAMPLES, "locked_and_arrows.py"), clip=arrows_clip),
    "custom_react": dict(script=os.path.join(HERE, "custom_react.py"), clip=custom_react_clip),
}

# --- the two bigger builds ---------------------------------------------------
# Heavier deps: cad needs build123d + trimesh + scipy; circuit needs LTspice
# installed plus the ltspice + schemdraw packages. Both are real runs — the
# CAD part is rebuilt and the circuit re-simulated on every slider change.

def _capture_for(page, rec, seconds, shot, every=0.12):
    t0 = time.time()
    while time.time() - t0 < seconds:
        rec.add(shot(page))
        time.sleep(every)


def cad_clip(browser, port, opts, rec):
    page = _open(browser, port, **dict(opts, viewport=(1500, 900)))
    _settle(page, 4, 7.0)                       # build123d + the WebGL viewer
    _frame(page, 0.8, (-60, -90))
    shot = lambda p: _shot(p, (24, 24, 8, 24))
    rec.add(shot(page))
    rec.hold(8)
    for sides in (4, 5, 8, 24):                 # each change rebuilds the part
        _set_range(page, "SIDES", sides)
        _capture_for(page, rec, 2.2, shot)
    _set_range(page, "HOLE_RADIUS", 14)
    _capture_for(page, rec, 2.2, shot)
    page.locator("[data-pc-panel-id] button", has_text="trace").first.click()
    _capture_for(page, rec, 3.6, shot)          # the helix draws itself in
    rec.hold(10)


_TEXT_HAS = "t => document.body.innerText.includes(t)"


def _release_range(page, panel_label, value):
    """Set a slider and let go: an on_release=True slider reports on pointerup."""
    _set_range(page, panel_label, value)
    page.evaluate("""(label) => {
      const el = [...document.querySelectorAll('[data-pc-panel-id]')]
        .find(p => p.innerText.trim().toUpperCase().startsWith(label.toUpperCase()));
      el.querySelector('input[type=range]')
        .dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
    }""", panel_label)


def circuit_clip(browser, port, opts, rec):
    page = _open(browser, port, **dict(opts, viewport=(1700, 1120)))
    _settle(page, 10, 3.0)
    # frame FIRST: an off-screen panel is culled from the DOM, and the status
    # label is what the waits below read
    _frame(page, 0.78, (-40, -60))
    time.sleep(1.0)                              # panels mount after the fit
    page.wait_for_function(_TEXT_HAS, arg="Idle", timeout=60_000)
    shot = lambda p: _shot(p, (16, 16, 16, 16))
    rec.add(shot(page))
    rec.hold(8)
    for label, value in (("Capacitor C", 60), ("Ripple freq", 40), ("Capacitor C", 16)):
        _release_range(page, label, value)      # on_release -> a real LTspice run
        page.wait_for_function(_TEXT_HAS, arg="Running", timeout=10_000)
        rec.add(shot(page))
        page.wait_for_function(_TEXT_HAS, arg="Idle", timeout=60_000)
        time.sleep(0.6)
        rec.add(shot(page))
        rec.hold(12)


CLIPS["cad_model3d"] = dict(script=os.path.join(EXAMPLES, "cad_model3d.py"),
                            clip=cad_clip, width=1100)
CLIPS["circuit_dashboard"] = dict(script=os.path.join(EXAMPLES, "circuit_dashboard.py"),
                                  clip=circuit_clip, width=1200)


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(CLIPS)
    for n in wanted:
        key = next(k for k in CLIPS if k.startswith(n))
        record(key, **CLIPS[key])
