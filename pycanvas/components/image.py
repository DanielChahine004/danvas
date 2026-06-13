"""Image: show a static image on the canvas.

Accepts a file path, http(s)/data URL, raw image bytes, a Matplotlib figure or
axes, a PIL image, or a NumPy array. (For a *stream* of frames use VideoFeed.)
Everything is duck-typed, so none of NumPy/PIL/Matplotlib is a hard dependency —
each is only needed if you actually pass that kind of object.
"""

import base64
import io
import sys

from .react import React

# Native React panel (not an iframe) so a vector/SVG or high-resolution image
# stays sharp when the canvas is zoomed — an iframe is rasterised then scaled.
# Scoped under `.pc-img`; the image is centred and never upscaled past natural
# size (``max-*:100%``), with ``object-fit`` deciding contain vs cover.
_IMG_CSS = """
.pc-img{width:100%;height:100%;box-sizing:border-box;display:flex;
 align-items:center;justify-content:center;background:#0b0f17}
.pc-img img{max-width:100%;max-height:100%;display:block}
"""

_IMG_SOURCE = """
function Component({ props }) {
  return (
    <div className="pc-img">
      <style>{`__CSS__`}</style>
      {props.src
        ? <img src={props.src} alt="" style={{ objectFit: props.fit || "contain" }} />
        : null}
    </div>
  );
}
""".replace("__CSS__", _IMG_CSS)


class Image(React):
    default_w = 420
    default_h = 320

    def __init__(self, src, name="image", label=None, w=None, h=None,
                 fit="contain"):
        # ``fit`` is the CSS object-fit: "contain" (default, whole image) or
        # "cover" (fill, cropping overflow).
        self._fit = fit
        super().__init__(source=_IMG_SOURCE, name=name, label=label, w=w, h=h,
                         props={"src": _to_data_uri(src), "fit": fit})

    def update(self, src):
        """Replace the image, live (the ``src`` prop swaps — no shape reload).

        A Matplotlib figure is auto-released from pyplot's registry after
        rendering, so calling this in a loop with fresh figures doesn't leak —
        no manual ``plt.close()`` needed.
        """
        super().update(src=_to_data_uri(src))


def _to_data_uri(src):
    """Coerce a supported image source into a ``src=`` string (URL or data URI)."""
    # String: a URL / data URI passes through; otherwise a local file path.
    if isinstance(src, str):
        if src.startswith(("http://", "https://", "data:")):
            return src
        with open(src, "rb") as f:
            return _bytes_uri(f.read())
    if isinstance(src, (bytes, bytearray, memoryview)):
        return _bytes_uri(bytes(src))
    # Matplotlib axes -> its figure; figure -> savefig to PNG.
    fig = getattr(src, "get_figure", None)
    if callable(fig):
        src = fig()
    if hasattr(src, "savefig"):
        buf = io.BytesIO()
        src.savefig(buf, format="png", bbox_inches="tight")
        # Release the figure from pyplot's global registry, which would
        # otherwise keep every figure alive — a leak when update(fig) runs in a
        # loop. The figure object itself stays usable (savefig still works).
        plt = sys.modules.get("matplotlib.pyplot")
        if plt is not None:
            plt.close(src)
        return _bytes_uri(buf.getvalue(), "image/png")
    # Anything exposing the IPython PNG hook (e.g. some plotting objects).
    png = getattr(src, "_repr_png_", None)
    if callable(png):
        data = png()
        if data:
            return _bytes_uri(data if isinstance(data, (bytes, bytearray))
                              else base64.b64decode(data), "image/png")
    # PIL image: has save() and a mode.
    if hasattr(src, "save") and hasattr(src, "mode"):
        buf = io.BytesIO()
        src.save(buf, format="PNG")
        return _bytes_uri(buf.getvalue(), "image/png")
    # NumPy array: encode via PIL if available.
    if hasattr(src, "__array_interface__") or (
        hasattr(src, "shape") and hasattr(src, "dtype")
    ):
        try:
            # Via importlib so PyInstaller's analysis doesn't follow it and pull
            # Pillow (and, through PIL._typing, numpy) into a baked app that
            # never renders an array image; bake() bundles Pillow when an Image
            # component is on the canvas (see pycanvas/bake.py).
            import importlib

            _PILImage = importlib.import_module("PIL.Image")
        except Exception as exc:  # pragma: no cover - depends on env
            raise ValueError(
                "showing a NumPy array as an image needs Pillow "
                "(pip install pillow)"
            ) from exc
        buf = io.BytesIO()
        _PILImage.fromarray(src).save(buf, format="PNG")
        return _bytes_uri(buf.getvalue(), "image/png")
    raise TypeError(f"can't render {type(src).__name__} as an image")


def _bytes_uri(data, mime=None):
    """Base64 ``data`` into a data URI, sniffing the MIME type when not given."""
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime or _sniff(data)};base64,{b64}"


def _sniff(data):
    """Best-effort image MIME from magic bytes (defaults to PNG)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.lstrip()[:4] == b"<svg" or data[:5] == b"<?xml":
        return "image/svg+xml"
    return "image/png"
