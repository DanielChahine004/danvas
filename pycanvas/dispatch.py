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
import os
import re

from .components import BaseComponent, Custom, Image, Label, Markdown, Plot, Table
from .components._doc import document


def panel_for(value, name="panel", label=None, w=None, h=None,
              formatter=None):
    """Build (but don't insert) the panel that best renders ``value``.

    Detection order, most specific first, so scripts and notebooks agree:
    existing component → Plotly → image-like (Matplotlib/PIL/array) → tabular
    (DataFrame/Series) → rich ``_repr_*`` → dict/list (JSON) → bytes → string →
    scalar. ``formatter`` is an optional IPython ``display_formatter`` used ahead
    of the ``_repr_*`` hooks. ``w``/``h`` override the chosen component's default
    size (each component carries its own sensible default).

    Strings are inspected rather than always shown verbatim: an existing **file
    path** renders by type (image/CSV/Markdown/JSON/HTML/text), an **image URL or
    data URI** becomes an image, a bare **web URL** becomes a clickable link,
    **HTML** renders as HTML, **Markdown** syntax renders as Markdown, and only a
    plain short one-liner stays a bold Label. ``bytes`` carrying an image render
    as that image.
    """
    if isinstance(value, BaseComponent):
        return value  # already a panel — show it as-is

    # A pathlib.Path (or any os.PathLike) is handled as a filesystem-path string.
    if isinstance(value, os.PathLike):
        value = os.fspath(value)

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

    # Raw bytes that look like an image (PNG/JPEG/GIF/…) render as the image;
    # anything else falls through to a repr label.
    if isinstance(value, (bytes, bytearray, memoryview)) and _is_image_bytes(value):
        return Image(bytes(value), name=name, label=label, w=w, h=h)

    if isinstance(value, str):
        return _string_panel(value, name, label, w, h, formatter)

    return Label(name=name, value=_short(value), label=label)


# Image file extensions Image can load directly (and that we recognize in paths
# and URLs). Kept here so the path and URL detectors agree on what's an image.
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico",
              ".tif", ".tiff", ".avif"}


def _string_panel(s, name, label, w, h, formatter):
    """Pick the best panel for a plain string by inspecting what it contains."""
    # An existing file (short, single-line path) renders by its extension.
    if "\n" not in s and len(s) <= 1024:
        try:
            is_file = os.path.isfile(s)
        except (ValueError, OSError):
            is_file = False  # e.g. embedded NULs on some platforms
        if is_file:
            panel = _path_panel(s, name, label, w, h, formatter)
            if panel is not None:
                return panel

    # A URL/URI pointing at an image renders as the image.
    if _is_image_url(s):
        return Image(s, name=name, label=label, w=w, h=h)

    # A bare web URL becomes a clickable link rather than dead text.
    if "\n" not in s and len(s) <= 2048 and s.startswith(("http://", "https://")):
        return Markdown(f"[{s}]({s})", name=name, label=label, w=w, h=h)

    # Literal HTML renders as HTML (wrapped for base styling unless it's already
    # a full document).
    if _looks_like_html(s):
        full = re.match(r"\s*<(?:!doctype|html)\b", s, re.I)
        return Custom(html=s if full else document(s),
                      name=name, label=label, w=w, h=h)

    # Markdown syntax (at any length) renders as Markdown.
    if _looks_like_markdown(s):
        return Markdown(s, name=name, label=label, w=w, h=h)

    # Plain text: a short single line is a bold Label; longer prose is Markdown
    # (which renders plain paragraphs fine).
    if "\n" not in s and len(s) <= 80:
        return Label(name=name, value=s, label=label)
    return Markdown(s, name=name, label=label, w=w, h=h)


def _path_panel(path, name, label, w, h, formatter):
    """Render an existing file by extension, or ``None`` if it's not a type we
    handle (the caller then falls back to treating the string as text)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in _IMAGE_EXT:
            return Image(path, name=name, label=label, w=w, h=h)
        if ext == ".csv":
            return _csv_table(path, name, label, w, h)
        if ext in (".md", ".markdown"):
            return Markdown(_read_text(path), name=name, label=label, w=w, h=h)
        if ext == ".json":
            with open(path, encoding="utf-8") as f:
                return panel_for(json.load(f), name=name, label=label,
                                 w=w, h=h, formatter=formatter)
        if ext in (".html", ".htm"):
            return Custom(html=_read_text(path), name=name, label=label, w=w, h=h)
        if ext in (".txt", ".log", ".rst", ".text"):
            return Markdown(_read_text(path), name=name, label=label, w=w, h=h)
    except Exception:
        return None  # unreadable / malformed -> fall back to text handling
    return None


def _read_text(path, limit=1_000_000):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(limit)


def _csv_table(path, name, label, w, h, max_rows=5000):
    """Load a CSV into a Table (stdlib csv, no pandas needed)."""
    import csv

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        rows = []
        for i, row in enumerate(reader):
            if i > max_rows:
                break
            rows.append(row)
    if not rows:
        return Label(name=name, value="(empty CSV)", label=label)
    header, body = rows[0], rows[1:]
    records = [dict(zip(header, r)) for r in body]
    return Table(records or rows, name=name, label=label, w=w, h=h)


def _is_image_url(s):
    """True for a ``data:image/...`` URI or an http(s) URL ending in an image ext."""
    if s.startswith("data:image/"):
        return True
    if not s.startswith(("http://", "https://")):
        return False
    path = s.split("?", 1)[0].split("#", 1)[0]
    return os.path.splitext(path)[1].lower() in _IMAGE_EXT


def _is_image_bytes(b):
    """Sniff common image magic numbers so ``show(img_bytes)`` renders the image."""
    sig = bytes(b[:16])
    head = sig.lstrip()[:5].lower()
    return (
        sig.startswith(b"\x89PNG")
        or sig.startswith(b"\xff\xd8\xff")            # JPEG
        or sig[:6] in (b"GIF87a", b"GIF89a")
        or sig.startswith(b"BM")                      # BMP
        or (sig[:4] == b"RIFF" and bytes(b[8:12]) == b"WEBP")
        or head == b"<svg "
        or head == b"<?xml"                           # often an SVG document
    )


_HTML_RE = re.compile(r"\s*<(?:!doctype|!--|[a-z][\w:-]*)(?:\s|>|/)", re.I)


def _looks_like_html(s):
    """A conservative check: the string opens with a tag and has a closing ``>``."""
    return bool(_HTML_RE.match(s)) and ">" in s


# Markers strong enough to call a string Markdown. Single ``*`` italics and bare
# ``_`` are deliberately excluded — too common in ordinary text (and code) to be
# reliable signals — so plain prose isn't misread as Markdown.
_MD_RE = re.compile(
    r"^\s{0,3}#{1,6}\s"          # ATX heading
    r"|\*\*.+?\*\*"             # **bold**
    r"|`[^`]+`"                 # `inline code`
    r"|^\s*```"                 # ``` fenced code
    r"|\[.+?\]\(.+?\)"         # [text](link)
    r"|^\s{0,3}[-*+]\s+\S"      # - bullet list
    r"|^\s{0,3}\d+\.\s+\S"      # 1. ordered list
    r"|^\s{0,3}>\s"            # > blockquote
    r"|^\|.+\|",               # | table | row |
    re.MULTILINE,
)


def _looks_like_markdown(s):
    return bool(_MD_RE.search(s))


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
