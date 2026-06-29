"""Control which overlapping panel sits on top — the stacking-order (z-index) API.

Three coloured cards are placed *overlapping* on purpose, so only the top one is
fully visible. Pick a card with the target toggle, then use the four buttons to
restack it live:

    to_front()  — jump above every other panel
    to_back()   — drop beneath every other panel
    forward()   — step up one place
    backward()  — step down one place

``to_front`` / ``to_back`` are durable: reload the page (or open a second tab)
and the stack rebuilds in the same order. ``forward`` / ``backward`` are a single
overlap-aware nudge applied to the live canvas only.

    python examples/layering.py
"""

import danvas

canvas = danvas.Canvas()

# Three overlapping cards. Each is a frameless Custom panel filled with a solid
# colour and a big letter, so the stacking order is obvious at a glance.
CARDS = [("A", "#ef4444"), ("B", "#22c55e"), ("C", "#3b82f6")]


def _card_html(letter, color):
    return f"""
      <div style="width:100%;height:100%;display:flex;align-items:center;
                  justify-content:center;background:{color};border-radius:14px;
                  box-shadow:0 8px 24px rgba(0,0,0,.35);color:white;
                  font:700 64px system-ui,sans-serif;user-select:none;">{letter}</div>
    """


# Insert them stepped diagonally so each covers part of the one before it. They
# stack in insertion order, so C (added last) starts on top.
panels = {}
for i, (letter, color) in enumerate(CARDS):
    panels[letter] = canvas.custom(
        name=f"card_{letter}", x=160 + i * 70, y=120 + i * 70, w=220, h=220,
        frame=False, html=_card_html(letter, color),
    )

# Pick which card the layering buttons act on.
target = canvas.toggle(["A", "B", "C"], name="target", default="A",
                       x=160, y=440, label="target card")

# One button per stacking operation, laid out in a row under the cards.
ops = [
    ("to front", lambda p: p.to_front()),
    ("to back", lambda p: p.to_back()),
    ("forward", lambda p: p.forward()),
    ("backward", lambda p: p.backward()),
]
for i, (text, _) in enumerate(ops):
    btn = canvas.button(f"op_{i}", text=text, x=160 + i * 150, y=520)

    @btn.on_click
    def _click(_=None, op=ops[i][1], text=text):
        panel = panels[target.value]
        op(panel)
        print(f"{text} -> card {target.value}")


print("Pick a card with the 'target' toggle, then press the buttons to restack it.")
print("Reload the page after 'to front'/'to back' — the order sticks.")
canvas.serve(port=8000, host="0.0.0.0")
