"""Build your own component from HTML — no source edits, no frontend rebuild.

The ``Custom`` panel renders arbitrary HTML in a sandboxed iframe and gives you a
symmetric two-way channel, so you don't subclass anything or write a dispatcher:

  * your HTML calls ``canvas.send({event: ...})`` to talk to Python;
  * ``@panel.on("event")`` routes those messages by their ``event`` field;
  * your HTML calls ``canvas.onPush(fn)`` to receive ``panel.push(data)``.

This builds a ``Dial`` — a draggable knob — in a handful of lines of user code.

Run:  python examples/custom_component.py
"""

import danvas

# --- the widget's front end: plain HTML + vanilla JS in the iframe -----------
# It talks to Python with the injected ``canvas.send(...)`` helper and redraws
# its needle whenever Python pushes a new angle via ``canvas.onPush(...)``.
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
  // dial stays in sync if the angle is changed programmatically. No __danvas
  // unwrapping or message-guard boilerplate — canvas.onPush handles it.
  canvas.onPush((deg) => draw(deg))
</script>
"""


canvas = danvas.Canvas()

# No subclass needed: insert the HTML, then route inbound events with @dial.on.
dial = canvas.custom(html=DIAL_HTML, name="dial", w=220, h=260, x=80, y=80)
status = canvas.label("status", value="drag the dial", x=340, y=80)


@dial.on("rotate")
def on_rotate(msg):
    status.update(f"angle: {msg['deg']}°")


@dial.on("reset")
def on_reset(_msg):
    dial.push(0)               # push 0 back into the iframe to recentre the needle
    status.update("reset to 0°")


print("Drag the knob (and hit reset). All of this lives in user code — no "
      "package edits, no npm build.")
canvas.serve(port=8000)
