"""Plot what a serial device prints, and send it lines.

The device prints numbers, one reading per line: "512" or "512,300,17" (comma
or space separated); each column becomes a trace. Any other line goes to the log.
reading.ino is a sketch for an Arduino that does this.

    as a link     the reader clicks Connect and chooses the device in the browser
    python app.py COM3     the same app, served locally, on that port
"""
import sys
from collections import deque

import danvas
from danvas.webserial import Serial

dev = Serial(baud=115200, port=sys.argv[1] if len(sys.argv) > 1 else None)

canvas = danvas.Canvas()
connect = canvas.button("device", text="Connect", x=60, y=60, w=200)
status = canvas.label("status", "not connected", right_of=connect)
plot = canvas.live_plot("readings", max_points=500, below=connect, w=640, h=300)
send = canvas.text_field("send", placeholder="a line to send, then Enter", below=plot, w=640)
log = canvas.markdown("", name="log", below=send, w=640, h=180)
recent = deque(maxlen=8)


@connect.on_click
def toggle():
    dev.close() if dev.is_open else dev.open()


@dev.on_open
def opened(info):
    status.update(f"connected: {info}")
    connect.update(text="Disconnect")


@dev.on_close
def closed(why):
    status.update(f"not connected: {why}")
    connect.update(text="Connect")


@dev.on_line
def line(text):
    parts = text.replace(",", " ").split()
    try:
        values = [float(p) for p in parts]
    except ValueError:
        values = None
    if values:
        plot.push({f"ch{i}": v for i, v in enumerate(values)})
    elif text.strip():
        recent.append(text)
        log.update("```\n" + "\n".join(recent) + "\n```")


@send.on_change
def sent(text):
    if dev.is_open and text:
        dev.write(text + "\n")


canvas.serve(port=8000)
