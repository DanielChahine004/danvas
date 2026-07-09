"""Toggle: pick one of N string options (bidirectional), as a native React panel.

Replaces the bespoke ``Toggle`` shape with a small React segmented control
mounted by ReactHost. Selection lives in local state (so it highlights instantly
on click) and is mirrored to Python via ``canvas.send({value})``; a Python
``update`` overrides it.
"""

from . import _theme
from .base import _ValuePersist
from .react import React

_TOGGLE_CSS = """
.pc-toggle{box-sizing:border-box;width:100%;height:100%;padding:10px 12px;
 display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.pc-toggle button{flex:1;min-width:0;padding:7px 10px;cursor:pointer;
 font:600 12px system-ui,-apple-system,sans-serif;color:var(--pc-off-text,#333333);
 background:var(--pc-off-bg,#eeeeee);border:1px solid var(--pc-border,#30363d);
 border-radius:7px;transition:background .12s}
.pc-toggle button.sel{background:var(--pc-accent,#3b82f6);border-color:transparent;
 color:var(--pc-accent-text,#fff)}
"""

# Segmented control: one button per option, the selected one styled ``.sel``.
# ``props.value`` (replayed on reconnect) seeds local state; a Python push (the
# ``value`` prop) overrides it.
from . import _jsx

_TOGGLE_SOURCE = _jsx.load("toggle").replace("__CSS__", _TOGGLE_CSS)


def _split_toggle_args(args, options, name):
    """Resolve Toggle's flexible positionals: a positional string is the
    name, a positional list/tuple is the options — either order."""
    if len(args) > 2:
        raise TypeError(
            f"Toggle takes at most two positional arguments ({len(args)} given)")
    for a in args:
        if isinstance(a, str):
            name = a
        elif a is not None:
            if options is not None:
                raise TypeError("Toggle got multiple values for its options")
            options = a
    if options is None:
        raise ValueError(
            'Toggle requires its options, e.g. Toggle(["a", "b"]) or '
            'Toggle("mode", ["a", "b"])')
    return options, name


class Toggle(_ValuePersist, React):
    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {"options": "list[str]", "value": "str"},
        "updates": {"data_patch": "merge changed data fields",
                    "post": "the new value (str)"},
        "events": [{"value": "str"}],
    }
    default_w = 260
    default_h = 84

    def __init__(self, *args, options=None, name="toggle", default=None,
                 color=None, label=None):
        # Every call order works: ``Toggle(["a", "b"])`` (the documented
        # content-first form), ``Toggle("mode", ["a", "b"])`` /
        # ``Toggle("mode", options=[...])`` (name-first — the shape fingers
        # trained on Slider keep producing), and the old
        # ``Toggle(["a", "b"], "mode")``. A positional string is the name;
        # a positional list/tuple is the options.
        options, name = _split_toggle_args(args, options, name)
        options = list(options)
        if not options:
            raise ValueError("Toggle requires at least one option")
        if default is None:
            default = options[0]
        super().__init__(source=_TOGGLE_SOURCE, name=name, label=label,
                         props={"options": options, "value": default,
                                "_th": _theme.derive(color) if color is not None else {}})
        self._value = default
        self._init_color(color)

    @property
    def options(self):
        return list(self._data.get("options", []))

    @options.setter
    def options(self, value):
        opts = list(value)
        if not opts:
            raise ValueError("Toggle requires at least one option")
        super().update(options=opts)

    def update(self, value):
        """Push a new selected option to the browser, live (and persist it)."""
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
            self._data["value"] = self._value
        self._dispatch_callbacks(self._callbacks, (self.value,), viewer)
