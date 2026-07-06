"""Chat: a shared message panel for everyone viewing the canvas.

Unlike the data-driven panels, chat flows *between viewers* — the server stamps
each line with the sender's identity (see :class:`~danvas.bridge.Bridge`) and
relays it to every browser. This component is just a window onto that shared
room, so multiple Chat panels (or panels on a merged view) all show the same
conversation. Viewers edit their own display name right in the panel.

Rendered as a native React panel (mounted by ReactHost): the JSX subscribes to
the canvas-wide chat channel via ``canvas.chat`` — distinct from the per-panel
``canvas.send`` controls use, because the room (identity, history, relay) is
shared by every viewer, not state of this one panel. The Python side
(:meth:`post` / :meth:`on_message`) talks to the bridge's chat room directly and
is unchanged by where the panel renders.

    chat = canvas.chat("chat")
    chat.post("welcome, everyone")        # send a line as the host
    @chat.on_message
    def log(entry):                        # observe every line from Python
        print(entry["name"], ":", entry["text"])
"""

from . import _theme
from .react import React

# Port of the former native ChatShapeUtil view, driven by ``canvas.chat`` instead
# of importing the bridge directly. Theme colours come from the canvas ``--pc-*``
# variables (the panel mounts natively, so they resolve). Written as a plain
# string so its JSX braces survive — nothing is substituted.
from . import _jsx

_CHAT_SOURCE = _jsx.load("chat")


class Chat(React):
    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {},
        "updates": {},
        "events": [],
        "note": "hub-native: the shared room rides chat/set_name frames, "
                "not per-panel updates",
    }
    default_w = 320
    default_h = 400

    def __init__(self, name="chat", label=None, color=None):
        super().__init__(source=_CHAT_SOURCE, name=name, label=label,
                         props={"_th": _theme.derive(color) if color is not None else {}})
        self._init_color(color)
        # Chat observers registered (possibly) before the bridge is attached;
        # they're handed to the bridge as sinks at bind time.
        self._chat_callbacks = []

    def _bind(self, component_id, bridge):
        super()._bind(component_id, bridge)
        for cb in self._chat_callbacks:
            bridge.add_chat_sink(cb)

    def _on_removed(self):
        if self._bridge is not None:
            for cb in self._chat_callbacks:
                self._bridge.remove_chat_sink(cb)

    def post(self, text, name="host", color="#64748b"):
        """Send a chat line from Python (a host/system announcement)."""
        if self._bridge is not None:
            self._bridge.post_chat(text, name=name, color=color)

    def on_message(self, fn=None, *, threaded=False, dedicated=False, queue="fifo"):
        """Decorator: register a callback fired with every chat entry (a dict of
        ``id``/``name``/``color``/``text``/``ts``).

        See :meth:`on_change <danvas.components.base.BaseComponent.on_change>`
        for the full ``threaded`` / ``dedicated`` / ``queue`` semantics.
        ``threaded`` and ``dedicated`` are mutually exclusive.
        """
        def register(f):
            self._register_callback(self._chat_callbacks, f, threaded, dedicated, queue)
            sink = self._chat_callbacks[-1]
            if self._bridge is not None:
                self._bridge.add_chat_sink(sink)
            return f
        return register(fn) if fn is not None else register