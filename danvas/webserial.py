"""A serial port an app can use as a link (Web Serial) and as a script (pyserial).

    from danvas.webserial import Serial

    dev = Serial(baud=115200, port="COM3")   # port is only used outside a browser

    @dev.on_line
    def got(text):
        print("device said", text)

    connect = canvas.button("connect", text="Connect")
    connect.on_click(lambda *_: dev.open())

    dev.write("led on\\n")

In danvas's static page (an app opened as a link) the port is on the reader's
own computer, reached through the browser's Web Serial API: open() asks the
browser, which lets the reader choose a device (once; a port they chose before
is reused on the next visit). Web Serial is in Chrome and Edge on a computer;
elsewhere open() reports an error through on_close. A browser only shows its
port picker right after a click, so call open() from a button's handler; if
that click is too long ago, the page shows its own "Choose a device" prompt.

Run as a script (python app.py), the same code opens `port` with pyserial
(pip install danvas[serial]) and reads it on a thread.

Callbacks: on_line(text) per line (\\n, a trailing \\r dropped), on_data(bytes)
per chunk as it arrives, on_open(info) and on_close(reason) as the port comes
and goes. Each may be used as a decorator.
"""
from __future__ import annotations

import sys
import threading
import traceback

_IN_BROWSER = sys.platform == "emscripten"
_ports: dict[int, "Serial"] = {}


def _call(fn, *args):
    try:
        fn(*args)
    except Exception:
        traceback.print_exc()


class Serial:
    def __init__(self, baud: int = 115200, port: str | None = None, filters: list[dict] | None = None):
        """`port` names the device outside a browser (COM3, /dev/ttyUSB0); in a
        browser the reader picks it, and `filters` (Web Serial's, e.g.
        [{"usbVendorId": 0x2341}] for Arduino boards) narrows the list."""
        self.baud, self.port, self.filters = baud, port, filters or []
        self.is_open = False
        self._buf = b""
        self._lines, self._data, self._opened, self._closed = [], [], [], []
        self._id = id(self)
        self._local = None
        _ports[self._id] = self

    # -- callbacks ------------------------------------------------------------------
    def on_line(self, fn):
        self._lines.append(fn)
        return fn

    def on_data(self, fn):
        self._data.append(fn)
        return fn

    def on_open(self, fn):
        self._opened.append(fn)
        return fn

    def on_close(self, fn):
        self._closed.append(fn)
        return fn

    # -- the port -------------------------------------------------------------------
    def open(self):
        if self.is_open:
            return
        if _IN_BROWSER:
            _post(self._id, "open", baud=self.baud, filters=self.filters)
        else:
            self._open_local()

    def write(self, data: str | bytes):
        if isinstance(data, str):
            data = data.encode()
        if not self.is_open:
            raise RuntimeError("the serial port is not open")
        if _IN_BROWSER:
            _post(self._id, "write", data=data)
        else:
            self._local.write(data)

    def close(self):
        if _IN_BROWSER:
            _post(self._id, "close")
        elif self._local is not None:
            self._local.close()

    # -- what arrives -------------------------------------------------------------------
    def _feed(self, data: bytes):
        for fn in self._data:
            _call(fn, data)
        if not self._lines:
            return
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            text = raw.rstrip(b"\r").decode("utf-8", "replace")
            for fn in self._lines:
                _call(fn, text)

    def _state(self, open_: bool, info: str):
        was = self.is_open
        self.is_open = open_
        if open_:
            self._buf = b""
            for fn in self._opened:
                _call(fn, info)
        elif was or info:
            for fn in self._closed:
                _call(fn, info)

    # -- outside a browser: pyserial on a thread ----------------------------------------
    def _open_local(self):
        try:
            import serial  # pyserial
        except ImportError:
            self._state(False, "pyserial is not installed: pip install danvas[serial]")
            return
        if not self.port:
            self._state(False, "no port given: Serial(port='COM3') names the device outside a browser")
            return
        try:
            self._local = serial.Serial(self.port, self.baud, timeout=0.2)
        except Exception as e:
            self._state(False, str(e))
            return
        self._state(True, self.port)

        def read():
            reason = ""
            try:
                while self._local.is_open:
                    chunk = self._local.read(self._local.in_waiting or 1)
                    if chunk:
                        self._feed(chunk)
            except Exception as e:
                reason = str(e)
            self._state(False, reason or "closed")

        threading.Thread(target=read, daemon=True, name=f"serial {self.port}").start()


# --- the browser: messages to and from the page ------------------------------------------

def _post(pid: int, op: str, **args):
    import js
    from pyodide.ffi import to_js
    js.postMessage(to_js({"ev": "serial", "pid": pid, "op": op, **args}, dict_converter=js.Object.fromEntries))


def _from_page(pid: int, kind: str, value=None):
    """The page's end: kind is "data" (bytes), "open" (a description) or "closed" (why)."""
    dev = _ports.get(pid)
    if dev is None:
        return
    if kind == "data":
        dev._feed(bytes(value.to_py() if hasattr(value, "to_py") else value))
    else:
        dev._state(kind == "open", str(value or ""))
