"""Chat: a shared message panel for everyone viewing the canvas.

Unlike the data-driven panels, chat flows *between viewers* — the server stamps
each line with the sender's identity (see :class:`~pycanvas.bridge.Bridge`) and
relays it to every browser. This component is just a window onto that shared
room, so multiple Chat panels (or panels on a merged view) all show the same
conversation. Viewers edit their own display name right in the panel.

    chat = canvas.chat("chat")
    chat.post("welcome, everyone")        # send a line as the host
    @chat.on_message
    def log(entry):                        # observe every line from Python
        print(entry["name"], ":", entry["text"])
"""

from .base import BaseComponent


class Chat(BaseComponent):
    component = "Chat"
    default_w = 320
    default_h = 400

    def __init__(self, name="chat", label=None):
        super().__init__(name=name, label=label)
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

    def on_message(self, fn):
        """Decorator: register a callback fired with every chat entry (a dict of
        ``id``/``name``/``color``/``text``/``ts``)."""
        self._chat_callbacks.append(fn)
        if self._bridge is not None:
            self._bridge.add_chat_sink(fn)
        return fn
