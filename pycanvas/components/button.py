"""Button: a momentary action trigger, rendered as a native React panel.

The native counterpart to the old bespoke ``Button`` shape: instead of a
dedicated frontend renderer, the button *is* a small React component mounted by
ReactHost (so it follows the canvas theme through the ``--pc-*`` variables and
talks to Python directly). Register handlers with ``@button.on_click``; they're
called with no arguments. ``value`` reads the running click count.
"""

import traceback

from .react import React

# Scoped under `.pc-button`; colours follow the canvas theme with safe fallbacks.
_BUTTON_CSS = """
.pc-button{box-sizing:border-box;width:100%;height:100%;padding:8px 12px;
 font:600 13px system-ui,-apple-system,sans-serif;color:var(--pc-text,#e6edf3);
 background:var(--pc-surface,#1b2230);border:1px solid var(--pc-border,#30363d);
 border-radius:8px;cursor:pointer;transition:background .12s}
.pc-button:hover{background:var(--pc-surface-hover,#232c3d)}
.pc-button:active{transform:translateY(1px)}
"""

# Each click is a bare ``canvas.send({})`` — Python counts them and fires the
# registered handlers. ``props.text`` is the face (replayed on reconnect).
_BUTTON_SOURCE = """
function Component({ canvas, props }) {
  return (
    <>
      <style>{`__CSS__`}</style>
      <button className="pc-button" onClick={() => canvas.send({})}>
        {props.text}
      </button>
    </>
  );
}
""".replace("__CSS__", _BUTTON_CSS)


class Button(React):
    """A clickable button that fires its handlers each time it's pressed.

    Unlike :class:`~pycanvas.Toggle`, it holds no selectable value — it's a
    one-shot action. ``value`` reads the running click count.
    """

    default_w = 200
    default_h = 84

    def __init__(self, name, text=None, label=None):
        # ``text`` is the button face; it defaults to the label/name so naming the
        # button is enough to caption it.
        caption = text if text is not None else (label if label is not None else name)
        super().__init__(source=_BUTTON_SOURCE, name=name, label=label,
                         props={"text": caption})
        self._value = 0  # number of clicks seen

    def update(self, text):
        """Change the button's face text, live (e.g. Start ⇄ Pause).

        Stored on the panel (as a prop) so a reconnecting client replays the
        current face.
        """
        super().update(text=text)

    def on_click(self, fn):
        """Decorator: register a handler fired (with no args) on each click."""
        self._callbacks.append(fn)
        return fn

    def _handle_input(self, _payload, viewer=None):
        with self._lock:
            self._value = (self._value or 0) + 1
        self._dispatch_callbacks(self._callbacks, (), viewer)
