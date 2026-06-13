"""Every viewer gets a unique emoji that traces a figure-8 around their cursor.

Showcases the cursor features end to end:
  * `serve(cursors=True)` — viewers report their pointer (canvas coords).
  * Native peer rendering — each viewer sees the others' live coloured cursors.
  * `canvas.viewers[i]["cursor"]` — Python reads every pointer and drives a
    per-viewer Custom panel that figure-8s around it.

Open the page in two browser tabs/devices to see two cursors, each with its own
coloured pointer and its own emoji looping around it.

    python examples/moving_widget.py
"""

import math
import time

import pycanvas

A = 140          # figure-8 amplitude (px)
ORB = 60         # emoji panel size (px)
EMOJI = ["🛸", "🐙", "🦊", "🐢", "🦋", "🐝", "🦀", "🐳", "🦜", "🐧", "🦥", "🐲"]

canvas = pycanvas.Canvas()
speed = canvas.slider("speed", min=0.1, max=30, step=0.1, default=10, x=40, y=40,
                      label="orbit speed")


def _orb_html(emoji):
    # The orb is purely decorative — see the custom() call below, which makes the
    # panel itself click-through (grabbable=False + operable=False). user-select
    # none just stops the emoji being highlightable as you drag past it.
    return f"""
      <div style="width:100%;height:100%;display:flex;align-items:center;
                  justify-content:center;font-size:34px;
                  filter:drop-shadow(0 0 10px #38bdf8);
                  user-select:none;-webkit-user-select:none;">{emoji}</div>
    """


# Per-viewer state, owned by the orbit loop: id -> panel, id -> emoji.
panels = {}
emoji_of = {}


def _emoji_for(viewer_id):
    if viewer_id not in emoji_of:
        emoji_of[viewer_id] = EMOJI[len(emoji_of) % len(EMOJI)]
    return emoji_of[viewer_id]


@canvas.background
def orbit():
    t = 0.0
    while True:
        t += speed.value * 0.015
        viewers = {v["id"]: v for v in canvas.viewers}

        # Drop panels for viewers who left (native cursor_gone handles their dot).
        for vid in list(panels):
            if vid not in viewers:
                canvas.remove(panels.pop(vid))

        # Figure-8 (Gerono lemniscate) around each viewer's cursor.
        for vid, v in viewers.items():
            tip = v.get("cursor")
            if not tip:                       # they haven't moved yet
                continue
            x = tip["x"] + A * math.cos(t) - ORB / 2
            y = tip["y"] + A * math.sin(t) * math.cos(t) - ORB / 2
            panel = panels.get(vid)
            if panel is None:                 # first sighting -> spawn their emoji
                panel = canvas.custom(
                    name=f"orb_{vid}", x=x, y=y, w=ORB, h=ORB,
                    frame=False, grabbable=False, operable=False,
                    html=_orb_html(_emoji_for(vid)),
                )
                panels[vid] = panel
            else:
                panel.move(x, y)

        time.sleep(1 / 60)


if __name__ == "__main__":
    canvas.serve(port=8000, cursors=True, tunnel=True, hot_reload=True)
