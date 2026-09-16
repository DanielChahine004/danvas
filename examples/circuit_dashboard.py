"""
SiPM Bias Twin-T Notch Filter — live danvas dashboard
=====================================================
An interactive LTspice front-end. Instead of a broadband RC low-pass, this is a
self-contained passive **Twin-T notch** — it passes DC (the
SiPM bias) but rejects one narrow band, which you tune onto the switching ripple.
Drag a slider, let go, and LTspice re-simulates and every panel redraws.

  Vin --+--[ R1 ]--A--[ R2 ]--+-- Vout (-> SiPM)
        |          |          |
        |        [ 2C ]       |       notch at  f0 = 1/(2*pi*R*C)
        |          |          |       (deep band-stop; DC and HF pass at 0 dB)
        |         GND         |
        |                     |
        +--[ C2 ]--B--[ C3 ]--+
                   |
                 [ R/2 ]
                   |
                  GND

The five sliders are the circuit's knobs (matched parts: R1=R2=R, the shunt cap
is 2C, the shunt resistor is R/2):
    DC bias (V) · Ripple amp (mV) · Ripple freq (kHz) · R (Ω) · C (nF)

The defaults (R=100 Ω, C=16 nF) put f0 ≈ 99.5 kHz, right on the 100 kHz ripple,
so it starts deeply rejecting it — drag C to slide the notch off and watch the
ripple reappear.

Run:
    pip install danvas ltspice "numpy<2" matplotlib schemdraw   # ltspice needs NumPy 1.x
    python examples/circuit_dashboard.py   # opens the browser, blocks

LTspice itself does the maths — each slider release fires a real transient + AC
run (a full pair is ~0.8 s). Point ``LTSPICE_EXE`` (env var, or the constant
below) at your install.
"""

import math
import os
import subprocess
import tempfile
import threading

import matplotlib
matplotlib.use("Agg")            # headless; no GUI window per render
import matplotlib.pyplot as plt
import numpy as np
import ltspice
import schemdraw
import schemdraw.elements as elm

import danvas

# Path to the LTspice executable (adjust if yours differs).
LTSPICE_EXE = os.environ.get("LTSPICE_EXE", r"C:\Users\h\AppData\Local\Programs\ADI\LTspice\LTspice.exe")


