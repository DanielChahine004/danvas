
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
            no messages yet — say hello
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
          title="your display name — edit and press Enter" />
      </div>

      <div style={{ display: "flex", gap: 6 }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder="message…"
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
