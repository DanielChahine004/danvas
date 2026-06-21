"""Streamlit-mode layout demo.

Demonstrates ``canvas.streamlit()``: vertical scrolling, full viewport width,
panels stacked top-to-bottom like a Streamlit app.

Layout (spans the full browser window width):

    ┌─ Title ─────────────────────────────────────────────────────────┐
    ├─ Controls row ──────────────────────────────────────────────────┤
    │  [Start]  [Pause]  [Reset]   lr: ◄────────────────────────────►│
    ├─ Metrics row ───────────────────────────────────────────────────┤
    │  Step │ Loss │ Val Loss │ Acc │ LR                              │
    ├─ Run log (h="auto") ────────────────────────────────────────────┤
    │  - step 0 …                                                     │
    │  - step 25 …  ← grows → status bar shifts down automatically   │
    ├─ Status bar ────────────────────────────────────────────────────┤
    └─────────────────────────────────────────────────────────────────┘

Run:
    python examples/streamlit_layout.py
"""

import math
import random
import time

import danvas

rng = random.Random(42)
canvas = danvas.Canvas()

# ── Streamlit root ────────────────────────────────────────────────────────────
# Sets camera to scroll_y (vertical scroll, zoom=1), returns a full-width
# column container.  All children automatically fill the viewport width.
page = canvas.streamlit(gap=20, padding=24)

# ── Title ─────────────────────────────────────────────────────────────────────
page.add(canvas.label("title", "Training Dashboard"))

# ── Controls row ─────────────────────────────────────────────────────────────
with page.row(gap=10) as controls:
    start_btn = canvas.button("start", text="Start")
    pause_btn = canvas.button("pause", text="Pause")
    reset_btn = canvas.button("reset", text="Reset")
    lr_slider = canvas.slider("lr", min=0.001, max=0.05,
                              default=0.01, step=0.001)

# ── Metrics row ───────────────────────────────────────────────────────────────
with page.row(gap=8) as metrics:
    step_lbl     = canvas.label("step_lbl",  "Step: 0")
    loss_lbl     = canvas.label("loss_lbl",  "Loss: —")
    val_loss_lbl = canvas.label("val_loss",  "Val: —")
    acc_lbl      = canvas.label("acc_lbl",   "Acc: —")
    lr_lbl       = canvas.label("lr_lbl",    "LR: 0.010")

# ── Run log (auto-height) ─────────────────────────────────────────────────────
# h="auto": the browser measures rendered height on each update() and triggers
# a container repack so the status bar below always stays flush under the log.
run_log = page.add(canvas.markdown("### Log\n\n_Press Start to begin…_", h="auto"))

# ── Status bar ────────────────────────────────────────────────────────────────
status = page.add(canvas.label("status", "Ready"))


# ── State ─────────────────────────────────────────────────────────────────────
state = {"running": False, "step": 0}
lines = []


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
    val_loss_lbl.update("Val: —")
    acc_lbl.update("Acc: —")
    lr_lbl.update(f"LR: {lr_slider.value:.3f}")
    run_log.update("### Log\n\n_Reset — press Start_")
    status.update("Ready")


# ── Background training loop ──────────────────────────────────────────────────
@canvas.background
def loop():
    while True:
        if not state["running"]:
            time.sleep(0.1)
            continue

        s  = state["step"]
        lr = lr_slider.value
        t  = s / 300

        train_loss = 0.05 + 2.3 * math.exp(-4 * t * (lr / 0.01)) + rng.gauss(0, 0.04)
        val_loss   = 0.08 + 2.4 * math.exp(-3.5 * t * (lr / 0.01)) + rng.gauss(0, 0.07)
        train_acc  = max(0.0, min(1.0, 1 - train_loss / 2.5))

        step_lbl.update(f"Step: {s}")
        loss_lbl.update(f"Loss: {train_loss:.3f}")
        val_loss_lbl.update(f"Val:  {val_loss:.3f}")
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
            run_log.update("### Log\n\n" + "\n".join(lines[-20:]))

        state["step"] += 1
        time.sleep(0.05)


canvas.serve()
