"""Container layout demo.

Shows the full container API:

  • canvas.column() — root vertical stacker
  • container.row() — nested horizontal row inside the column
  • explicit .add() — build the layout programmatically, step by step
  • context-manager style — compact auto-interception of panels in a block
  • auto-repack — a growing h="auto" log shifts the status bar down without
    any reflow() call; the browser repacks the tree when heights settle
  • container.move() — reposition the whole layout live from a button

Layout (one 640-wide column, everything inside it):

    ┌─ Header label ──────────────────────────────────────────┐
    ├─ Metrics row ───────────────────────────────────────────┤
    │  Step │ Loss │ Acc │ LR                                 │
    ├─ Controls row ──────────────────────────────────────────┤
    │  [Start] [Pause] [Reset]  lr: ◄────────►               │
    ├─ Run log (h="auto") ────────────────────────────────────┤
    │  - step 0 …                                            │
    │  - step 25 …           ← grows → status bar auto-shifts│
    ├─ Status bar ────────────────────────────────────────────┤
    └─────────────────────────────────────────────────────────┘

Run:
    python examples/container_layout.py
"""

import math
import random
import time

import danvas

rng = random.Random(42)
canvas = danvas.Canvas()


# ── Root column ──────────────────────────────────────────────────────────────
# x/y anchors it on the canvas; w=640 fixes every child to 640 px wide.
layout = canvas.column(x=60, y=40, w=640, gap=20)


# ── Header — added explicitly ─────────────────────────────────────────────────
layout.add(canvas.label("header", "Container Layout Demo"))


# ── Metrics row — nested inside the column ───────────────────────────────────
# layout.row() creates a child row container, adds it to layout, returns it.
# Labels sit side by side at their natural width; row height = tallest child.
metrics = layout.row(gap=8)
step_lbl = metrics.add(canvas.label("step_lbl",  "Step: 0"))
loss_lbl = metrics.add(canvas.label("loss_lbl",  "Loss: —"))
acc_lbl  = metrics.add(canvas.label("acc_lbl",   "Acc: —"))
lr_lbl   = metrics.add(canvas.label("lr_lbl",    "LR: 0.010"))


# ── Controls row — context-manager style ─────────────────────────────────────
# `with layout.row() as row:` creates the child row and auto-intercepts
# every panel created inside the block — same result as explicit .add() calls.
with layout.row(gap=10) as controls:
    start_btn  = canvas.button("start",  text="Start")
    pause_btn  = canvas.button("pause",  text="Pause")
    reset_btn  = canvas.button("reset",  text="Reset")
    move_btn   = canvas.button("move",   text="Move →")
    lr_slider  = canvas.slider("lr", min=0.001, max=0.05,
                               default=0.01, step=0.001)


# ── Log panel (h="auto") ──────────────────────────────────────────────────────
# auto-height: the browser measures the rendered content and reports the height.
# When a new line is appended and the panel grows, the frontend auto-repacks
# the column — the status bar below slides down with no reflow() needed.
run_log = layout.add(
    canvas.markdown("### Log\n\n_Press Start to begin…_", h="auto")
)


# ── Status bar at the bottom ──────────────────────────────────────────────────
status = layout.add(canvas.label("status", "Ready"))


# ── State ────────────────────────────────────────────────────────────────────
state  = {"running": False, "step": 0, "moved": False}
lines  = []


# ── Callbacks ─────────────────────────────────────────────────────────────────
@start_btn.on_click
def on_start():
    if state["step"] == 0 or not state["running"]:
        state["running"] = True
        start_btn.update(text="Running…")
        pause_btn.update(text="Pause")


@pause_btn.on_click
def on_pause():
    state["running"] = not state["running"]
    pause_btn.update(text="Resume" if not state["running"] else "Pause")


@reset_btn.on_click
def on_reset():
    state.update(running=False, step=0)
    lines.clear()
    start_btn.update(text="Start")
    pause_btn.update(text="Pause")
    step_lbl.update("Step: 0")
    loss_lbl.update("Loss: —")
    acc_lbl.update("Acc: —")
    lr_lbl.update(f"LR: {lr_slider.value:.3f}")
    run_log.update("### Log\n\n_Reset — press Start_")
    status.update("Ready")


@move_btn.on_click
def on_move():
    # Move the entire layout tree — all children shift together.
    if state["moved"]:
        layout.move(60, 40)
        move_btn.update(text="Move →")
    else:
        layout.move(360, 40)
        move_btn.update(text="Move ←")
    state["moved"] = not state["moved"]


# ── Background loop ───────────────────────────────────────────────────────────
@canvas.background
def loop():
    while True:
        if not state["running"]:
            time.sleep(0.1)
            continue

        s   = state["step"]
        lr  = lr_slider.value
        t   = s / 300

        train_loss = 0.05 + 2.3 * math.exp(-4 * t * (lr / 0.01)) + rng.gauss(0, 0.04)
        val_loss   = 0.08 + 2.4 * math.exp(-3.5 * t * (lr / 0.01)) + rng.gauss(0, 0.07)
        train_acc  = max(0.0, min(1.0, 1 - train_loss / 2.5))

        step_lbl.update(f"Step: {s}")
        loss_lbl.update(f"Loss: {train_loss:.3f}")
        acc_lbl.update(f"Acc:  {train_acc:.1%}")
        lr_lbl.update(f"LR:   {lr:.3f}")
        status.update(
            f"step {s}  ·  loss {train_loss:.3f}  ·  val {val_loss:.3f}"
            f"  ·  lr {lr:.4f}"
        )

        if s % 25 == 0:
            lines.append(
                f"- **step {s}** — loss `{train_loss:.3f}`, "
                f"val `{val_loss:.3f}`, acc `{train_acc:.1%}`"
            )
            # Appending a line makes the log panel taller.  Because it's h="auto"
            # the browser measures the new height and auto-repacks the column —
            # the status bar slides down without any reflow() call from Python.
            run_log.update("### Log\n\n" + "\n".join(lines[-20:]))

        state["step"] += 1
        time.sleep(0.05)


canvas.serve()
