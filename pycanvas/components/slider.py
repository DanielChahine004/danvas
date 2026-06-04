"""Slider: a bidirectional numeric input."""

from .base import BaseComponent


class Slider(BaseComponent):
    component = "Slider"

    def __init__(self, label, min=0, max=100, default=None):
        if default is None:
            default = min
        super().__init__(label=label, min=min, max=max, value=default)
        self._value = default

    def update(self, value):
        """Push a new value to the slider in the browser."""
        with self._lock:
            self._value = value
        self._send_update({"value": value})

    def state_payload(self):
        return {"value": self.value}
