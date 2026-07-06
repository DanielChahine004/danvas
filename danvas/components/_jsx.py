"""Loader for the built-in panels' JSX sources.

Each native panel's React component lives as a real ``.jsx`` file in
``danvas/components/jsx/`` — named after its template kind — so editors
highlight and lint it as JSX rather than as a Python string literal. The
Python component classes load it here (cached; the files ship as package
data) and apply any per-class substitutions (``__CSS__``) on top, exactly as
when the source was inline. ``scripts/gen_component_templates.py`` renders
the same strings into the language-neutral template asset, so the ``.jsx``
files are the single source of truth for what every SDK's panels mount.
"""

import functools
import os

_DIR = os.path.join(os.path.dirname(__file__), "jsx")


@functools.lru_cache(maxsize=None)
def load(kind):
    """The JSX source for template ``kind`` (e.g. ``"slider"``)."""
    with open(os.path.join(_DIR, f"{kind}.jsx"), encoding="utf-8") as f:
        return f.read()
