"""Turn an arbitrary Python value into the right canvas panel.

:func:`panel_for` is the shared brain behind ``Canvas.show(value)`` and the
notebook cell-capture (:mod:`pycanvas.autopanel`). It inspects a value and picks
a component the same way a notebook decides how to render an ``Out[...]``: rich
objects (DataFrames, figures, anything with ``_repr_html_`` / ``_repr_png_``)
render richly; plain structures and scalars fall back to a table, JSON tree, or
label.

Unlike the notebook formatter it does **not** require IPython — it calls the
``_repr_*`` hooks directly — so ``Canvas.show`` works in plain scripts too. When
an IPython ``display_formatter`` *is* available (the cell-capture passes one), it
is used first so notebook-registered formatters are honoured.
"""

import base64
import json

from .components import BaseComponent, Custom, Image, Label, Markdown, Plot, Table
from .components._doc import document


def panel_for(value, name="panel", label=None, w=None, h=None,
              formatter=None):
    """Build (but don't insert) the panel that best renders ``value``.

    Detection order, most specific first, so scripts and notebooks agree:
    existing component → Plotly → image-like (Matplotlib/PIL/array) → tabular
    (DataFrame/Series) → rich ``_repr_*`` → dict/list (JSON) → string → scalar.
    ``formatter`` is an optional IPython ``display_formatter`` used ahead of the
    ``_repr_*`` hooks. ``w``/``h`` override the chosen component's default size
    (each component carries its own sensible default).
    """
    if isinstance(value, BaseComponent):
        return value  # already a panel — show it as-is

    if _is_plotly(value):
        plot = Plot(name=name, label=label, w=w, h=h)
        plot.update(value)
        return plot

    if _is_image_like(value):
        return Image(value, name=name, label=label, w=w, h=h)

    if _is_tabular(value):
        return Table(value, name=name, label=label, w=w, h=h)

    html = _rich_html(value, formatter)
    if html is not None:
        return Custom(html=html, name=name, label=label, w=w, h=h)

    if isinstance(value, (dict, list, tuple, set)):
        body = (
            "<pre style='margin:0;white-space:pre-wrap;font-size:12px'>"
            f"{_escape(_as_json(value))}</pre>"
        )
        return Custom(html=document(body), name=name, label=label, w=w, h=h)

    if isinstance(value, str):
        # A short single-line string reads best as a bold Label; longer/multi-line
        # text is treated as Markdown (which renders plain prose fine too).
        if "\n" not in value and len(value) <= 80:
            return Label(name=name, value=value, label=label)
        return Markdown(value, name=name, label=label, w=w, h=h)

    return Label(name=name, value=_short(value), label=label)


# -- type duck-typing (no hard imports of pandas/numpy/plotly/matplotlib) -----
def _is_plotly(obj):
    mod = type(obj).__module__ or ""
    return mod.startswith("plotly") and callable(getattr(obj, "to_html", None))


def _is_tabular(obj):
    # pandas DataFrame/Series expose to_html; guard out plotly (also has to_html).
    mod = type(obj).__module__ or ""
    if mod.startswith("pandas") and callable(getattr(obj, "to_html", None)):
        return True
    # Records (list of dicts) or a matrix (list of rows) are clearly tabular; a
    # list of scalars or a bare dict stays JSON, where it reads better.
    if isinstance(obj, (list, tuple)) and obj:
        if all(isinstance(x, dict) for x in obj):
            return True
        if all(isinstance(x, (list, tuple)) for x in obj):
            return True
    return False


def _is_image_like(obj):
    mod = type(obj).__module__ or ""
    if mod.startswith("matplotlib"):
        return hasattr(obj, "savefig") or hasattr(obj, "get_figure")
    if mod.startswith("PIL"):
        return hasattr(obj, "save") and hasattr(obj, "mode")
    if mod.startswith("numpy") and hasattr(obj, "shape"):
        # 2-D (grayscale) or H×W×{3,4} (RGB/RGBA) arrays read as images;
        # anything else is data, handled as a table/structure.
        shape = obj.shape
        return len(shape) == 2 or (len(shape) == 3 and shape[2] in (3, 4))
    return False


# -- rich representation (IPython-free) --------------------------------------
def _rich_html(value, formatter=None):
    """An HTML body from ``value``'s rich repr, or ``None`` if it has none."""
    data = {}
    if formatter is not None:
        try:
            data, _ = formatter.format(value)
        except Exception:
            data = {}
    if not data:
        data = _dunder_bundle(value)
    if "text/html" in data:
        return document(_join(data["text/html"]))
    for mime in ("image/png", "image/jpeg"):
        if mime in data:
            b64 = _join(data[mime]).strip()
            return document(
                f"<img style='max-width:100%;height:auto' "
                f"src='data:{mime};base64,{b64}'>"
            )
    if "image/svg+xml" in data:
        return document(_join(data["image/svg+xml"]))
    return None


def _dunder_bundle(value):
    """Collect MIME data straight from the object's ``_repr_*`` hooks."""
    out = {}
    h = getattr(value, "_repr_html_", None)
    if callable(h):
        try:
            r = h()
            if r:
                out["text/html"] = r
        except Exception:
            pass
    for attr, mime in (("_repr_png_", "image/png"), ("_repr_jpeg_", "image/jpeg")):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                r = fn()
                if r:
                    out[mime] = (r if isinstance(r, str)
                                 else base64.b64encode(r).decode("ascii"))
            except Exception:
                pass
    svg = getattr(value, "_repr_svg_", None)
    if callable(svg):
        try:
            r = svg()
            if r:
                out["image/svg+xml"] = r
        except Exception:
            pass
    mb = getattr(value, "_repr_mimebundle_", None)
    if callable(mb):
        try:
            r = mb()
            if isinstance(r, tuple):
                r = r[0]
            if isinstance(r, dict):
                for k, v in r.items():
                    out.setdefault(k, v)
        except Exception:
            pass
    return out


# -- small helpers -----------------------------------------------------------
def _join(v):
    """IPython reprs may return a list of strings; join to one."""
    return "".join(v) if isinstance(v, (list, tuple)) else v


def _as_json(value):
    try:
        return json.dumps(value, indent=2, default=str)
    except Exception:
        return repr(value)


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _short(value, limit=2000):
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + " …"
