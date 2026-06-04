"""Toggle: pick one of N string options (bidirectional)."""

from .base import BaseComponent


class Toggle(BaseComponent):
    component = "Toggle"
    default_w = 260
    default_h = 84

    def __init__(self, label, options, default=None):
        options = list(options)
        if not options:
            raise ValueError("Toggle requires at least one option")
        if default is None:
            default = options[0]
        super().__init__(label=label, options=options, value=default)
        self._value = default

    def update(self, value):
        """Push a new selected option to the browser."""
        with self._lock:
            self._value = value
        self._send_update({"value": value})

    def state_payload(self):
        return {"value": self.value}
