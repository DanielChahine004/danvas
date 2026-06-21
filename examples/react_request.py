"""React panel request/response: await an answer from Python.

`canvas.request(data)` is the awaitable twin of `canvas.send` — it returns a
Promise that resolves with the **return value** of the panel's matching
`@on_request` handler. Here a panel asks Python to factorize a number
(server-side compute) and awaits the result, instead of hand-rolling reqId
correlation over send + push.

Run:  python examples/react_request.py
Then type a number and click "factorize".
"""

import danvas

PANEL = r"""
function Component({ canvas }) {
  const [n, setN] = React.useState("360");
  const [out, setOut] = React.useState(null);
  const [busy, setBusy] = React.useState(false);

  const go = async () => {
    setBusy(true); setOut(null);
    try {
      // Awaits the @on_request("factorize") handler's return value.
      const res = await canvas.request({ event: "factorize", n: Number(n) });
      setOut(res);                 // { factors: [...] }
    } catch (e) {
      setOut({ error: String((e && e.message) || e) });  // handler raised
    } finally {
      setBusy(false);
    }
  };

  const field = {
    padding: "6px 8px", fontSize: 14, borderRadius: 6,
    border: "1px solid var(--pc-border, #30363d)",
    background: "var(--pc-input-bg, #0d1117)", color: "var(--pc-text, #e6edf3)",
  };

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10,
      font: "13px system-ui", color: "var(--pc-text, #e6edf3)" }}>
      <div style={{ display: "flex", gap: 6 }}>
        <input value={n} onChange={(e) => setN(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") go(); }}
          style={{ ...field, flex: 1, minWidth: 0 }} />
        <button onClick={go} disabled={busy}
          style={{ ...field, cursor: "pointer", fontWeight: 600 }}>
          {busy ? "…" : "factorize"}
        </button>
      </div>
      <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 13 }}>
        {out == null
          ? <span style={{ color: "var(--pc-muted, #9aa4b2)" }}>await canvas.request(…)</span>
          : out.error
            ? <span style={{ color: "#f85149" }}>{out.error}</span>
            : <span>{n} = {out.factors.join(" × ")}</span>}
      </div>
    </div>
  );
}
"""

canvas = danvas.Canvas()
panel = canvas.react(PANEL, name="rpc", label="canvas.request → on_request",
                     x=120, y=120, w=320, h=150)


@panel.on_request("factorize")
def _(req):
    """Return the prime factors of req['n'] — the value resolves the panel's Promise."""
    n = int(req["n"])
    if n < 2:
        raise ValueError("enter an integer ≥ 2")   # raising rejects the Promise
    factors, d = [], 2
    while d * d <= n:
        while n % d == 0:
            factors.append(d)
            n //= d
        d += 1
    if n > 1:
        factors.append(n)
    return {"factors": factors}


print("Type a number and click factorize — Python computes the factors and the "
      "panel awaits the answer over canvas.request / on_request.")

canvas.serve(port=8000)
