"""TextField: a text-entry input, rendered as a native React panel.

Single-line (default) or multiline (``multiline=True``). Callbacks receive the
committed value â€” on Enter-key or focus-loss for single-line, on focus-loss for
multiline. ``update(value)`` pushes new text to the browser live.
"""

from . import _theme
from .base import _mark_dedicated, _mark_threaded
from .react import React

_FIELD_CSS = """
.pc-field{box-sizing:border-box;width:100%;height:100%;padding:10px 12px;
 display:flex;align-items:stretch}
.pc-field input,.pc-field textarea{flex:1;min-height:0;min-width:0;
 box-sizing:border-box;padding:6px 8px;
 background:var(--pc-input-bg,#ffffff);border:1px solid var(--pc-border,#e2e2e2);
 border-radius:6px;color:var(--pc-text,#e6edf3);
 font:13px system-ui,-apple-system,sans-serif;resize:none;outline:none}
.pc-field input:focus,.pc-field textarea:focus{
 border-color:var(--pc-accent,#3b82f6);
 box-shadow:0 0 0 3px var(--pc-accent-t,rgba(59,130,246,.25))}
.pc-field textarea{resize:vertical}
"""

# Single-line: fires on Enter (then blurs the field) and on blur.
# Multiline: fires on blur only (Enter inserts a newline).
# Both modes keep local state so typing is always smooth, independent of Python.
_FIELD_SOURCE = """
function Component({ canvas, value, props }) {
  const initial = value != null ? value : (props.value != null ? props.value : "");
  const [text, setText] = React.useState(initial);
  React.useEffect(() => { if (value != null) setText(value); }, [value]);
  function commit(v) { canvas.send({ value: v }); }
  const _th = props._th || {};
  if (props.multiline) {
    return (
      <>
        <style>{`__CSS__`}</style>
        <div className="pc-field" style={_th}>
          <textarea placeholder={props.placeholder || ""}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onBlur={(e) => commit(e.target.value)} />
        </div>
      </>
    );
  }
  return (
    <>
      <style>{`__CSS__`}</style>
      <div className="pc-field" style={_th}>
        <input type="text" placeholder={props.placeholder || ""}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { commit(e.target.value); e.target.blur(); }
          }} />
      </div>
    </>
  );
}
""".replace("__CSS__", _FIELD_CSS)


class TextField(React):
    """A text-entry field that notifies Python when the user commits a value.

    Single-line (default): fires ``on_change`` on Enter or when the field loses
    focus. Multiline: fires on focus-loss. ``value`` reads the last committed text.
    """

    default_w = 240
    default_h = 80

    def __init__(self, name, placeholder="", default="", multiline=False,
                 color=None, label=None):
        super().__init__(source=_FIELD_SOURCE, name=name, label=label,
                         props={"value": default, "placeholder": placeholder,
                                "multiline": bool(multiline),
                                "_th": _theme.derive(color) if color is not None else {}})
        self._value = default
        self._frame_color = _theme.accent_hex(color) if color is not None else None

    @property
    def placeholder(self):
        return self._data.get("placeholder", "")

    @placeholder.setter
    def placeholder(self, value):
        super().update(placeholder=value)

    def update(self, value):
        """Push a new text value to the field in the browser, live.

        Streams over the push channel so the field updates without a full
        re-mount, and keeps the baked prop current for reconnecting clients.
        """
        with self._lock:
            self._value = value
        self._data["value"] = value
        self.push(value)

    def state_payload(self):
        v = self._value
        return {"post": v} if v is not None else None

    def on_change(self, fn=None, *, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: called with the committed text each time the user submits.

        See :meth:`on_change <danvas.components.base.BaseComponent.on_change>`
        for the full ``threaded`` / ``dedicated`` / ``queue`` semantics.
        ``threaded`` and ``dedicated`` are mutually exclusive.
        """
        if threaded and dedicated:
            raise ValueError("threaded and dedicated are mutually exclusive")
        def register(f):
            if dedicated:
                self._callbacks.append(_mark_dedicated(f, queue))
            elif threaded:
                self._callbacks.append(_mark_threaded(f))
            else:
                self._callbacks.append(f)
            return f
        return register(fn) if fn is not None else register

    def _handle_input(self, payload, viewer=None):
        if "value" in payload:
            with self._lock:
                self._value = payload["value"]
            self._data["value"] = self._value
        self._dispatch_callbacks(self._callbacks, (self.value,), viewer)
