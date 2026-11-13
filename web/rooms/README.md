# Live rooms

A static danvas app runs in each reader's own tab, so each reader has a canvas
of their own. **Share live** (lower right of the page) lets one reader host:
their tab keeps running the app, and anyone who opens the room's link joins it
as another viewer of that canvas, as a served danvas has. Everyone sees the same
panels, each other's drawings, cursors and moves; a guest's slider moves the
host's app. Guests run no Python.

This worker is the relay between them: one Cloudflare Durable Object per room,
passing danvas's frames between the host's tab and each guest without reading
them. See worker.js for the wire.

- The room lasts while the host's tab is open. A host that reloads takes the
  same room back (the app restarts; drawings are kept), and guests reconnect.
- Anyone with the link can join. A guest of a private repository's app sees what
  the host's app shows them, without needing access to the repository.
- Whatever the host's app can reach, its guests can drive through the app: a
  serial device the host connected, for example.

## Deploy

```bash
cd web/rooms
npx wrangler deploy
```

It runs on Workers' free plan. `ORIGINS` (wrangler.toml) lists the pages that may
host or join; the page (web/boot.js) expects the relay at
`wss://danvas-rooms.<account>.workers.dev`, or `?rooms=wss://...`.

## Locally

```bash
cd web/rooms && npx wrangler dev --port 8788 --ip 127.0.0.1
```

The page uses `ws://127.0.0.1:8788` when it is served from localhost (as by
web/dev_server.py). Open an app, click Share live, and open the room's link in
another tab.
