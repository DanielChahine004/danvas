"""Turn an arbitrary Python value into the right canvas panel.

:func:`panel_for` is the shared brain behind ``Canvas.show(value)`` and the
notebook cell-capture (:mod:`danvas.autopanel`). It inspects a value and picks
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
import inspect
import json
import os
import re
import struct
from urllib.parse import unquote as _unquote

from .components import BaseComponent, Custom, Image, Label, Markdown, Plot, Table
from .components._doc import document


def _filtered_kw(cls, kw):
    """Return the subset of ``kw`` that ``cls.__init__`` explicitly accepts.

    Prevents unknown kwargs from crashing component constructors while still
    letting callers pass component-specific options to ``panel_for`` without
    knowing in advance which component will be chosen.
    """
    if not kw:
        return {}
    try:
        sig = inspect.signature(cls.__init__)
        params = set(sig.parameters) - {"self"}
        has_var_kw = any(
            p.kind is inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        return dict(kw) if has_var_kw else {k: v for k, v in kw.items() if k in params}
    except Exception:
        return {}


def panel_for(value, name="panel", label=None, w=None, h=None,
              formatter=None, **comp_kw):
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
        return _image_panel(value, name, label, w, h)

    if _is_tabular(value):
        return _table_panel(value, name, label, w, h, comp_kw)

    html = _rich_html(value, formatter)
    if html is not None:
        # A notebook-style rich repr is content-bounded: fit the panel height.
        return _mark_auto(Custom(html=html, name=name, label=label, w=w, h=h))

    if isinstance(value, (dict, list, tuple, set)):
        return _mark_auto(Custom(html=_json_tree_html(value), name=name,
                                 label=label, w=w, h=h))

    # Raw bytes that look like an image (PNG/JPEG/GIF/…) render as the image;
    # anything else falls through to a repr label.
    if isinstance(value, (bytes, bytearray, memoryview)) and _is_image_bytes(value):
        return _image_panel(bytes(value), name, label, w, h)

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
        return _image_panel(s, name, label, w, h)

    # A bare web URL becomes a clickable link rather than dead text — one line,
    # so fit the panel height to it.
    if "\n" not in s and len(s) <= 2048 and s.startswith(("http://", "https://")):
        return _mark_auto(Markdown(f"[{s}]({s})", name=name, label=label,
                                   w=w, h=h))

    # Literal HTML renders as HTML (wrapped for base styling unless it's already
    # a full document); fit the panel to the content.
    if _looks_like_html(s):
        full = re.match(r"\s*<(?:!doctype|html)\b", s, re.I)
        return _mark_auto(Custom(html=s if full else document(s),
                                 name=name, label=label, w=w, h=h))

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
            return _image_panel(path, name, label, w, h)
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
    # NumPy: 1-D array → single "value" column; 2-D non-uint8 → row matrix.
    # uint8 2-D arrays are pixel data and go to the image path instead.
    if mod.startswith("numpy") and hasattr(obj, "shape"):
        shape = obj.shape
        dtype_str = str(getattr(obj, "dtype", ""))
        if len(shape) == 1:
            return True
        if len(shape) == 2 and "uint8" not in dtype_str:
            return True
        return False
    if isinstance(obj, (list, tuple)) and obj:
        if all(isinstance(x, dict) for x in obj):
            return True
        if all(isinstance(x, (list, tuple)) for x in obj):
            return True
        # Flat list of scalars → single-column table (cleaner than JSON pre).
        if all(isinstance(x, (int, float, bool, str, type(None))) for x in obj):
            return True
    # Flat dict (all scalar values) → key/value table.
    if isinstance(obj, dict) and obj and all(
            not isinstance(v, (dict, list, tuple, set)) for v in obj.values()):
        return True
    return False


def _is_image_like(obj):
    mod = type(obj).__module__ or ""
    if mod.startswith("matplotlib"):
        return hasattr(obj, "savefig") or hasattr(obj, "get_figure")
    if mod.startswith("PIL"):
        return hasattr(obj, "save") and hasattr(obj, "mode")
    if mod.startswith("numpy") and hasattr(obj, "shape"):
        shape = obj.shape
        dtype_str = str(getattr(obj, "dtype", ""))
        # H×W×{3,4} (RGB/RGBA) arrays are always image-like.
        if len(shape) == 3 and shape[2] in (3, 4):
            return True
        # 2-D: only uint8 is pixel data; float matrices are tabular data.
        if len(shape) == 2:
            return "uint8" in dtype_str
        return False
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
        # Wrap in an inline-block so the body's scrollWidth at max-content
        # reflects the content's intrinsic width, not the iframe frame width.
        # Without this, a block-level outer div fills 100% of the body and
        # fitW() echoes the current frame width rather than the content size.
        inner = _join(data["text/html"])
        return document(f"<div style='display:inline-block'>{inner}</div>")
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


# -- default sizing for auto-rendered panels ---------------------------------
# show()/dispatch builds the panel for a value, so it also picks the size. The
# defaults below fit the panel to its content instead of dropping small content
# into a big fixed box: content-bounded panels (JSON, rich repr, HTML, the URL
# link) get auto-height; tables get a height computed from their row count and
# capped; images size to their natural dimensions. Explicit canvas.table(...) /
# canvas.custom(...) are untouched — only the inferred sizes here change.

# Card/table furniture, calibrated from the rendered panel (toolbar ~35, header
# row ~43 with the per-column profile line, the always-shown distribution row
# ~44, data row ~27) plus a small buffer so a snug table doesn't show a
# scrollbar over a pixel or two.
_TBL_CHROME, _TBL_BAR, _TBL_HEAD, _TBL_DIST, _TBL_ROW = 34, 36, 46, 44, 28
_IMG_CHROME = 34  # card label bar above the image area


def _mark_auto(component):
    """Opt a content-bounded auto-rendered panel into fits-content sizing.

    ``insert`` treats a component carrying ``_auto_h=True`` as default
    auto-height: it fits the content but still yields to a grid/column slot.
    Custom (iframe) panels — the rich-repr / SVG / HTML / JSON outputs — also
    get ``_auto_w=True``, so ``show()`` sizes their *width* to the content's
    natural width (a one-shot fit at load), the way their height already fits.
    The React display panels (Markdown/Image/Table) measure differently and are
    left to their own width handling.
    """
    component._auto_h = True
    if isinstance(component, Custom):
        component._auto_w = True
    return component


_JSON_TREE_JS = r"""
!function(){
var D=document,data=%s;
var root=D.getElementById('jtroot');
root.style.cssText='font:13px/1.7 ui-monospace,monospace;padding:6px 8px;overflow:auto;box-sizing:border-box';
root.appendChild(build(data,0));
function build(v,d){
  var t=typeof v;
  if(v===null)return leaf('null','#a78bfa');
  if(t==='boolean')return leaf(String(v),'#c084fc');
  if(t==='number')return leaf(String(v),'#60a5fa');
  if(t==='string')return leaf(JSON.stringify(v),'#86efac');
  var arr=Array.isArray(v),keys=arr?[...Array(v.length).keys()]:Object.keys(v);
  if(!keys.length)return leaf(arr?'[]':'{}','#94a3b8');
  var wrap=el('span'),body=el('div');
  body.style.paddingLeft='14px';
  keys.forEach(function(k,i){
    var row=el('div');
    if(!arr){var ks=el('span');ks.textContent=JSON.stringify(k)+': ';ks.style.cssText='color:#93c5fd;font-weight:bold';row.appendChild(ks);}
    row.appendChild(build(arr?v[k]:v[k],d+1));
    if(i<keys.length-1){var cm=el('span');cm.textContent=',';cm.style.color='#64748b';row.appendChild(cm);}
    body.appendChild(row);
  });
  var o=el('span'),c=el('span'),prev=el('span');
  o.textContent=arr?'[':'{';o.style.cssText='cursor:pointer;color:#94a3b8;user-select:none';
  c.textContent=arr?']':'}';c.style.color='#94a3b8';
  prev.style.cssText='color:#475569;font-size:11px';
  function peek(){return(arr?keys.slice(0,3).map(function(k){return String(v[k]);}):keys.slice(0,3).map(function(k){return JSON.stringify(k);})).join(', ')+(keys.length>3?', …':'');}
  var col=d>0;
  function tog(c){body.style.display=c?'none':'';prev.style.display=c?'':'none';prev.textContent=' '+peek()+' ';}
  o.addEventListener('click',function(e){e.stopPropagation();col=!col;tog(col);});
  tog(col);
  wrap.appendChild(o);wrap.appendChild(prev);wrap.appendChild(body);wrap.appendChild(c);
  return wrap;
}
function leaf(t,c){var e=el('span');e.textContent=t;e.style.color=c;return e;}
function el(t){return D.createElement(t);}
}();
"""


def _json_tree_html(value):
    """A collapsible, syntax-colored JSON tree as a self-contained HTML document."""
    json_str = json.dumps(value, default=str, ensure_ascii=False)
    body = (f"<div id='jtroot'></div>"
            f"<script>{_JSON_TREE_JS % json_str}</script>")
    return document(body)


def _table_panel(value, name, label, w, h, comp_kw=None):
    """A Table sized (when no height was given) to its rows, capped at the
    component default so a huge table stays scrollable instead of giant."""
    # Normalize numpy arrays into forms Table._normalize already handles.
    mod = type(value).__module__ or ""
    if mod.startswith("numpy") and hasattr(value, "tolist"):
        shape = value.shape
        value = ({"value": value.tolist()} if len(shape) == 1
                 else value.tolist())
    # Flat list/tuple of scalars → named single column so header reads "value".
    elif isinstance(value, (list, tuple)) and value \
            and not isinstance(value[0], (dict, list, tuple)):
        value = {"value": list(value)}

    if h is None:
        try:
            # Dict-of-columns: row count is the length of the first column.
            # Flat-dict (scalar values) has len(dict) rows in the key/value layout.
            if isinstance(value, dict):
                first = next(iter(value.values()), None)
                n = max(1, len(first) if hasattr(first, "__len__") else len(value))
            else:
                n = max(1, len(value))
        except TypeError:
            n = None
        if n is not None:
            h = min(_TBL_CHROME + _TBL_BAR + _TBL_HEAD + _TBL_DIST
                    + n * _TBL_ROW + 6, Table.default_h)
    return Table(value, name=name, label=label, w=w, h=h,
                 **_filtered_kw(Table, comp_kw or {}))


def _image_panel(src, name, label, w, h):
    """An Image sized (when no size was given) to the picture's natural
    dimensions, scaled into a panel-friendly range so it neither swims in a big
    box nor overflows. Falls back to the component default when the size can't
    be determined (e.g. a remote URL)."""
    if w is None and h is None:
        dims = _image_dims(src)
        size = _image_panel_size(*dims) if dims else None
        if size:
            w, h = size[0], size[1] + _IMG_CHROME
    return Image(src, name=name, label=label, w=w, h=h)


def _image_panel_size(w, h, max_w=560, max_h=440, min_w=160):
    """Clamp a natural ``(w, h)`` into a panel-friendly box, preserving aspect."""
    if not w or not h or w <= 0 or h <= 0:
        return None
    ratio = h / w
    width = max(min_w, min(float(w), max_w))
    height = width * ratio
    if height > max_h:
        height = max_h
        width = height / ratio
    return (int(round(width)), int(round(height)))


def _image_dims(src):
    """Best-effort natural ``(width, height)`` of an image source, or ``None``.

    Covers the sources :class:`~danvas.Image` accepts — PIL, NumPy, Matplotlib,
    raw bytes, a file path, and data URIs — by reading the object's own size or
    sniffing the image header. Remote URLs return ``None`` (no fetch)."""
    try:
        # PIL image: .size + .mode.
        if hasattr(src, "size") and hasattr(src, "mode"):
            return (int(src.size[0]), int(src.size[1]))
        # Matplotlib axes -> figure -> inches * dpi.
        fig = getattr(src, "get_figure", None)
        if callable(fig):
            src = fig()
        if hasattr(src, "get_size_inches"):
            w_in, h_in = src.get_size_inches()
            dpi = getattr(src, "dpi", None) or 100
            return (int(w_in * dpi), int(h_in * dpi))
        # NumPy array: shape is (H, W[, C]).
        if hasattr(src, "shape") and hasattr(src, "dtype") and len(src.shape) >= 2:
            return (int(src.shape[1]), int(src.shape[0]))
        if isinstance(src, str):
            if src.startswith("data:"):
                head, _, payload = src.partition(",")
                if "svg" in head:
                    raw = (base64.b64decode(payload).decode("utf-8", "replace")
                           if ";base64" in head else _unquote(payload))
                    return _svg_dims(raw)
                data = (base64.b64decode(payload) if ";base64" in head
                        else _unquote(payload).encode("latin-1", "replace"))
                return _raster_dims(data)
            if src.startswith(("http://", "https://")):
                return None  # remote: size unknown without fetching
            with open(src, "rb") as f:
                head = f.read(2048)
            if head.lstrip()[:4] == b"<svg" or head[:5] == b"<?xml":
                return _svg_dims(head.decode("utf-8", "replace"))
            return _raster_dims(head)
        if isinstance(src, (bytes, bytearray, memoryview)):
            b = bytes(src)
            if b.lstrip()[:4] == b"<svg":
                return _svg_dims(b.decode("utf-8", "replace"))
            return _raster_dims(b)
    except Exception:
        return None
    return None


def _raster_dims(b):
    """``(w, h)`` from PNG / GIF / JPEG header bytes, or ``None``."""
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return tuple(struct.unpack(">II", b[16:24]))
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return tuple(struct.unpack("<HH", b[6:10]))
    if b[:2] == b"\xff\xd8":  # JPEG: walk segment markers to the start-of-frame
        i, n = 2, len(b)
        while i + 9 < n:
            if b[i] != 0xFF:
                i += 1
                continue
            marker = b[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", b[i + 5:i + 9])
                return (w, h)
            i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    return None


def _svg_dims(text):
    """``(w, h)`` from an SVG's width/height attrs, else its viewBox, else None."""
    m = re.search(r"<svg\b[^>]*>", text, re.I)
    tag = m.group(0) if m else text[:512]

    def num(name):
        mm = re.search(rf'{name}\s*=\s*["\']?\s*([\d.]+)', tag, re.I)
        return float(mm.group(1)) if mm else None

    w, h = num("width"), num("height")
    if w and h:
        return (int(w), int(h))
    vb = re.search(r'viewBox\s*=\s*["\']?\s*[-\d.]+[ ,]+[-\d.]+[ ,]+'
                   r'([\d.]+)[ ,]+([\d.]+)', tag, re.I)
    if vb:
        return (int(float(vb.group(1))), int(float(vb.group(2))))
    return None


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