def run_ltspice(netlist, sim_dir):
    """Write ``netlist`` to a .cir, batch-run LTspice, return the .raw path."""
    cir = os.path.join(sim_dir, "circuit.cir")
    with open(cir, "w") as f:
        f.write(netlist)
    result = subprocess.run([LTSPICE_EXE, "-b", "-Run", cir],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print("LTspice stderr:", result.stderr)
        raise RuntimeError("LTspice failed — check LTSPICE_EXE path")
    raw = cir.replace(".cir", ".raw")
    if not os.path.exists(raw):
        raise RuntimeError(f"No .raw output found at {raw}")
    return raw


# ── Twin-T notch netlists ──────────────────────────────────────────────────────
# Standard Twin-T: a low-pass T (R1=R2=R in series, shunt cap 2C to ground) in
# parallel with a high-pass T (C2=C3=C in series, shunt resistor R/2 to ground).
# With those matched ratios the two paths cancel at f0 = 1/(2*pi*R*C) → a deep
# notch, while DC passes through the resistor arm and HF through the cap arm.
# RL models the next stage's (SiPMsure a/TIA) input impedance and a clean DC reference.

def _twin_t(p):
    """The shared Twin-T body lines (same components for transient and AC)."""
    R, C = p["R"], p["C"]
    return (
        f"R1 input n1 {R}\n"
        f"R2 n1 output {R}\n"
        f"C1 n1 0 {2 * C}\n"
        f"C2 input n2 {C}\n"
        f"C3 n2 output {C}\n"
        f"R3 n2 0 {R / 2}\n"
        f"RL output 0 1Meg\n"
    )


def transient_netlist(p):
    """Transient netlist: 12 periods of the input ripple, 200 steps/period."""
    period = 1.0 / p["RIPPLE_FREQ"]
    end_time = 12 * period          # let the notch settle before measuring ripple
    step = period / 200
    return (
        "Twin-T Notch Transient\n"
        f"Vinput input 0 DC {p['DC_BIAS']} "
        f"SIN({p['DC_BIAS']} {p['RIPPLE_AMP']} {p['RIPPLE_FREQ']})\n"
        + _twin_t(p)
        + f".tran {step:.3e} {end_time:.3e}\n"
        ".save V(input) V(output)\n.backanno\n.end\n"
    )


def ac_netlist(p):
    """AC sweep netlist: 0.1 Hz → 10 MHz, 200 points/decade (resolves the notch)."""
    return (
        "Twin-T Notch AC\n"
        "Vin input 0 AC 1\n"
        + _twin_t(p)
        + ".ac dec 200 0.1 10Meg\n"
        ".save V(input) V(output)\n.backanno\n.end\n"
    )


# ── Renderers — each returns something danvas.Image.update() accepts ──────────

def schematic_png(p):
    """Draw the Twin-T notch schematic and return white-background PNG bytes.

    Built from explicit ``.at(start).to(end)`` coordinates (two parallel T's
    between a left input rail and a right output rail). White bg matters: the
    Image panel sits on a dark canvas, where black wires on a transparent
    background would vanish — ``transparent=False`` paints it white.
    ``get_imagedata`` renders straight to PNG bytes (``d.fig`` is schemdraw's own
    wrapper, not a Matplotlib figure).
    """
    R, C_nF, twoC_nF = p["R"], p["C"] * 1e9, 2 * p["C"] * 1e9
    with schemdraw.Drawing(show=False, transparent=False, dpi=130, fontsize=11) as d:
        # Top T: in → R1 → A → R2 → out, with 2C shunting A to ground.
        d.add(elm.Resistor().at((0, 0)).to((3, 0)).label(f"R1\n{R:.0f}Ω"))
        d.add(elm.Resistor().at((3, 0)).to((6, 0)).label(f"R2\n{R:.0f}Ω"))
        d.add(elm.Capacitor().at((3, 0)).to((3, -1.2)).label(f"2C\n{twoC_nF:.0f}nF", loc="bottom"))
        d.add(elm.Ground().at((3, -1.2)))
        # Bottom T: in → C2 → B → C3 → out, with R/2 shunting B to ground.
        d.add(elm.Capacitor().at((0, -2.4)).to((3, -2.4)).label(f"C2 {C_nF:.0f}nF", loc="top"))
        d.add(elm.Capacitor().at((3, -2.4)).to((6, -2.4)).label(f"C3 {C_nF:.0f}nF", loc="top"))
        d.add(elm.Resistor().at((3, -2.4)).to((3, -3.6)).label(f"R/2\n{R / 2:.0f}Ω", loc="bottom"))
        d.add(elm.Ground().at((3, -3.6)))
        # Input / output rails join the two T's.
        d.add(elm.Line().at((0, 0)).to((0, -2.4)))
        d.add(elm.Line().at((6, 0)).to((6, -2.4)))
        d.add(elm.Dot(open=True).at((0, 0)).label(
            f"Vin\n{p['DC_BIAS']:.0f}V DC\n"
            f"+{p['RIPPLE_AMP'] * 1000:.0f}mV @ {p['RIPPLE_FREQ'] / 1e3:.0f}kHz", loc="left"))
        d.add(elm.Dot(open=True).at((6, 0)).label("out\n→ SiPM", loc="right"))

    return d.get_imagedata("png")


def transient_figure(raw_path, p):
    """Parse the transient .raw and return (matplotlib figure, metrics dict)."""
    l = ltspice.Ltspice(raw_path, mode="Transient")
    l.parse()
    t = l.get_time()
    v_in = l.get_data("V(input)")
    v_out = l.get_data("V(output)")

    half = len(t) // 2                       # measure ripple on the settled tail
    rip_in = (v_in[half:].max() - v_in[half:].min()) * 1000
    rip_out = (v_out[half:].max() - v_out[half:].min()) * 1000
    atten = rip_in / rip_out if rip_out > 0 else float("inf")
    db = 20 * np.log10(atten) if np.isfinite(atten) and atten > 0 else float("inf")

    fig, axes = plt.subplots(2, 1, figsize=(6.4, 4.6), sharex=True)
    axes[0].plot(t * 1e6, v_in, color="tomato", linewidth=1.2)
    axes[0].set_ylabel("Voltage (V)")
    axes[0].set_title(f"Input — {rip_in:.1f} mV p-p")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(t * 1e6, v_out, color="steelblue", linewidth=1.2)
    axes[1].set_ylabel("Voltage (V)")
    axes[1].set_xlabel("Time (µs)")
    axes[1].set_title(f"Output — {rip_out:.4f} mV p-p  ({db:.1f} dB)")
    axes[1].grid(True, alpha=0.3)

    fig.tight_layout()
    return fig, {"rip_in": rip_in, "rip_out": rip_out, "atten": atten, "db": db}


def bode_figure(raw_path, p):
    """Parse the AC .raw and return a Bode-magnitude matplotlib figure."""
    l = ltspice.Ltspice(raw_path, mode="AC")
    l.parse()
    freq = l.get_frequency()
    v_out = l.get_data("V(output)")
    mag = 20 * np.log10(np.abs(v_out))
    f0 = 1.0 / (2 * np.pi * p["R"] * p["C"])
    notch_db = float(mag.min())              # depth actually achieved in the sweep

    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.semilogx(freq, mag, color="steelblue", linewidth=1.5)
    ax.axvline(f0, color="tomato", linestyle="--", linewidth=1,
               label=f"notch f₀ = {f0 / 1e3:.1f} kHz")
    ax.axvline(p["RIPPLE_FREQ"], color="orange", linestyle="--", linewidth=1,
               label=f"ripple {p['RIPPLE_FREQ'] / 1e3:.0f} kHz")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB)")
    ax.set_title(f"Twin-T notch — {p['R']:.0f}Ω, {p['C'] * 1e9:.0f}nF  "
                 f"(depth {notch_db:.0f} dB)")
    ax.set_xlim([freq[0], freq[-1]])
    ax.legend(fontsize=8)
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    return fig


