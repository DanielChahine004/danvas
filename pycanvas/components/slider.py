"""Slider: a bidirectional numeric input."""

from .base import BaseComponent


class Slider(BaseComponent):
    component = "Slider"

    def __init__(self, name, min=0, max=100, default=None, step=1,
                 on_release=False, label=None):
        if default is None:
            default = min
        # ``step`` controls the slider's granularity *and* signals an int vs.
        # float slider: an integer step keeps values ints (e.g. servo angles),
        # while a fractional step like ``0.1`` makes it a float slider. It also
        # drives the precision of the manual number-entry box in the browser.
        #
        # ``on_release``: when False (default) the thumb reports every change as
        # it's dragged; when True the drag stays silent and ``on_change`` fires
        # once, with the settled value, when the user lets go — so a frantic drag
        # can't flood a slow handler. The thumb tracks the cursor live either way.
        super().__init__(name=name, label=label, min=min, max=max, step=step,
                         on_release=on_release, value=default)
        self._value = default

    def update(self, value):
        """Push a new value to the slider in the browser."""
        with self._lock:
            self._value = value
        self._send_update({"value": value})

    def state_payload(self):
        return {"value": self.value}
