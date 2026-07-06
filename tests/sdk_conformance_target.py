"""The Python reference target for the source-SDK conformance suite.

Spawned by tests/test_sdk_conformance.py as::

    python tests/sdk_conformance_target.py <broker_port>

It dials into an already-running danvasd and stands up the fixed behavior
script every conformance target implements (the Rust twin is
danvas-rust/examples/conformance_target.rs). This one is deliberately the
*production* Python owner path — a full Canvas transplanted onto the broker by
serve_via_broker(existing_port=...) — so the suite measures what a real
`canvas.serve()` app does on the wire, not a purpose-built shim.

## The target behavior script (normative for every SDK)

| panel | template | behavior |
|---|---|---|
| `lbl` | label  | text "hello"; the readout every handler writes into |
| `sld` | slider | min 0 max 100 value 10; input {value: v} → lbl shows "v=<v:int>" |
| `ask` | button | request {ping: n} → response {pong: n+1} |
| `dl`  | download | click request → {url, filename}; serves b"conformance-bytes\\n" as hello.txt |
| `up`  | upload | its data.url receives POSTs; each file → lbl shows "up=<name>:<size>" |
| `bin` | custom | INPUT envelope (code 5) → lbl shows "bin=<len>" AND the bytes echo back as a CUSTOM (code 3) envelope |
| `cam` | video  | passive until… |
| `ctl` | button | …a click: send ONE VIDEO (code 1) envelope on `cam` carrying b"\\xff\\xd8conformance-jpeg" |

Plus the standing duties the suite exercises without target cooperation:
replay everything on reconnect (the broker restarts under it), fold
browser-sent layout into that replay, answer file_pull/file_push broadcasts
for unknown tokens with a decline, and heartbeat.
"""

import sys

import danvas
from danvas.remote import serve_via_broker


def main():
    port = int(sys.argv[1])
    canvas = danvas.Canvas()

    lbl = canvas.label("lbl", "hello", x=10, y=10)
    sld = canvas.slider("sld", min=0, max=100, default=10, x=10, y=110)

    @sld.on_change
    def _slid(v):
        lbl.update(f"v={v:.0f}")

    ask = canvas.button("ask", x=10, y=210)

    @ask.on_request()
    def _asked(data):
        return {"pong": (data or {}).get("ping", 0) + 1}

    canvas.download("dl", source=b"conformance-bytes\n",
                    filename="hello.txt", x=10, y=310)

    up = canvas.upload("up", x=10, y=410)

    @up.on_upload
    def _got(file):
        lbl.update(f"up={file.name}:{file.size}")

    bin_panel = canvas.custom(html="<b>bin</b>", name="bin", x=10, y=510)

    @bin_panel.on_binary
    def _binned(data):
        lbl.update(f"bin={len(data)}")
        bin_panel.push_binary(bytes(data))   # echoes as a CUSTOM (3) envelope

    cam = canvas.video("cam", encode=False, x=10, y=610)
    ctl = canvas.button("ctl", x=10, y=710)

    @ctl.on_click
    def _clicked():
        cam.update(b"\xff\xd8conformance-jpeg")

    serve_via_broker(canvas, existing_port=port, open_browser=False,
                     block=True)


if __name__ == "__main__":
    main()
