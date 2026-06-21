"""Stream this machine's screen to the canvas and let a remote browser drive it.

A single :class:`~danvas.Custom` panel doubles as a tiny remote desktop: a
background thread grabs the screen and ``push()``es JPEG frames straight into the
panel's iframe (no reload), while the iframe captures mouse + keyboard and sends
them back, where they're replayed onto this machine with the Win32 API.

    python examples/remote_control.py        # then open the printed network URL

================================ ⚠  SECURITY  ================================
This hands ANYONE who can open the canvas FULL mouse and keyboard control of
THIS machine, with **no authentication**. Treat it like leaving your computer
unlocked on the network. Only run it on a network you trust (ideally just your
own LAN), stop it (Ctrl+C) when you're done, and never expose it to the public
internet. You are responsible for what a connected client does.
=============================================================================

Dependencies: none beyond danvas — capture uses Pillow's ``ImageGrab`` and
control uses the built-in Win32 API via ``ctypes`` (so this example is
**Windows-only**; swap in ``pyautogui`` for a cross-platform controller).
"""

import base64
import ctypes
import io
import threading
import time

from PIL import ImageGrab

import danvas

# --- streaming knobs ---------------------------------------------------------
FPS = 12             # frames per second to stream
STREAM_WIDTH = 1100  # downscale frames to this width (bandwidth vs. sharpness)
JPEG_QUALITY = 50

# Match capture pixels and cursor coordinates to one (physical) pixel space.
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

_user32 = ctypes.windll.user32
SCREEN_W = _user32.GetSystemMetrics(0)
SCREEN_H = _user32.GetSystemMetrics(1)

# Win32 mouse_event / keybd_event flags.
_MOUSE = {
    "down": {0: 0x0002, 1: 0x0020, 2: 0x0008},  # left / middle / right press
    "up": {0: 0x0004, 1: 0x0040, 2: 0x0010},    # left / middle / right release
}
_WHEEL = 0x0800
_KEYUP = 0x0002

# e.key names (non-printable) -> Win32 virtual-key codes.
_VK = {
    "Enter": 0x0D, "Backspace": 0x08, "Tab": 0x09, "Escape": 0x1B,
    "Delete": 0x2E, "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27, "ArrowDown": 0x28,
    " ": 0x20,
}
_MODS = {"Shift", "Control", "Alt", "Meta"}


def _tap_vk(vk, shift=False, ctrl=False, alt=False, meta=False):
    """Press (and release) a virtual key, holding any requested modifiers."""
    if ctrl:
        _user32.keybd_event(0x11, 0, 0, 0)
    if alt:
        _user32.keybd_event(0x12, 0, 0, 0)
    if meta:
        _user32.keybd_event(0x5B, 0, 0, 0)
    if shift:
        _user32.keybd_event(0x10, 0, 0, 0)
    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, _KEYUP, 0)
    if shift:
        _user32.keybd_event(0x10, 0, _KEYUP, 0)
    if meta:
        _user32.keybd_event(0x5B, 0, _KEYUP, 0)
    if alt:
        _user32.keybd_event(0x12, 0, _KEYUP, 0)
    if ctrl:
        _user32.keybd_event(0x11, 0, _KEYUP, 0)


def _handle_key(ev):
    """Replay a keydown. Modifiers ride as flags; standalone ones are ignored."""
    key = ev.get("key", "")
    if key in _MODS:
        return  # applied via the ctrl/alt/meta/shift flags on the next key
    ctrl, alt, meta = ev.get("ctrl"), ev.get("alt"), ev.get("meta")
    if key in _VK:
        _tap_vk(_VK[key], shift=ev.get("shift"), ctrl=ctrl, alt=alt, meta=meta)
    elif len(key) == 1:
        # VkKeyScanW maps a character to a vk + the shift state needed to type it.
        res = _user32.VkKeyScanW(ord(key))
        if res == -1:
            return
        vk = res & 0xFF
        need_shift = bool((res >> 8) & 1)
        _tap_vk(vk, shift=need_shift, ctrl=ctrl, alt=alt, meta=meta)


