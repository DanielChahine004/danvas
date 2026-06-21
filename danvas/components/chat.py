"""Chat: a shared message panel for everyone viewing the canvas.

Unlike the data-driven panels, chat flows *between viewers* â€” the server stamps
each line with the sender's identity (see :class:`~danvas.bridge.Bridge`) and
relays it to every browser. This component is just a window onto that shared
room, so multiple Chat panels (or panels on a merged view) all show the same
conversation. Viewers edit their own display name right in the panel.

Rendered as a native React panel (mounted by ReactHost): the JSX subscribes to
the canvas-wide chat channel via ``canvas.chat`` â€” distinct from the per-panel
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

from .base import _mark_dedicated, _mark_threaded
from .react import React

# Port of the former native ChatShapeUtil view, driven by ``canvas.chat`` instead
# of importing the bridge directly. Theme colours come from the canvas ``--pc-*``
# variables (the panel mounts natively, so they resolve). Written as a plain
# string so its JSX braces survive â€” nothing is substituted.
_CHAT_SOURCE = r"""
function Component({ canvas }) {
  const chat = canvas.chat;
  const [me, setMe] = React.useState(null);
  const [messages, setMessages] = React.useState(() => [...chat.history()]);
  const [draft, setDraft] = React.useState("");
  const [nameDraft, setNameDraft] = React.useState("");
  const [editingName, setEditingName] = React.useState(false);
  const listRef = React.useRef(null);

  React.useEffect(() => chat.identity(setMe), []);

  // Backfill from the retained log, then append each new line (deduped by msgId
  // so the history/subscribe gap can't double-post).
  React.useEffect(() => {
    setMessages([...chat.history()]);
    return chat.subscribe((entry) =>
      setMessages((m) => (m.some((x) => x.msgId === entry.msgId) ? m : [...m, entry]))
    );
  }, []);

  // While not actively editing, keep the name field showing our current name.
  React.useEffect(() => {
    if (me && !editingName) setNameDraft(me.name);
  }, [me, editingName]);

  // Keep the newest message in view.
  React.useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const send = () => {
    const t = draft.trim();
    if (!t) return;
    chat.send(t);
    setDraft("");
  };

  const commitName = () => {
    setEditingName(false);
    const n = nameDraft.trim();
    if (n && me && n !== me.name) chat.setName(n);
    else if (me) setNameDraft(me.name);
  };

  const fieldStyle = {
    fontSize: 13, padding: "5px 8px",
    border: "1px solid var(--pc-border-mid)", borderRadius: 6,
    background: "var(--pc-input-bg)", color: "var(--pc-text)",
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, gap: 6, height: "100%" }}>
      <div ref={listRef} style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {messages.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--pc-faint2)", fontStyle: "italic", padding: 4 }}>
            no messages yet â€” say hello
          </div>
        ) : (
          messages.map((m) => (
            <div key={m.msgId} style={{ fontSize: 13, lineHeight: 1.4, marginBottom: 3, wordBreak: "break-word" }}>
              <span style={{ fontWeight: 700, color: m.color || "var(--pc-text)" }}>
                {m.name}{me && m.id === me.id ? " (you)" : ""}:
              </span>{" "}
              <span style={{ color: "var(--pc-text)" }}>{m.text}</span>
            </div>
          ))
        )}
      </div>

      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span style={{ fontSize: 11, color: "var(--pc-muted)" }}>name</span>
        <input
          value={nameDraft}
          onChange={(e) => setNameDraft(e.target.value)}
          onFocus={() => setEditingName(true)}
          onBlur={commitName}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
          maxLength={24}
          style={{ ...fieldStyle, flex: 1, minWidth: 0 }}
          title="your display name â€” edit and press Enter" />
      </div>

      <div style={{ display: "flex", gap: 6 }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder="messageâ€¦"
          style={{ ...fieldStyle, flex: 1, minWidth: 0 }} />
        <button onClick={send} style={{
          padding: "5px 12px", border: "none", borderRadius: 6, fontSize: 13,
          fontWeight: 600, cursor: "pointer", background: "var(--pc-accent)",
          color: "var(--pc-accent-text)",
        }}>Send</button>
      </div>
    </div>
  );
}
"""


class Chat(React):
    default_w = 320
    default_h = 400

    def __init__(self, name="chat", label=None):
        super().__init__(source=_CHAT_SOURCE, name=name, label=label)
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
        if threaded and dedicated:
            raise ValueError("threaded and dedicated are mutually exclusive")
        def register(f):
            if dedicated:
                sink = _mark_dedicated(f, queue)
            elif threaded:
                sink = _mark_threaded(f)
            else:
                sink = f
            self._chat_callbacks.append(sink)
            if self._bridge is not None:
                self._bridge.add_chat_sink(sink)
            return f
        return register(fn) if fn is not None else register
