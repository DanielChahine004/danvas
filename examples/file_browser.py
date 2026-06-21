"""Browse a folder, pick a file, run a pipeline on it, see the result on canvas.

The FileBrowser lists a directory (navigable in/out of subfolders, but sandboxed
to ``root``). Selecting a file fires ``@browser.on_select`` with its path; here
we read the chosen image with OpenCV and push it to a VideoFeed panel — swap in
your own pipeline and drive any panel (Plot, LivePlot, Label, ...) instead.

Run:  python examples/file_browser.py
"""

import os

import danvas

canvas = danvas.Canvas()

# Browse the repo's examples/ folder by default; point this anywhere you like.
ROOT = os.path.dirname(os.path.abspath(__file__))

files = canvas.file_browser("files", root=ROOT, x=40, y=40)
status = canvas.label("status", value="pick a file", x=400, y=40)


@files.on_select
def on_select(path):
    # Your pipeline goes here. This demo just reports the choice; a real one
    # might do: fig = analyze(path); plot.update(fig)
    status.update(f"selected: {os.path.basename(path)}  ({os.path.getsize(path)} bytes)")


@files.on_navigate
def on_navigate(cwd):
    print("now in", cwd)


print("Browse the examples/ folder and click a file.")
canvas.serve(port=8000)
