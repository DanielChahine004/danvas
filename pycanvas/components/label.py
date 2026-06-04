"""Label: an output-only text/number display."""

from .base import BaseComponent


class Label(BaseComponent):
    component = "Label"
    default_w = 240
    default_h = 84

    def __init__(self, label, value=""):
        super().__init__(label=label, value=str(value))
        self._value = value

    def update(self, value):
        """Push a new string/number to display."""
        with self._lock:
            self._value = value
        self._send_update({"value": str(value)})

    def state_payload(self):
        return {"value": str(self.value)}
