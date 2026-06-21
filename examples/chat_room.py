"""A shared chat room on the canvas.

Everyone who opens the canvas (locally, over the LAN, or through a tunnel) sees
the same conversation and can pick their own display name. The live-viewer badge
at the top shows how many people are connected. Python can post messages too and
observe everything that's said.

    python examples/chat_room.py
"""

import danvas

canvas = danvas.Canvas()

chat = canvas.chat("chat", x=80, y=80)
canvas.label("tip", "edit your name in the chat panel", x=440, y=80)


# Observe the room from Python (e.g. to log it or trigger actions).
@chat.on_message
def on_message(entry):
    print(f"[chat] {entry['name']}: {entry['text']}")


# Greet the room from the host side.
chat.post("welcome — say hi 👋")

# host="" makes it reachable from other devices on the same Wi-Fi; pass
# tunnel=True instead to share it with anyone on the public internet.
canvas.serve(port=8000, tunnel=True)
