"""Canvas shapes: geo, text, note, draw, line, frame, highlight + drawing observation.

All seven managed shape types and the ephemeral drawing observation layer:

  canvas.geo(...)        – rectangle, ellipse, cloud, star, diamond, …
  canvas.text(...)       – plain floating text
  canvas.note(...)       – sticky note with a coloured background
  canvas.draw(...)       – freehand stroke defined by a point list
  canvas.line(...)       – polyline / cubic spline through control points
  canvas.frame(...)      – artboard container (labels a region)
  canvas.highlight(...)  – semi-transparent highlighter stroke
  canvas.drawings        – live dict of user-drawn ephemeral shapes
  canvas.on_draw         – fires when users draw, move, or delete shapes

Managed shapes are Python-owned: they survive page reload, can be updated live,
and are excluded from the free-form drawing sync.  User-drawn shapes are
ephemeral: they sync across all viewers but live only in the tldraw store — the
``on_draw`` hook lets Python react to them and optionally mutate or delete them.
"""

import math
import time

import pycanvas

canvas = pycanvas.Canvas()
canvas.set_view(navigation=('scroll_y', 0.7))  # lock horizontal pan; scroll up/down only

# ---------------------------------------------------------------------------
# 1. Geo shapes — the big family of vector shapes
# ---------------------------------------------------------------------------
canvas.geo(x=40, y=40, w=160, h=100, geo="rectangle",
           color="blue", fill="semi", name="rect")
canvas.geo(x=220, y=40, w=140, h=100, geo="ellipse",
           color="green", fill="solid", name="ell")
canvas.geo(x=380, y=40, w=140, h=100, geo="diamond",
           color="violet", fill="semi", name="diamond")
canvas.geo(x=540, y=40, w=120, h=100, geo="star",
           color="orange", fill="solid", name="star")
canvas.geo(x=680, y=40, w=140, h=100, geo="cloud",
           color="light-blue", fill="semi", name="cloud")

# A labelled rectangle we'll update live
status_box = canvas.geo(x=40, y=180, w=260, h=80, geo="rectangle",
                        color="black", fill="none", dash="dashed",
                        text="waiting…", name="status-box")

# ---------------------------------------------------------------------------
# 2. Text and sticky notes
# ---------------------------------------------------------------------------
canvas.text(x=330, y=190, text="Floating text: ALSO NOTICE THIS CANVAS HAS canvas.camer - 'scroll_y' so you can only scroll up and down!", color="grey", size="l",
            font="sans", name="float-text")

canvas.note(x=760, y=470, text="Sticky note\n(yellow)", color="yellow",
            name="note-yellow")
canvas.note(right_of=canvas["note-yellow"],  text="Sticky note\n(blue)", color="blue",
            name="note-blue")

# ---------------------------------------------------------------------------
# 3. Freehand draw stroke
# ---------------------------------------------------------------------------
# A sine-wave path expressed as a list of (x, y) tuples.
wave_pts = [
    (40 + i * 4, 380 + int(30 * math.sin(i * 0.4)))
    for i in range(60)
]
canvas.draw(wave_pts, color="red", size="m", name="wave")

# A simple rough circle
circle_pts = [
    (560 + int(50 * math.cos(a)), 400 + int(50 * math.sin(a)))
    for a in [i * 0.25 for i in range(26)]
]
canvas.draw(circle_pts, color="violet", size="l", name="rough-circle")

# ---------------------------------------------------------------------------
# 4. Polyline / cubic spline
# ---------------------------------------------------------------------------
canvas.line([(40, 480), (120, 430), (200, 480), (280, 430), (360, 480)],
            color="black", dash="solid", size="m", name="zigzag")

canvas.line([(420, 480), (500, 430), (580, 480), (660, 430), (740, 480)],
            color="blue", spline="cubic", size="m", name="spline")

# ---------------------------------------------------------------------------
# 5. Artboard frame
# ---------------------------------------------------------------------------
canvas.frame(x=40, y=540, w=780, h=160, label="Canvas shapes demo",
             name="demo-frame")

# ---------------------------------------------------------------------------
# 6. Highlighter strokes
# ---------------------------------------------------------------------------
canvas.highlight([(50, 560), (400, 560)], color="yellow", size="xl",
                 name="hl-yellow")
canvas.highlight([(50, 600), (250, 600)], color="light-green", size="l",
                 name="hl-green")

# ---------------------------------------------------------------------------
# 7. Live update panel + counter
# ---------------------------------------------------------------------------
counter = canvas.label("draw-counter", value="user-drawn shapes: 0",
                       x=40, y=740)
last_event = canvas.label("last-event", value="—  (draw something on the canvas)",
                          x=40, y=790, w=780)

# ---------------------------------------------------------------------------
# 8. Ephemeral drawing observation
# ---------------------------------------------------------------------------
@canvas.on_draw
def on_user_draw(event):
    """React whenever a viewer draws, moves, or deletes a shape."""
    n = len(canvas.drawings)
    counter.update(f"user-drawn shapes: {n}")

    # Report the most recent change.
    if event["added"]:
        s = event["added"][-1]
        last_event.update(f"added  {s.type}  at ({s.x:.0f}, {s.y:.0f})"
                          f"  color={s.color or '—'}")
    elif event["updated"]:
        s = event["updated"][-1]
        last_event.update(f"updated {s.type}  now at ({s.x:.0f}, {s.y:.0f})")
    elif event["removed"]:
        last_event.update(f"deleted {event['removed'][-1]}")


# ---------------------------------------------------------------------------
# 9. Live shape update demo — cycle the status box colour every 3 s
# ---------------------------------------------------------------------------
COLORS = ["black", "blue", "green", "red", "violet", "orange"]
_ci = 0

@canvas.background
def cycle_color():
    global _ci
    while True:
        time.sleep(3)
        _ci = (_ci + 1) % len(COLORS)
        status_box.color = COLORS[_ci]
        status_box.text = f"color → {COLORS[_ci]}"


print("Canvas shapes demo.  Draw anything on the canvas and watch the counter.")
canvas.serve(port=8001, tunnel=True)
