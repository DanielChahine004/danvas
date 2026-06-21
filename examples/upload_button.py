"""Receive a file from a viewer's browser into Python.

The mirror of the Download button: a viewer picks (or drags in) a file and its
bytes stream up to Python over plain HTTP — behind the canvas's auth gate, with
no WebSocket size limits. Two panels shown here:

  * **in memory** — upload a CSV; Python parses it and renders it as a table;
  * **per-user to disk** — each upload is saved into a folder named for *who*
    uploaded it. ``@on_upload`` takes an optional ``viewer`` arg identifying the
    uploader (``role`` is server-trusted; ``name``/``id``/``color`` come from the
    live roster), so you can attribute and route files per person.

Tip: set distinct logins with ``serve(passwords={"alice": "a", "bob": "b"})`` to
see ``viewer["role"]`` differ per user; otherwise everyone shares ``role=None``
and is told apart by their auto-assigned roster name (Fox, Owl, ...).

Run:  python examples/upload_button.py
Then drop a .csv on the first panel, or any file on the second.
"""

import csv
import io
import os

import danvas

canvas = danvas.Canvas()

# 1) In-memory: parse an uploaded CSV and show it as a sortable table.
csv_up = canvas.upload("csv", text="Upload a CSV", accept=".csv",
                       max_size=5 * 1024 * 1024, x=40, y=40)
table = canvas.table([{"info": "upload a CSV to see it here"}],
                     name="preview", x=40, y=200, w=520, h=320)


@csv_up.on_upload
def show_csv(file, viewer):
    rows = list(csv.DictReader(io.StringIO(file.data.decode("utf-8", "replace"))))
    table.update(rows or [{"info": f"{file.name} had no rows"}])
    print(f"{viewer.get('name')} uploaded {file.name} ({len(rows)} rows)")


# 2) Per-user to disk: keep uploads in memory, then save each into a folder named
#    for whoever sent it. ``viewer`` carries the uploader's identity.
SAVE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
file_up = canvas.upload("file", text="Upload any file (saved per user)",
                        multiple=True, max_size=50 * 1024 * 1024, x=600, y=40)
status = canvas.label("status", value="no uploads yet", x=600, y=200, w=360)


@file_up.on_upload
def saved(file, viewer):
    # role is server-trusted; fall back to the roster name (or "anon") for a folder.
    who = viewer.get("role") or viewer.get("name") or "anon"
    user_dir = os.path.join(SAVE_ROOT, str(who))
    os.makedirs(user_dir, exist_ok=True)
    path = file.save(user_dir)
    status.update(f"{who} uploaded {file.name} ({file.size} bytes) -> {path}")
    print("saved", path)


print("Drop a CSV on the left panel, or any file on the right.")
canvas.serve(port=8000)
