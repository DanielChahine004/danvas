"""Label: an output-only text/number display, rendered as a native React panel.

Native (not a sandboxed iframe) so the text stays sharp at every zoom level and
follows the canvas theme directly through the ``--pc-*`` variables. A label
**defaults to ``h="auto"``** (its content is a short line, so the panel fits its
height to the text) unless the caller pins an explicit ``h``. Live updates stream
in via :meth:`push` — the value prop swaps, so React re-renders just the text
node (no shape-prop churn, fine for a status line updated every loop iteration).
"""

from . import _theme
from .react import React

# Scoped under `.pc-label`; the text colour follows the canvas theme.
_LABEL_CSS = """
.pc-label{box-sizing:border-box;width:100%;padding:8px;font-weight:600;
 font-family:system-ui,-apple-system,sans-serif;font-size:13px;line-height:1.5;
 color:var(--pc-accent,var(--pc-text));white-space:pre-wrap;word-break:break-word}
"""

# Renders the latest pushed value (``value``), falling back to the initial value
# carried in ``props.text`` (also what a reconnecting client replays). Written as
# a plain string so its JSX braces survive; only __CSS__ is substituted.
_LABEL_SOURCE = """
function Component({ value, props }) {
  const text = value != null ? value : (props.text != null ? props.text : "");
  const _th = props._th || {};
  return (
    <>
      <style>{`__CSS__`}</style>
      <div className="pc-label" style={_th}>{text}</div>
    </>
  );
}
""".replace("__CSS__", _LABEL_CSS)


def _str(v):
    return "" if v is None else str(v)


class Label(React):
    default_w = 240
    default_h = 84

    def __init__(self, name="label", value="", color=None, label=None, w=None, h=None):
        super().__init__(source=_LABEL_SOURCE, name=name, label=label, w=w, h=h,
                         props={"text": _str(value),
                                "_th": _theme.derive(color) if color is not None else {}})
        self._value = value
        self._init_color(color)
        # A label holds a short line or number, so default to fitting the panel
        # to its content (no tall, mostly-empty box) unless the caller pinned an
        # explicit height. ``insert`` honours this flag for layout/placement too.
        if h is None:
            self._auto_h = True

    def update(self, value):
        """Push a new string/number to display (live, without re-rendering the
        shape). The value streams in over the push channel; the baked prop is kept
        current too, so a client connecting after this update shows the latest
        value rather than the one passed at construction."""
        self._value = value
        self._data["text"] = _str(value)
        self.push(_str(value))