# --- the control surface that runs inside the panel's sandboxed iframe --------
# Shows the streamed screen and forwards pointer + key events via canvas.send().
# Coordinates are normalized to 0..1 over the image so Python can scale them to
# the real screen regardless of the panel's size.
PANEL_HTML = """
<body style="margin:0;overflow:hidden;background:#000;cursor:crosshair">
  <img id="s" draggable="false"
       style="width:100vw;height:100vh;object-fit:fill;display:block">
  <script>
    const img = document.getElementById('s');
    // Frames pushed from Python (Custom.push) arrive as postMessage.
    window.addEventListener('message', (e) => {
      const d = e.data;
      if (d && d.__danvas !== undefined) {
        img.src = 'data:image/jpeg;base64,' + d.__danvas;
      }
    });
    const norm = (e) => {
      const r = img.getBoundingClientRect();
      return {
        x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
        y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
      };
    };
    let lastMove = 0;
    img.addEventListener('mousemove', (e) => {
      const now = performance.now();
      if (now - lastMove < 25) return;        // throttle to ~40/s
      lastMove = now;
      const p = norm(e); canvas.send({ k: 'move', x: p.x, y: p.y });
    });
    img.addEventListener('mousedown', (e) => {
      e.preventDefault(); window.focus();
      const p = norm(e); canvas.send({ k: 'down', x: p.x, y: p.y, b: e.button });
    });
    window.addEventListener('mouseup', (e) => {
      canvas.send({ k: 'up', b: e.button });
    });
    img.addEventListener('contextmenu', (e) => e.preventDefault());
    img.addEventListener('wheel', (e) => {
      e.preventDefault(); canvas.send({ k: 'wheel', d: e.deltaY });
    }, { passive: false });
    // Keyboard works once the panel has focus (click it first).
    window.addEventListener('keydown', (e) => {
      e.preventDefault();
      canvas.send({ k: 'key', key: e.key, shift: e.shiftKey,
                    ctrl: e.ctrlKey, alt: e.altKey, meta: e.metaKey });
    });
  </script>
</body>
"""

canvas = danvas.Canvas()

# Size the panel to the screen's aspect ratio so the streamed image isn't
# distorted (object-fit:fill maps the panel area 1:1 onto the screen).
_w = 760
_h = round(_w * SCREEN_H / SCREEN_W) + 30  # + header
screen = canvas.custom(html=PANEL_HTML, name="screen", label="remote screen",
                       w=_w, h=_h)


@screen.on_message
def on_event(ev):
    """Replay one browser input event onto this machine."""
    try:
        k = ev.get("k")
        if k == "move":
            _user32.SetCursorPos(int(ev["x"] * SCREEN_W), int(ev["y"] * SCREEN_H))
        elif k == "down":
            _user32.SetCursorPos(int(ev["x"] * SCREEN_W), int(ev["y"] * SCREEN_H))
            _user32.mouse_event(_MOUSE["down"].get(ev.get("b", 0), 0x0002), 0, 0, 0, 0)
        elif k == "up":
            _user32.mouse_event(_MOUSE["up"].get(ev.get("b", 0), 0x0004), 0, 0, 0, 0)
        elif k == "wheel":
            delta = -120 if ev.get("d", 0) > 0 else 120  # browser dy is inverted
            _user32.mouse_event(_WHEEL, 0, 0, delta, 0)
        elif k == "key":
            _handle_key(ev)
    except Exception as exc:  # never let one bad event kill the input handler
        print("input error:", exc)


def stream_screen():
    """Grab the screen and push JPEG frames into the panel ~FPS times a second."""
    period = 1.0 / FPS
    while True:
        start = time.time()
        img = ImageGrab.grab()  # primary monitor, physical pixels (DPI-aware)
        if img.width > STREAM_WIDTH:
            h = round(img.height * STREAM_WIDTH / img.width)
            img = img.resize((STREAM_WIDTH, h))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY)
        screen.push(base64.b64encode(buf.getvalue()).decode("ascii"))
        time.sleep(max(0, period - (time.time() - start)))


threading.Thread(target=stream_screen, daemon=True).start()

print("=" * 70)
print("⚠  REMOTE CONTROL IS LIVE — anyone who opens the canvas controls THIS PC.")
print("   Click the panel to give it focus, then use your mouse & keyboard.")
print("   Stop with Ctrl+C. Only run this on a network you trust.")
print("=" * 70)
canvas.serve(port=8000, host="0.0.0.0")
