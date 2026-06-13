"""TensorBoard-style training tracker, built from plain PyCanvas panels.

The canvas answer to ``tensorboard --logdir``: instead of event files and a
separate web app, you track a run live on an infinite canvas — and because
PyCanvas is bidirectional you also get *controls* TensorBoard can't give you
(pause/resume, a live learning-rate slider) on the same board.

There's no logging framework to learn. You make each panel once, keep the
handle, and push to it from your loop:

    loss = canvas.live_plot("loss", traces=["train", "val"], smoothing=0.6)
    loss.push({"train": train_loss, "val": val_loss}, x=step)   # in the loop

    weights = canvas.histogram("weights", bins=40)
    weights.add(layer.weight, step=epoch)                       # distribution

    canvas.table(hparams)                                       # key/value table

Needs ``plotly`` (live plots + histogram) and ``matplotlib`` (the sample grid):

    pip install plotly matplotlib
    python training_dashboard/train_dashboard.py
"""

import math
import time

import matplotlib

matplotlib.use("Agg")  # headless backend; no GUI window
import matplotlib.pyplot as plt
import numpy as np

import pycanvas

HPARAMS = {
    "model": "tiny-cnn",
    "optimizer": "adam",
    "batch_size": 64,
    "epochs": 30,
    "steps_per_epoch": 50,
    "dropout": 0.2,
}
STEPS = HPARAMS["epochs"] * HPARAMS["steps_per_epoch"]
rng = np.random.default_rng(0)


def sample_grid(step):
    """A 3x3 grid of synthetic 'predictions' — TensorBoard's IMAGES tab."""
    fig, axes = plt.subplots(3, 3, figsize=(4.4, 4.4), dpi=100)
    acc = min(0.98, 0.4 + 0.6 * step / STEPS)
    for ax in axes.ravel():
        ax.imshow(rng.random((8, 8)), cmap="magma")
        true = int(rng.integers(0, 10))
        correct = rng.random() < acc
        pred = true if correct else int(rng.integers(0, 10))
        ax.set_title(f"{pred}/{true}", fontsize=8,
                     color="#16a34a" if correct else "#dc2626")
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(f"pred / true  —  step {step}", fontsize=9)
    fig.tight_layout()
    return fig


canvas = pycanvas.Canvas()

# --- controls: a left column of panels we drive the run with ----------------
# `canvas.column(...)` auto-stacks whatever we insert, each keeping its natural
# height, so we never hand-place a control.
with canvas.column(w=320, gap=12, origin=(40, 40)):
    status = canvas.label("status", "ready — press start")
    start = canvas.button("start / pause", text="Start")
    reset = canvas.button("reset", text="Reset")
    lr = canvas.slider("learning rate", min=0.0005, max=0.05, default=0.01, step=0.0005)
    smooth = canvas.slider("smoothing", min=0.0, max=0.95, default=0.6, step=0.05)

# --- charts: make each panel once, then push to it in the loop --------------
loss = canvas.live_plot("loss", traces=["train", "val"], smoothing=0.6, max_points=None, x=440, y=40, w=580, h=280)
acc = canvas.live_plot("accuracy", traces=["train", "val"], smoothing=0.6, max_points=None, below=loss, w=580, h=280)
lr_plot = canvas.live_plot("lr_plot", label="learning rate", max_points=None, below=acc, w=580, h=220)  # unsmoothed
weights = canvas.histogram("weights", bins=40, below=lr_plot, w=580, h=300)

# --- run summary: hyperparameters, sample predictions, a text log -----------
with canvas.column(w=460, gap=16, origin=(1060, 40)):
    canvas.table(HPARAMS, name="hparams", h="auto")        # flat dict -> table
    preds = canvas.image(sample_grid(0), name="predictions", h="auto")
    log = canvas.markdown("### run log\n\n_waiting to start…_", name="run log", h="auto")

state = {"running": False, "step": 0, "weights": rng.normal(0, 0.5, 2048)}
log_lines = []


@start.on_click
def _toggle():
    state["running"] = not state["running"]
    start.update(text="Pause" if state["running"] else "Start")


@reset.on_click
def _reset():
    state.update(running=False, step=0, weights=rng.normal(0, 0.5, 2048))
    log_lines.clear()
    start.update(text="Start")
    for plot in (loss, acc, lr_plot):
        plot.clear()
    status.update("reset — press start")


@smooth.on_change
def _set_smoothing(value):
    loss.smoothing = value
    acc.smoothing = value


@canvas.background
def train():
    """Stand-in training loop. Swap this body for your real one."""
    while True:
        if not state["running"] or state["step"] >= STEPS:
            if state["step"] >= STEPS and state["running"]:
                state["running"] = False
                start.update(text="Start")
                status.update("done ✓")
            time.sleep(0.05)
            continue

        step = state["step"]
        epoch = step // HPARAMS["steps_per_epoch"]
        learn = lr.value  # live-adjustable from the slider

        # Loss decays faster at higher lr; val trails train and is noisier.
        progress = math.exp(-3.5 * learn / 0.01 * step / STEPS)
        train_loss = 0.05 + 2.3 * progress + rng.normal(0, 0.03)
        val_loss = 0.10 + 2.4 * progress + rng.normal(0, 0.07)

        loss.push({"train": train_loss, "val": val_loss}, x=step)
        acc.push({"train": 1 - train_loss / 2.6, "val": 1 - val_loss / 2.6}, x=step)
        lr_plot.push({"lr": learn}, x=step)

        status.update(
            f"epoch {epoch + 1}/{HPARAMS['epochs']}  ·  step {step + 1}/{STEPS}"
            f"  ·  loss {train_loss:.3f}  ·  lr {learn:.4f}"
        )

        # Once per epoch: nudge the weights, redraw histogram + sample grid + log.
        if step % HPARAMS["steps_per_epoch"] == 0:
            state["weights"] = state["weights"] * 0.98 + rng.normal(0, 0.02, 2048)
            weights.add(state["weights"], step=epoch)
            preds.update(sample_grid(step))
            log_lines.append(f"- epoch **{epoch + 1}** — train {train_loss:.3f}, val {val_loss:.3f}")
            log.update("### run log\n\n" + "\n".join(log_lines[-12:]))

        state["step"] += 1
        time.sleep(0.04)


print("Open the canvas, press Start, and drag 'learning rate' mid-run to watch")
print("the loss curve react — the same loop a real model would drive.")
canvas.serve(port=8000)
