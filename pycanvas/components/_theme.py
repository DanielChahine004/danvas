"""Color parsing and CSS variable derivation for per-component color theming.

Usage in a component::

    from . import _theme
    theme = _theme.derive(color) if color is not None else {}
    # pass theme as the ``_th`` prop; JSX spreads it as style on the root element
"""

import colorsys


def _parse(color):
    """Return ``(r, g, b)`` ints from a tuple/list or ``'#rrggbb'``/``'#rgb'``."""
    if isinstance(color, (tuple, list)):
        return tuple(int(x) for x in color)
    s = str(color).strip().lstrip("#")
    if len(s) == 3:
        s = s[0] * 2 + s[1] * 2 + s[2] * 2
    if len(s) != 6:
        raise ValueError(
            f"color must be an (r, g, b) tuple or a hex string '#rrggbb', got {color!r}"
        )
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _hex(r, g, b):
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def derive(color):
    """Return a CSS custom-property dict derived from *color*.

    *color* may be an ``(r, g, b)`` tuple (0–255 ints) or a hex string
    such as ``'#3b82f6'`` or ``'#38f'``.

    The returned dict is ready to be passed as a React ``style`` prop —
    spreading it on a component's root element scopes the theme to that
    panel only (CSS custom properties cascade inward)::

        {"--pc-accent":      "#3b82f6",
         "--pc-accent-dk":   "#2563eb",   # darker — hover / active
         "--pc-accent-t":    "rgba(59,130,246,.35)",  # translucent — glow
         "--pc-accent-text": "#fff"}      # legible text colour on the accent bg
    """
    r, g, b = _parse(color)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)

    def _mk(h, l, s):
        nr, ng, nb = colorsys.hls_to_rgb(
            h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s))
        )
        return round(nr * 255), round(ng * 255), round(nb * 255)

    dk = _mk(h, l * 0.75, s)   # 25% darker — hover / active

    # Relative luminance (simplified sRGB gamma) to choose a readable text colour.
    lum = (0.2126 * (r / 255) ** 2.2
           + 0.7152 * (g / 255) ** 2.2
           + 0.0722 * (b / 255) ** 2.2)
    txt = "#fff" if lum < 0.18 else "#1a1a2e"

    return {
        "--pc-accent":      _hex(r, g, b),
        "--pc-accent-dk":   _hex(*dk),
        "--pc-accent-t":    f"rgba({r},{g},{b},.35)",
        "--pc-accent-text": txt,
    }


def accent_hex(color):
    """Return just the ``'#rrggbb'`` accent hex for *color* (for frame theming)."""
    r, g, b = _parse(color)
    return _hex(r, g, b)
