"""Build your own component from HTML — no source edits, no frontend rebuild.

The ``Custom`` panel renders arbitrary HTML in a sandboxed iframe and gives you
a two-way channel: your HTML calls ``canvas.send(data)`` to talk to Python, and
Python calls ``panel.push(data)`` to stream data back into the iframe. Because
that's all ordinary Python + HTML, you can package a reusable widget by
subclassing ``Custom`` and exposing whatever decorator API you like.

This example builds a ``Dial`` — a draggable knob — entirely in user space:

  * the HTML emits ``{event: "rotate", deg: ...}`` / ``{event: "reset"}`` via
    ``canvas.send`` and listens for pushes to redraw the needle;
  * the ``Dial`` subclass adds a custom ``@dial.on("rotate")`` decorator that
    routes inbound messages by their ``event`` field (see ``_handle_input``).

Run:  python examples/custom_component.py
"""

import pycanvas

# --- the widget's front end: plain HTML + vanilla JS in the iframe -----------
# It talks to Python with the injected ``canvas.send(...)`` helper and redraws
# its needle whenever Python pushes a new angle (the ``message`` event).
DIAL_HTML = """
<style>
  body { margin: 0; font-family: system-ui, sans-serif; text-align: center; }
  #knob { touch-action: none; cursor: grab; }
  #knob:active { cursor: grabbing; }
  button { margin-top: 6px; font-size: 13px; }
</style>

<svg id="knob" width="160" height="160" viewBox="-80 -80 160 160">
  <circle r="70" fill="#1e293b" stroke="#475569" stroke-width="3"/>
  <line id="needle" x1="0" y1="0" x2="0" y2="-60" stroke="#38bdf8" stroke-width="5"
        stroke-linecap="round"/>
  <circle r="6" fill="#38bdf8"/>
</svg>
<div id="readout">0&deg;</div>
<button id="reset">reset</button>

<script>
  const knob = document.getElementById('knob')
  const needle = document.getElementById('needle')
  const readout = document.getElementById('readout')
  let dragging = false

  // Pointer angle (degrees, 0 = up, clockwise) relative to the knob centre.
  function angleFromEvent(e) {
    const r = knob.getBoundingClientRect()
    const dx = e.clientX - (r.left + r.width / 2)
    const dy = e.clientY - (r.top + r.height / 2)
    let deg = Math.atan2(dx, -dy) * 180 / Math.PI   // 0 at top, clockwise
    if (deg < 0) deg += 360
    return Math.round(deg)
  }

  function draw(deg) {
    needle.setAttribute('transform', `rotate(${deg})`)
    readout.textContent = deg + '\\u00b0'
  }

  knob.addEventListener('pointerdown', (e) => {
    dragging = true
    knob.setPointerCapture(e.pointerId)
  })
  knob.addEventListener('pointermove', (e) => {
    if (!dragging) return
    const deg = angleFromEvent(e)
    draw(deg)                                  // optimistic local redraw
    canvas.send({ event: 'rotate', deg })      // -> Python
  })
  knob.addEventListener('pointerup', () => { dragging = false })

  document.getElementById('reset').addEventListener('click', () => {
    canvas.send({ event: 'reset' })
  })

  // Python -> iframe: panel.push(deg) lands here and redraws the needle, so the
  // dial stays in sync if the angle is changed programmatically.
  window.addEventListener('message', (e) => {
    if (e.data && e.data.__pycanvas !== undefined) draw(e.data.__pycanvas)
  })
</script>
"""


class Dial(pycanvas.Custom):
    """A draggable knob packaged as a reusable component.

    Subclasses :class:`pycanvas.Custom`, so it needs no changes to the package
    and no frontend rebuild — the HTML above *is* the component. It adds an
    ``@dial.on(event)`` decorator that routes inbound ``canvas.send`` messages by
    their ``event`` field, and a ``value`` that holds the last angle.
    """

    def __init__(self, name="dial", **place):
        super().__init__(html=DIAL_HTML, name=name, width=220, height=260, **place)
        self._routes = {}
        self._value = 0

    def on(self, event):
        """Decorator: register a handler for one ``event`` the HTML emits."""
        def deco(fn):
            self._routes.setdefault(event, []).append(fn)
            return fn
        return deco

    def set_angle(self, deg):
        """Drive the dial from Python; the needle redraws in the browser."""
        self._value = deg
        self.push(deg)

    # Custom delivers the raw ``canvas.send`` payload here; we fan it out to the
    # handlers registered for that event instead of a single value callback.
    def _handle_input(self, payload):
        event = payload.get("event")
        if event == "rotate":
            self._value = payload.get("deg", self._value)
        for fn in self._routes.get(event, []):
            try:
                fn(payload)
            except Exception:
                import traceback
                traceback.print_exc()


canvas = pycanvas.Canvas()

dial = canvas.insert(Dial("dial"), x=80, y=80)
status = canvas.label("status", value="drag the dial", x=340, y=80)


@dial.on("rotate")
def on_rotate(msg):
    status.update(f"angle: {msg['deg']}°")


@dial.on("reset")
def on_reset(_msg):
    dial.set_angle(0)          # push 0 back into the iframe to recentre the needle
    status.update("reset to 0°")


print("Drag the knob (and hit reset). All of this lives in user code — no "
      "package edits, no npm build.")
canvas.serve(port=8000)
