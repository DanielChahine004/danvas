"""Slider: a bidirectional numeric input, rendered as a native React panel.

Replaces the bespoke ``Slider`` shape with a small React component mounted by
ReactHost. The thumb is owned by the browser (a ``useState`` controlled input)
so dragging is smooth even though Python doesn't echo each value back; Python
pushes only override it (``update``) or read the settled value (``on_change``).
"""

from . import _theme
from .base import _ValuePersist
from .react import React

_SLIDER_CSS = """
.pc-slider{box-sizing:border-box;width:100%;height:100%;padding:4px 12px 8px;
 display:flex;gap:10px;align-items:center;
 font:600 12px system-ui,-apple-system,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-slider input[type=range]{flex:1;min-width:0;accent-color:var(--pc-accent,#3b82f6)}
.pc-slider .val{width:5ch;text-align:center;font-variant-numeric:tabular-nums;
 background:none;border:none;border-bottom:1px solid transparent;color:inherit;
 font:inherit;padding:0;cursor:text;outline:none}
.pc-slider .val:hover{border-bottom-color:rgba(255,255,255,.25)}
.pc-slider .val:focus{border-bottom-color:var(--pc-accent,#3b82f6);
 box-shadow:0 2px 0 var(--pc-accent-t,rgba(59,130,246,.3))}
"""

# Controlled range input: local state tracks the thumb live, a Python ``push``
# (the ``value`` prop) overrides it, and drags emit ``canvas.send({value})`` —
# continuously, or once on release when ``props.on_release``. ``props.value``
# (replayed on reconnect) falls back to ``props.default``.
# The numeric label is a typeable input: blur or Enter commits, clamping to
# [min, max] and snapping to the nearest step multiple.
from . import _jsx

_SLIDER_SOURCE = _jsx.load("slider").replace("__CSS__", _SLIDER_CSS)


class Slider(_ValuePersist, React):
    # Language-neutral contract (rendered into templates/components.json):
    # what an SDK sets/streams and what this panel emits. PROTOCOL.md explains.
    CONTRACT = {
        "data": {"min": "number", "max": "number", "value": "number",
                 "step": "number|null", "default": "number",
                 "on_release": "bool -- fire input only when the drag ends"},
        "updates": {"data_patch": "merge changed data fields",
                    "post": "the current value (number)"},
        "events": [{"value": "number"}],
    }
    default_w = 240
    default_h = 72

    def __init__(self, name="slider", min=0, max=100, default=None, step=1,
                 on_release=False, color=None, label=None):
        if default is None:
            default = min
        # ``step`` controls granularity and signals int vs. float (a fractional
        # step makes a float slider). ``on_release``: when False (default) every
        # drag step reports; when True only the settled value is sent on release.
        super().__init__(source=_SLIDER_SOURCE, name=name, label=label,
                         props={"min": min, "max": max, "step": step,
                                "default": default, "value": default,
                                "on_release": on_release,
                                "_th": _theme.derive(color) if color is not None else {}})
        self._value = default
        self._init_color(color)

    @property
    def min(self):
        return self._data.get("min")

    @min.setter
    def min(self, value):
        super().update(min=value)

    @property
    def max(self):
        return self._data.get("max")

    @max.setter
    def max(self, value):
        super().update(max=value)

    @property
    def step(self):
        return self._data.get("step")

    @step.setter
    def step(self, value):
        super().update(step=value)

    def update(self, value):
        """Push a new value to the slider in the browser, live.

        Streams in over the push channel (the ``value`` prop), and keeps the
        baked prop current so a reconnecting client replays the latest value.
        """
        with self._lock:
            self._value = value
        self._data["value"] = value
        self.push(value)

    def state_payload(self):
        v = self._value
        return {"post": v} if v is not None else None

    def _handle_input(self, payload, viewer=None):
        if "value" in payload:
            with self._lock:
                self._value = payload["value"]
            self._data["value"] = self._value  # keep baked props current for reconnects
        self._dispatch_callbacks(self._callbacks, (self.value,), viewer)
