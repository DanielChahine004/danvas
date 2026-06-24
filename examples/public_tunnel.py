"""Share a canvas with anyone, anywhere — via a public HTTPS tunnel.

LAN sharing (``host="0.0.0.0"``) only reaches devices on the same network. Pass
``tunnel=True`` and danvas keeps the server on ``127.0.0.1`` but opens a public
tunnel to it, printing a ``https://…`` URL you can send to anyone on any network.

Needs a tunnel binary on PATH. The default is cloudflared (no signup, no visitor
warning page): ``winget install --id Cloudflare.cloudflared`` /
``brew install cloudflared``. Or pass ``tunnel_provider="localtunnel"`` (Node).

Run:  python examples/public_tunnel.py
Then open the printed public URL anywhere — drag the slider and watch every
connected viewer update in real time.
"""

import danvas

canvas = danvas.Canvas()

servo = canvas.slider("servo_1", min=0, max=180, default=90)
status = canvas.label("status", value="idle")


@servo.on_change
def on_servo(value):
    status.update(f"servo at {value}")


# tunnel=True prints a public https URL alongside the local one. The tunnel is
# closed automatically when the server stops (Ctrl+C).
canvas.serve(port=8000, tunnel=True, password="secret")
