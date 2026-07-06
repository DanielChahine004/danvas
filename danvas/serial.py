"""The serial bridge: a wired device becomes a danvas source.

A microcontroller with no network (an Arduino Uno, an STM32 on RS-485) can't
speak WebSocket — but the danvas protocol is just frames, and a UART carries
frames fine. This bridge is the shipped middleman: the device prints
**newline-delimited JSON frames** down the wire, and the bridge relays them to
a hub as a dial-in source, carrying every duty too heavy for firmware — the
WebSocket, heartbeat, auto-reconnect, the replay cache, and template
expansion. The device's whole job is ``Serial.println(json)`` out and
``readline`` + parse in.

Run it (needs ``pip install danvas[serial]``)::

    python -m danvas.serial COM3                 # serve-by-default on :8000
    python -m danvas.serial /dev/ttyUSB0 --baud 115200 --url 192.168.1.9:8000

Device → bridge (one JSON object per line):

- ``{"type": "register_template", "id", "kind", "data": {...}, "x", "y",
  "rel": {...}}`` — a native panel from the shared asset, expanded bridge-side
  so the firmware never carries the templates.
- ``{"type": "register", ...}`` / ``{"type": "update", "id", "payload"}`` /
  ``{"type": "remove", "id"}`` — the raw wire verbs, cached for replay.
- ``{"type": "response", "reqId", "result"}`` — answering a request.
- Anything else is forwarded verbatim (chat, view, ...).

Bridge → device (one JSON object per line): the ``input`` / ``layout`` /
``request`` frames for panels this device registered — nothing else, so a
big shared canvas can't flood a 115200-baud link. Binary envelopes are not
bridged (a UART is no place for video); everything text works.
"""

import argparse
import json
import sys
import threading
import time

from .source import SourceClient


class SerialBridge:
    """Pump frames between a line-oriented transport and a hub connection.

    ``transport`` is anything with ``readline() -> bytes`` and
    ``write(bytes)`` (a pyserial ``Serial``, or any file-like pair in tests).
    """

    def __init__(self, transport, client):
        self.transport = transport
        self.client = client
        self._ids = set()          # panels the device registered
        self._closing = False
        client.on_frame(self._from_hub)

    # -- device -> hub ---------------------------------------------------------
    def pump(self):
        """Read device lines forever (call on its own thread or as the main loop)."""
        while not self._closing:
            try:
                raw = self.transport.readline()
            except Exception:
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw.decode("utf-8", "replace").strip() or "{}")
            except ValueError:
                continue   # boot noise / partial line — a device may babble
            if not isinstance(msg, dict) or "type" not in msg:
                continue
            try:
                self._from_device(msg)
            except Exception:
                # A malformed frame must not kill the pump — the device may
                # be mid-flash or speaking a newer vocabulary.
                import traceback
                traceback.print_exc()

    def _from_device(self, msg):
        kind = msg.get("type")
        cid = msg.get("id")
        if kind == "register_template":
            self._ids.add(cid)
            self.client.register_template(
                cid, msg.get("kind"), name=msg.get("name"),
                x=msg.get("x"), y=msg.get("y"), w=msg.get("w"),
                h=msg.get("h"), rel=msg.get("rel"),
                **(msg.get("data") or {}))
        elif kind == "register":
            self._ids.add(cid)
            place = {k: msg[k] for k in ("x", "y", "rel") if k in msg}
            self.client.register(cid, msg.get("component", "React"),
                                 props=msg.get("props"), **place)
        elif kind == "update":
            payload = msg.get("payload") or {}
            if isinstance(payload, dict) and payload:
                self.client.update(cid, **payload)
        elif kind == "remove":
            self._ids.discard(cid)
            self.client.remove(cid)
        else:
            # response / chat / view / ... — verbatim; the device is a peer.
            self.client._send(msg)

    # -- hub -> device ---------------------------------------------------------
    def _from_hub(self, msg):
        # Only interactions on the device's own panels ride the wire down —
        # a 115200-baud link must never drink a whole canvas replay.
        if msg.get("type") not in ("input", "layout", "request"):
            return
        if msg.get("id") not in self._ids:
            return
        try:
            self.transport.write((json.dumps(msg) + "\n").encode("utf-8"))
        except Exception:
            pass   # device unplugged; pump() will notice on its side

    def close(self):
        self._closing = True


def _serve_or_attach(port):
    """The SDK convention: attach to a hub on ``port``, else spawn danvasd."""
    import socket
    import webbrowser
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        return None   # already served — attach quietly
    except OSError:
        pass
    import subprocess
    from .remote import _find_danvasd
    binary = _find_danvasd()
    if binary is None:
        raise SystemExit("no hub on the port and danvasd was not found "
                         "(set $DANVASD or pass --url to dial elsewhere)")
    proc = subprocess.Popen([binary, "--port", str(port)])
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            webbrowser.open(f"http://127.0.0.1:{port}")
            return proc
        except OSError:
            time.sleep(0.1)
    raise SystemExit("danvasd never opened its port")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m danvas.serial",
        description="Bridge a serial device onto a danvas canvas.")
    ap.add_argument("device", help="serial port (COM3, /dev/ttyUSB0, or any "
                                   "pyserial URL like socket:// or loop://)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--url", default=None,
                    help="dial an existing hub (host:port) instead of "
                         "serving one")
    ap.add_argument("--port", type=int, default=8000,
                    help="serve-by-default port (spawn/attach danvasd)")
    ap.add_argument("--label", default="serial")
    args = ap.parse_args(argv)

    try:
        import serial as pyserial
    except ImportError:
        raise SystemExit("pyserial is required: pip install danvas[serial]")

    broker = None
    if args.url is None:
        broker = _serve_or_attach(args.port)
        url = f"127.0.0.1:{args.port}"
    else:
        url = args.url

    transport = pyserial.serial_for_url(args.device, baudrate=args.baud,
                                        timeout=1)
    client = SourceClient(url, label=args.label)
    client.connect()
    bridge = SerialBridge(transport, client)
    print(f"[danvas.serial] {args.device} @ {args.baud} <-> {url} "
          f"(label {args.label!r})")
    try:
        bridge.pump()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()
        client.close()
        if broker is not None:
            broker.terminate()


if __name__ == "__main__":
    main()
