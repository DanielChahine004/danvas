"""Button: a momentary action trigger (fires a callback on each click)."""

import traceback

from .base import BaseComponent


class Button(BaseComponent):
    """A clickable button that fires its handlers each time it's pressed.

    Unlike :class:`~pycanvas.Toggle`, it holds no selectable value — it's a
    one-shot action. Register handlers with ``@button.on_click``; they're called
    with no arguments. ``value`` reads the running click count.
    """

    component = "Button"
    default_w = 200
    default_h = 84

    def __init__(self, name, text=None, label=None):
        # ``text`` is the button face; it defaults to the label/name so naming the
        # button is enough to caption it.
        caption = text if text is not None else (label if label is not None else name)
        super().__init__(name=name, label=label, text=caption)
        self._value = 0  # number of clicks seen

    def update(self, text):
        """Change the button's face text, live (e.g. Start ⇄ Pause).

        Stored on the panel so a reconnecting client replays the current face.
        """
        self._props["text"] = text
        self._send_update({"text": text})

    def on_click(self, fn):
        """Decorator: register a handler fired (with no args) on each click."""
        self._callbacks.append(fn)
        return fn

    def _handle_input(self, _payload):
        with self._lock:
            self._value = (self._value or 0) + 1
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                traceback.print_exc()
