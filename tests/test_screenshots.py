"""Pixel-diff regression: re-shoot the README scenes and compare to the PNGs.

The other suites measure the wire and the DOM; this one measures the PICTURE.
It would have caught the three renderer regressions found while making the
README screenshots (a Plotly chart shrunk to a 140x40 plot area, an unplaced
title re-packed under its own `below=` chain, a literal "\\u2026" placeholder),
none of which any wire-level assertion could see.

For each deterministic scene in docs/screenshots/shoot.py the scene is launched
in a real danvasd + headless Chromium, driven the same way (the slider really
moved, the sliders really set), framed identically, and compared to the
committed PNG. A pixel "differs" when any channel moves by more than 24/255
(text antialiasing wobbles less than that); the scene fails when more than
1% of pixels differ. Regenerate baselines on an intended visual change:

    python docs/screenshots/shoot.py

Requires: playwright + chromium, pillow, plotly, and a built danvasd — all
skipped cleanly when absent.
"""

import os
import sys

import pytest

pytest.importorskip("playwright.sync_api")
Image = pytest.importorskip("PIL.Image")
pytest.importorskip("plotly")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHOTS = os.path.join(_ROOT, "docs", "screenshots")
sys.path.insert(0, _SHOTS)

import shoot  # noqa: E402  (docs/screenshots/shoot.py)

from test_frontend_smoke import _danvasd  # noqa: E402

DIFF_CHANNEL = 24      # per-channel tolerance (0-255) before a pixel counts
MAX_DIFF_FRACTION = 0.01


def _diff_fraction(a, b):
    from PIL import ImageChops
    if a.size != b.size:
        return 1.0
    d = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    # max over channels, then threshold
    mask = d.convert("L").point(lambda v: 255 if v > DIFF_CHANNEL else 0)
    changed = sum(1 for v in mask.getdata() if v)
    return changed / (a.size[0] * a.size[1])


def _diff_fraction_fast(a, b):
    try:
        import numpy as np
    except ImportError:
        return _diff_fraction(a, b)
    if a.size != b.size:
        return 1.0
    x = np.asarray(a.convert("RGB"), dtype=np.int16)
    y = np.asarray(b.convert("RGB"), dtype=np.int16)
    return float((np.abs(x - y).max(axis=2) > DIFF_CHANNEL).mean())


@pytest.mark.parametrize("name", shoot.DETERMINISTIC)
def test_scene_matches_committed_screenshot(name, tmp_path):
    if _danvasd() is None:
        pytest.skip("danvasd not built")
    baseline = os.path.join(_SHOTS, name + ".png")
    if not os.path.isfile(baseline):
        pytest.skip(f"no baseline {baseline}; run docs/screenshots/shoot.py")
    out = shoot.shoot(name, out_dir=str(tmp_path), **shoot.SHOTS[name])
    frac = _diff_fraction_fast(Image.open(out), Image.open(baseline))
    assert frac <= MAX_DIFF_FRACTION, (
        f"{name}: {frac:.2%} of pixels differ from docs/screenshots/{name}.png "
        f"(limit {MAX_DIFF_FRACTION:.0%}); the fresh capture is at {out}. "
        "If the change is intended, regenerate with docs/screenshots/shoot.py.")
