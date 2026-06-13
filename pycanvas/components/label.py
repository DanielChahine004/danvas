"""Label: an output-only text/number display.

Rendered inside a sandboxed ``Custom`` iframe (rather than as a native node) so
it inherits the content-measuring machinery: a label **defaults to ``h="auto"``**
(its content is a short line, so the panel fits its height to the text and
re-fits when the value changes or the panel is narrowed) — pass an explicit
``h`` to pin it instead. Live updates stream in via :meth:`Custom.push` — the text node is
swapped in place, so the iframe is never reloaded (no flicker, fine for a status
line updated every loop iteration).

The document is built with the shared :func:`document` helper (the same
top-aligned, theme-aware page Markdown uses) rather than ``Custom.compose`` —
compose centers content in a ``min-height:100vh`` body, which on a fixed-height
label pushes the text below the panel's viewport. The live-update script rides in
the body, so no ``css``/``js`` is passed to ``Custom`` and compose is never used.
"""

import html as _html

from .custom import Custom
from ._doc import document

# document(theme=True) already supplies the theme-aware text colour and padding;
# here we only set the label's own type (a touch bolder than body prose).
_LABEL_CSS = (
    ".pc-label{font-weight:600;white-space:pre-wrap;word-break:break-word;}"
)

# Receive pushed values and swap the text in place — no reload. The ResizeObserver
# Custom arms for h="auto" then re-fits the panel height to the new text. window.canvas
# is defined by Custom._wrap's helper, which is prepended ahead of this document.
_LABEL_JS = (
    "var el=document.getElementById('pc-label');"
    "canvas.onPush(function(v){el.textContent=v;});"
)


class Label(Custom):
    component = "Custom"
    default_w = 240
    default_h = 84

    def __init__(self, name, value="", label=None, w=None, h=None):
        super().__init__(html=self._render(value), name=name, label=label,
                         w=w, h=h)
        self._value = value
        # A label holds a short line or number, so default to fitting the panel
        # to its content (no tall, mostly-empty box) unless the caller pinned an
        # explicit height. ``insert`` honours this flag for layout/placement too.
        if h is None:
            self._auto_h = True

    def register_props(self):
        # Blend into the canvas theme (transparent body, text colour following the
        # light/dark toggle) instead of rendering as a white notebook box. Same
        # hint Markdown uses.
        props = super().register_props()
        props["themed"] = True
        return props

    def update(self, value):
        """Push a new string/number to display (live, without reloading)."""
        with self._lock:
            self._value = value
            # Keep the baked document current too, so a later full render (re-insert
            # under the same name, or a client connecting after this update) shows
            # the latest value rather than the one passed at construction.
            self._html = self._render(value)
        self.push(str(value))

    @staticmethod
    def _render(value):
        body = (
            f"<div id='pc-label' class='pc-label'>{_html.escape(str(value))}</div>"
            f"<script>{_LABEL_JS}</script>"
        )
        return document(body, _LABEL_CSS, theme=True)
