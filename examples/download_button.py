"""Download files (or freshly-generated data) from the server to the viewer.

The browser can't read the host disk — Python can — so a Download button lets
*host code* decide exactly what bytes leave the machine; the viewer only ever
gets an unguessable, short-lived URL pointing at them (streamed behind the same
auth gate as the rest of the canvas). Two flavours shown here:

  * a **static file** — point ``source=`` at a path on disk;
  * **generated on click** — register ``@download.provide`` to produce the bytes
    (and an optional filename) fresh every time the button is pressed.

Run:  python examples/download_button.py
Then click either button — your browser saves the file.
"""

import csv
import io
import os
import time

import pycanvas

canvas = pycanvas.Canvas()

# 1) A static file: serve this very script. ``filename`` sets the saved-as name
#    (otherwise the path's basename is used).
THIS_FILE = os.path.abspath(__file__)
canvas.download("source", source=THIS_FILE, filename="download_button.py",
                text="Download this script", x=40, y=40)

# 2) Generated on click: build a CSV in memory each press, named with a
#    timestamp so every download is distinct. The provider returns a
#    ``(filename, bytes)`` pair; returning just bytes (or a path) works too.
report = canvas.download("report", text="Export CSV report", x=40, y=150)


@report.provide
def make_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["t", "value"])
    for i in range(10):
        w.writerow([i, round((i * 1.5) % 7, 2)])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return (f"report-{stamp}.csv", buf.getvalue().encode("utf-8"))


canvas.label("hint", value="Click a button — the file downloads to your machine.",
             x=320, y=40)

print("Open the canvas and click a download button.")
canvas.serve(port=8000)
