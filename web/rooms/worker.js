// Live rooms for static danvas: one viewer's tab runs the app (the host), and
// others join it as more viewers of that same canvas, as a served danvas has.
// The relay is one Durable Object per room. It does not read danvas's frames:
// it carries them between the host and each guest.
//
//   GET /host?room=ID    WebSocket: the host. One per room.
//   GET /join?room=ID&…  WebSocket: a guest. Its query (vid, vname, vcolor:
//                        danvas's viewer identity) is passed to the host.
//
// Guest <-> relay: danvas's own frames, text and binary, unchanged; a guest's
// socket is an ordinary danvas socket.
// Host <-> relay, binary, each frame tagged with the guest it is for or from:
//   [kind u8][guest u32 big-endian][payload]   kind 0 text (UTF-8), 1 binary
//   kind 2 (host -> relay): close that guest
// Relay -> host, text: {"join": g, "query": {...}, "ua": "..."} and {"leave": g}.

const TEXT = 0, BYTES = 1, CLOSE = 2;
const MAX_GUESTS = 24;
const HOST_LEFT = 4410, NO_HOST = 4404, FULL = 4429;

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const origin = req.headers.get("Origin") || "";
    const allowed = env.ORIGINS.split(",").map((s) => s.trim());
    if (!allowed.includes(origin) && !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) {
      return new Response("not from an allowed page", { status: 403 });
    }
    const room = url.searchParams.get("room") || "";
    if (!["/host", "/join"].includes(url.pathname) || !/^[A-Za-z0-9_-]{8,64}$/.test(room)) {
      return new Response("GET /host?room=ID or /join?room=ID (WebSocket)", { status: 400 });
    }
    if (req.headers.get("Upgrade") !== "websocket") return new Response("expected a WebSocket", { status: 426 });
    return env.ROOMS.get(env.ROOMS.idFromName(room)).fetch(req);
  },
};

export class Room {
  constructor(ctx) {
    this.ctx = ctx;
  }

  /** The open host socket (a replaced one may still be closing). */
  host() { return this.ctx.getWebSockets("host").find((h) => h.readyState === 1) ?? null; }

  async fetch(req) {
    const url = new URL(req.url);
    const [client, server] = Object.values(new WebSocketPair());
    if (url.pathname === "/host") {
      // a host that reloads takes its room back; the old socket is stale
      for (const old of this.ctx.getWebSockets("host")) try { old.close(1012, "replaced"); } catch { /* gone */ }
      this.ctx.acceptWebSocket(server, ["host"]);
      server.serializeAttachment({ host: true });
      // guests still here reconnect to the new host as fresh viewers
      for (const ws of this.ctx.getWebSockets()) {
        if (ws !== server && !ws.deserializeAttachment()?.host) try { ws.close(1012, "the host restarted"); } catch { /* gone */ }
      }
      return new Response(null, { status: 101, webSocket: client });
    }
    // a guest
    this.ctx.acceptWebSocket(server);
    const host = this.host();
    if (!host) { server.close(NO_HOST, "no one is hosting this room"); return new Response(null, { status: 101, webSocket: client }); }
    if (this.ctx.getWebSockets().length > MAX_GUESTS + 1) { server.close(FULL, "the room is full"); return new Response(null, { status: 101, webSocket: client }); }
    const g = ((await this.ctx.storage.get("next")) ?? 0) + 1;
    await this.ctx.storage.put("next", g);
    server.serializeAttachment({ g });   // kept through hibernation
    const query = Object.fromEntries([...url.searchParams].filter(([k]) => ["vid", "vname", "vcolor"].includes(k)));
    host.send(JSON.stringify({ join: g, query, ua: req.headers.get("User-Agent") || "" }));
    return new Response(null, { status: 101, webSocket: client });
  }

  findGuest(g) {
    for (const ws of this.ctx.getWebSockets()) if (ws.deserializeAttachment()?.g === g) return ws;
    return null;
  }

  async webSocketMessage(ws, msg) {
    const a = ws.deserializeAttachment() ?? {};
    if (a.host) {
      if (typeof msg === "string") return;   // nothing for the relay itself yet
      const b = new Uint8Array(msg);
      if (b.length < 5) return;
      const g = new DataView(b.buffer, b.byteOffset).getUint32(1);
      const to = this.findGuest(g);
      if (!to) { if (b[0] !== CLOSE) ws.send(JSON.stringify({ leave: g })); return; }
      if (b[0] === TEXT) to.send(new TextDecoder().decode(b.subarray(5)));
      else if (b[0] === BYTES) to.send(b.subarray(5));
      else if (b[0] === CLOSE) try { to.close(1000, "closed by the host"); } catch { /* gone */ }
      return;
    }
    if (a.g == null) return;
    const host = this.host();
    if (!host) { try { ws.close(HOST_LEFT, "the host left"); } catch { /* gone */ } return; }
    const body = typeof msg === "string" ? new TextEncoder().encode(msg) : new Uint8Array(msg);
    host.send(tagged(typeof msg === "string" ? TEXT : BYTES, a.g, body));
  }

  async webSocketClose(ws) { this.gone(ws); }
  async webSocketError(ws) { this.gone(ws); }

  gone(ws) {
    const a = ws.deserializeAttachment() ?? {};
    try { ws.close(); } catch { /* closed */ }
    if (a.host) {
      if (this.ctx.getWebSockets("host").some((h) => h !== ws && h.readyState === 1)) return;   // replaced by a newer host
      for (const other of this.ctx.getWebSockets()) try { other.close(HOST_LEFT, "the host left"); } catch { /* gone */ }
    } else if (a.g != null) {
      try { this.host()?.send(JSON.stringify({ leave: a.g })); } catch { /* gone */ }
    }
  }
}

function tagged(kind, g, body) {
  const out = new Uint8Array(body.length + 5);
  out[0] = kind;
  new DataView(out.buffer).setUint32(1, g);
  out.set(body, 5);
  return out;
}
