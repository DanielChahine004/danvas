"""Package a canvas into a standalone desktop app with canvas.bake().

The same file is both the source and the app:

  * ``python examples/bake_app.py``  -> builds ``dist/MiniConsole(.exe)``
  * launching that executable          -> runs this script in a native window,
                                          serving the canvas locally, with no
                                          Python or browser needed on the machine

Inside the built app ``sys.frozen`` is set, so ``bake()`` skips rebuilding and
just shows the window. Building needs the desktop extra:
``pip install -e ".[desktop]"``.
"""

import danvas

canvas = danvas.Canvas()

speed = canvas.slider("speed", min=0, max=100, default=20, x=80, y=80)
status = canvas.label("status", value="idle", x=80, y=200)


@speed.on_change
def on_speed(value):
    status.update("running" if value else "idle")

canvas.set_view(ui=False)  # hide tldraw's toolbars/menus

# Build when run with python; run in a window when launched as the executable.
canvas.bake(name="MiniConsole", window_size=(1000, 700))