def results_markdown(p, m, f0):
    """Compose the live results panel from the latest transient metrics."""
    rej = "∞ (perfect)" if not np.isfinite(m["atten"]) else f"{m['atten']:.0f}×"
    db = "∞" if not np.isfinite(m["db"]) else f"{m['db']:.1f} dB"
    # How well the ripple lands in the notch (a decade of mistuning = a few dB).
    if abs(math.log10(p["RIPPLE_FREQ"] / f0)) < 0.04:        # within ~10 %
        tune = "ripple sits **in the notch** → deeply rejected"
    else:
        where = "above" if p["RIPPLE_FREQ"] > f0 else "below"
        tune = (f"ripple is **{where}** the notch "
                f"({p['RIPPLE_FREQ'] / f0:.2f}× f₀) — drag C to retune f₀ onto it")
    return (
        f"### Results\n"
        f"- Notch f₀ = 1/(2π·R·C) = **{f0 / 1e3:.1f} kHz** ({f0:.0f} Hz)\n"
        f"- Input ripple: **{m['rip_in']:.1f} mV** p-p\n"
        f"- Output ripple: **{m['rip_out']:.4f} mV** p-p\n"
        f"- Ripple rejection: **{rej}** ({db})\n"
        f"- {tune}\n"
    )


# ── Canvas + panels ────────────────────────────────────────────────────────────

canvas = danvas.Canvas()

canvas.text(x=40, y=24, text="SiPM Bias Twin-T Notch Filter — live LTspice",
            color="light-blue", size="l", name="title")
canvas.text(x=40, y=54, text=f"engine: {LTSPICE_EXE}",
            color="grey", size="s", name="engine")

