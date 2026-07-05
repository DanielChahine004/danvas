# Security model

danvas is a **shared-canvas runtime**: processes and browsers that join a
canvas cooperate on one live document. Read this before exposing a canvas
beyond your own machine — the design is deliberate, and so are its
boundaries.

## The trust model, in one paragraph

**Everyone admitted to a canvas is trusted with the whole canvas.** An
authorized peer — a browser that passed the password page, a dial-in SDK
process, a merged canvas — can register panels whose JSX/HTML executes in
every viewer's browser, rewrite any panel's properties (the shared plane),
read the full canvas state, up- and download files through the panels that
offer it, and speak in chat under a chosen name. That is the product: a
multiplayer surface where any process can put live UI in front of every
viewer. The corollary: **admission is the security boundary.** Do not give
the password (or an open LAN bind, or a tunnel URL) to anyone you would not
let run JavaScript in the other viewers' browsers.

## The perimeter, layer by layer

- **Bind**: `serve()` binds `127.0.0.1` by default — nothing is exposed
  until you pass `host="0.0.0.0"` (LAN) or `tunnel=True` (public HTTPS via
  cloudflared). Choose exposure explicitly.
- **Password**: `serve(password=...)` gates every route — the page, the
  WebSocket, uploads, downloads — behind a session cookie (`pc_session`, a
  random per-session token; the password itself never rides a cookie).
  `passwords={role: pw}` gives each audience its own password.
- **Roles** attenuate what an admitted viewer sees and may operate
  (role-hidden panels are enforced at ingress and egress, and role-gated
  file tokens fail closed across hubs) — but roles are *visibility and
  operability* controls, not a sandbox: any admitted **process** peer is
  authoritative on the shared plane, stopped only by a hard `locked`.
- **Guest browsers vs process peers**: browsers pass per-panel gates
  (`locked`, `operable`, `lock_for`, roles) on every input; dial-in SDK
  processes are treated as parts of the application itself.

## What is sandboxed, and what is not

- Custom-panel HTML runs in an **iframe sandbox** (`allow-scripts`, no
  `allow-same-origin`): it cannot read the parent page, other panels, or
  cookies; it talks only through the postMessage bridge. It *can* render
  anything and use the canvas API its panel is entitled to.
- React panels compile and run **in the page itself** — a React panel from
  any admitted source has full DOM access in every viewer's browser. This is
  the strongest capability admission grants; it is not attenuated by roles.
- **File browsing** is sandboxed owner-side: a `file_browser` panel can never
  escape the `root=` its owner chose; viewers never send trusted paths.
- **Downloads** serve only content the owner code chose, behind unguessable
  short-TTL tokens; **uploads** land only at endpoints an owner minted, with
  owner-side size caps. Both ride the same auth gate as everything else.
- The wire enforces ingress authorization: inputs/requests/binary frames for
  panels a viewer may not operate are dropped before any handler runs.

## Operational guidance

- Treat a **tunnel URL like a password**: anyone holding it reaches the
  password page (or the open canvas, if you set none — don't, for tunnels).
- The canvas renders what peers send; run **untrusted code outside** the
  canvas and give it no credentials, rather than admitting it and hoping
  roles contain it.
- `persist=` files and ledgers contain canvas state including user-set
  values — treat them like the data they hold.
- The broker binds plain HTTP/WS; for hostile networks put TLS in front
  (the built-in tunnel provides HTTPS end-to-end).

## Reporting a vulnerability

If you find a way to cross one of the boundaries above **without admission**
(auth bypass, iframe sandbox escape, file-browser root escape, token
prediction), please email the maintainer (see the repository owner's GitHub
profile) rather than opening a public issue. Reports that amount to "an
admitted peer can affect other viewers" are the documented trust model, not
vulnerabilities — but boundary crossings are taken seriously and fixed with
priority.
