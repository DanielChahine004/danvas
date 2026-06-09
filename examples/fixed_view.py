"""Configure the canvas as a fixed UI instead of a free creative workspace.

The ``view`` option on ``serve`` controls how tldraw is presented and navigated:
where the camera starts and how zoomed, whether the viewer may pan/zoom, and
whether the toolbars are shown. Here we frame two panels and lock the view down
into a kiosk-style dashboard.
"""

import pycanvas

canvas = pycanvas.Canvas()

speed = canvas.slider("speed", min=0, max=100, default=20, x=80, y=80)
status = canvas.label("status", value="idle", x=80, y=200)


@speed.on_change
def on_speed(value):
    status.update("running" if value else "idle")


canvas.serve(
    port=8000,
    view={
        "x": 200,        # centre the camera on this canvas point...
        "y": 160,
        "zoom": 1.0,     # ...at 100% zoom
        "locked": True,  # no panning or zooming — a fixed frame
        "ui": False,     # hide tldraw's toolbars/menus
    },
)