# Sliders — one per circuit parameter. Integer steps keep the readout clean;
# the real (V / F / Hz) values are reconstructed in current_params(). on_release
# means a simulation only fires when the user lets go of the thumb.
SLIDER_W = 330
bias = canvas.slider("bias", min=20, max=80, default=67, step=1,
                     on_release=True, label="DC bias (V)",
                     x=40, y=84, w=SLIDER_W)
ripple = canvas.slider("ripple", min=10, max=500, default=100, step=5,
                       on_release=True, label="Ripple amp (mV)",
                       below=bias, w=SLIDER_W, gap=10)
freq = canvas.slider("freq", min=1, max=1000, default=100, step=1,
                     on_release=True, label="Ripple freq (kHz)",
                     below=ripple, w=SLIDER_W, gap=10)
res = canvas.slider("res", min=1, max=2000, default=100, step=1,
                    on_release=True, label="Resistor R (Ω)",
                    below=freq, w=SLIDER_W, gap=10)
cap = canvas.slider("cap", min=1, max=1000, default=16, step=1,
                    on_release=True, label="Capacitor C (nF)",
                    below=res, w=SLIDER_W, gap=10)

status = canvas.label("status", "Starting…", below=cap, w=SLIDER_W, gap=14)
results = canvas.markdown("", name="results", below=status, w=SLIDER_W, h="auto",
                          gap=10)

# Output panels (right of the controls). Seeded blank; filled by recompute().
schem_img = canvas.image(b"", name="schematic", label="Schematic",
                         x=410, y=84)
trans_img = canvas.image(b"", name="transient", label="Transient — ripple rejection",
                         x=880, y=84, w=660, h=470)
bode_img = canvas.image(b"", name="bode", label="AC — notch response",
                        x=880, y=584, w=660, h=400)


# ── Recompute: schematic (fast) + LTspice transient & AC (the slow ~0.8 s bit) ──

_sim_lock = threading.Lock()     # serialise LTspice; one run touches the .raw files


def current_params():
    """Read the live slider values back into the netlist's native SI units."""
    return {
        "DC_BIAS": float(bias.value),
        "RIPPLE_AMP": float(ripple.value) / 1000.0,    # mV → V
        "RIPPLE_FREQ": float(freq.value) * 1e3,        # kHz → Hz
        "R": float(res.value),                         # Ω
        "C": float(cap.value) * 1e-9,                  # nF → F
    }


def recompute(*_):
    """Redraw the schematic, then run LTspice and refresh the plot panels.

    Registered on every slider as a dedicated ``queue="latest"`` handler, so it
    runs off the dispatch thread and rapid releases of one slider collapse to the
    newest. The lock serialises overlapping runs across *different* sliders, and
    params are re-read inside the lock so the simulation always reflects the
    latest knob positions (a superseded run just recomputes the same final state).
    """
    p = current_params()
    try:
        schem_img.update(schematic_png(p))          # param-only, instant
    except Exception as exc:                          # noqa: BLE001
        status.update(f"⚠ schematic error: {exc}")

    status.update("⏳ Running LTspice…")
    with _sim_lock:
        p = current_params()                          # freshest knobs win
        try:
            with tempfile.TemporaryDirectory() as tmp:
                raw_t = run_ltspice(transient_netlist(p), tmp)
                fig_t, metrics = transient_figure(raw_t, p)
                trans_img.update(fig_t)               # Image auto-closes the fig

                raw_ac = run_ltspice(ac_netlist(p), tmp)
                bode_img.update(bode_figure(raw_ac, p))

            f0 = 1.0 / (2 * math.pi * p["R"] * p["C"])
            results.update(results_markdown(p, metrics, f0))
            status.update("✓ Idle — drag a slider to re-simulate")
        except Exception as exc:                      # noqa: BLE001
            status.update(f"⚠ LTspice error: {exc}")


for _s in (bias, ripple, freq, res, cap):
    _s.on_change(recompute, dedicated=True, queue="latest")


# ── Serve ──────────────────────────────────────────────────────────────────────

recompute()          # seed all panels before the first browser connects
print("Drag a slider and release to re-run LTspice. Ctrl-C to stop.")
canvas.serve(port=8000, hot_reload=True)
