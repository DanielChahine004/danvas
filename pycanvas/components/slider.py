"""Slider: a bidirectional numeric input, rendered as a native React panel.

Replaces the bespoke ``Slider`` shape with a small React component mounted by
ReactHost. The thumb is owned by the browser (a ``useState`` controlled input)
so dragging is smooth even though Python doesn't echo each value back; Python
pushes only override it (``update``) or read the settled value (``on_change``).
"""

from .react import React

_SLIDER_CSS = """
.pc-slider{box-sizing:border-box;width:100%;height:100%;padding:10px 12px;
 display:flex;gap:10px;align-items:center;
 font:600 12px system-ui,-apple-system,sans-serif;color:var(--pc-text,#e6edf3)}
.pc-slider input[type=range]{flex:1;min-width:0;accent-color:var(--pc-accent,#3b82f6)}
.pc-slider .val{min-width:4ch;text-align:right;font-variant-numeric:tabular-nums}
"""

# Controlled range input: local state tracks the thumb live, a Python ``push``
# (the ``value`` prop) overrides it, and drags emit ``canvas.send({value})`` —
# continuously, or once on release when ``props.on_release``. ``props.value``
# (replayed on reconnect) falls back to ``props.default``.
_SLIDER_SOURCE = """
function Component({ canvas, value, props }) {
  const initial = value != null ? value
                : (props.value != null ? props.value : props.default);
  const [v, setV] = React.useState(initial);
  React.useEffect(() => { if (value != null) setV(value); }, [value]);
  const onRelease = props.on_release;
  const isFloat = String(props.step).indexOf(".") >= 0;
  const show = isFloat ? Number(v).toFixed(2) : v;
  return (
    <>
      <style>{`__CSS__`}</style>
      <div className="pc-slider">
        <input type="range" min={props.min} max={props.max} step={props.step}
          value={v}
          onChange={(e) => {
            const n = Number(e.target.value);
            setV(n);
            if (!onRelease) canvas.send({ value: n });
          }}
          onPointerUp={onRelease
            ? (e) => canvas.send({ value: Number(e.target.value) })
            : undefined} />
        <span className="val">{show}</span>
      </div>
    </>
  );
}
""".replace("__CSS__", _SLIDER_CSS)


class Slider(React):
    default_w = 240
    default_h = 96

    def __init__(self, name, min=0, max=100, default=None, step=1,
                 on_release=False, label=None):
        if default is None:
            default = min
        # ``step`` controls granularity and signals int vs. float (a fractional
        # step makes a float slider). ``on_release``: when False (default) every
        # drag step reports; when True only the settled value is sent on release.
        super().__init__(source=_SLIDER_SOURCE, name=name, label=label,
                         props={"min": min, "max": max, "step": step,
                                "default": default, "value": default,
                                "on_release": on_release})
        self._value = default

    def update(self, value):
        """Push a new value to the slider in the browser, live.

        Streams in over the push channel (the ``value`` prop), and keeps the
        baked prop current so a reconnecting client replays the latest value.
        """
        with self._lock:
            self._value = value
        self._data["value"] = value
        self.push(value)

    def _handle_input(self, payload):
        if "value" in payload:
            with self._lock:
                self._value = payload["value"]
        for cb in self._callbacks:
            try:
                cb(self.value)
            except Exception:
                import traceback
                traceback.print_exc()
