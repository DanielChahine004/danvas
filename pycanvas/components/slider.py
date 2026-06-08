"""Slider: a bidirectional numeric input."""

from .base import BaseComponent


class Slider(BaseComponent):
    component = "Slider"

    def __init__(self, name, min=0, max=100, default=None, step=1,
                 debounce=0, label=None):
        if default is None:
            default = min
        # ``step`` controls the slider's granularity *and* signals an int vs.
        # float slider: an integer step keeps values ints (e.g. servo angles),
        # while a fractional step like ``0.1`` makes it a float slider. It also
        # drives the precision of the manual number-entry box in the browser.
        #
        # ``debounce`` (milliseconds) rate-limits how often a *drag* reports back
        # to Python: the thumb still moves live in the browser, but ``on_change``
        # fires at most once per window (plus a final settled value), so a frantic
        # drag can't flood the socket. 0 (default) reports every change.
        super().__init__(name=name, label=label, min=min, max=max, step=step,
                         debounce=debounce, value=default)
        self._value = default

    def update(self, value):
        """Push a new value to the slider in the browser."""
        with self._lock:
            self._value = value
        self._send_update({"value": value})

    def state_payload(self):
        return {"value": self.value}
