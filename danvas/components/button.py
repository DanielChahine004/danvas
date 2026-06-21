"""Button: a momentary action trigger, rendered as a native React panel.

The native counterpart to the old bespoke ``Button`` shape: instead of a
dedicated frontend renderer, the button *is* a small React component mounted by
ReactHost (so it follows the canvas theme through the ``--pc-*`` variables and
talks to Python directly). Register handlers with ``@button.on_click``; they're
called with no arguments. ``value`` reads the running click count.
"""

import traceback

from . import _theme
from .react import React

# Scoped under `.pc-button`; when a color theme is set via --pc-accent the button
# renders in that colour; otherwise falls back to the neutral surface palette.
_BUTTON_CSS = """
.pc-button{box-sizing:border-box;width:100%;height:100%;padding:8px 12px;
 font:600 13px system-ui,-apple-system,sans-serif;
 color:var(--pc-accent-text,var(--pc-text,#e6edf3));
 background:var(--pc-accent,var(--pc-surface,#1b2230));
 border:1px solid var(--pc-accent,var(--pc-border,#30363d));
 border-radius:8px;cursor:pointer;transition:background .12s,border-color .12s}
.pc-button:hover{background:var(--pc-accent-dk,var(--pc-surface-hover,#232c3d));
 border-color:var(--pc-accent-dk,var(--pc-border,#30363d))}
.pc-button:active{transform:translateY(1px)}
"""

# Each click is a bare ``canvas.send({})`` — Python counts them and fires the
# registered handlers. ``props.text`` is the face (replayed on reconnect).
_BUTTON_SOURCE = """
function Component({ canvas, props }) {
  const _th = props._th || {};
  return (
    <>
      <style>{`__CSS__`}</style>
      <button className="pc-button" style={_th} onClick={() => canvas.send({})}>
        {props.text}
      </button>
    </>
  );
}
""".replace("__CSS__", _BUTTON_CSS)


class Button(React):
    """A clickable button that fires its handlers each time it's pressed.

    Unlike :class:`~danvas.Toggle`, it holds no selectable value — it's a
    one-shot action. ``value`` reads the running click count.
    """

    default_w = 200
    default_h = 84

    def __init__(self, name, text=None, color=None, label=None):
        # ``text`` is the button face; it defaults to the label/name so naming the
        # button is enough to caption it.
        caption = text if text is not None else (label if label is not None else name)
        super().__init__(source=_BUTTON_SOURCE, name=name, label=label,
                         props={"text": caption,
                                "_th": _theme.derive(color) if color is not None else {}})
        self._value = 0  # number of clicks seen
        self._init_color(color)

    @property
    def text(self):
        """The button's face label; settable live."""
        return self._data.get("text", "")

    @text.setter
    def text(self, value):
        self.update(value)

    def update(self, text):
        """Change the button's face text, live (e.g. Start ⇄ Pause).

        Stored on the panel (as a prop) so a reconnecting client replays the
        current face.
        """
        super().update(text=text)

    def on_click(self, fn=None, *, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: register a handler fired (with no args) on each click.

        See :meth:`on_change <danvas.components.base.BaseComponent.on_change>`
        for the full ``threaded`` / ``dedicated`` / ``queue`` semantics.
        ``threaded`` and ``dedicated`` are mutually exclusive.
        """
        def register(f):
            return self._register_callback(self._callbacks, f, threaded, dedicated, queue)
        return register(fn) if fn is not None else register

    def _handle_input(self, _payload, viewer=None):
        with self._lock:
            self._value = (self._value or 0) + 1
        self._dispatch_callbacks(self._callbacks, (), viewer)