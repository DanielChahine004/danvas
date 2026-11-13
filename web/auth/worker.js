// "Sign in with GitHub" for the static danvas page: a Cloudflare Worker that completes
// GitHub's login. GitHub needs the app's client secret to turn a login code into a token,
// and a secret cannot live in a public page, so this holds it. It stores nothing: the token
// goes straight back to the page, in the URL fragment, which never reaches a server.
//
// Settings (wrangler.toml [vars] and `wrangler secret put`):
//   CLIENT_ID      the GitHub App's client ID
//   CLIENT_SECRET  its client secret (a secret)
//   VIEWER         the danvas page's URL; tokens are only ever sent there
//   VIEWERS        optional: more page URLs, space or comma separated (other people's
//                  copies), each approved by whoever runs this worker
//
//   GET  /login?back=<page url>        -> GitHub's sign-in page
//   GET  /callback?code=...&state=...  -> back to the page with #token=...
//   POST /refresh {refresh_token}      -> a new token, before the old one expires

export default {
	async fetch(req, env) {
		const url = new URL(req.url);
		// every page allowed to receive a token; the first is the default
		const viewers = [env.VIEWER, ...(env.VIEWERS || "").split(/[\s,]+/)].filter(Boolean).map((v) => v.replace(/\/?$/, "/"));
		const viewer = viewers[0];
		const allowed = (u) => typeof u === "string" && viewers.some((v) => u.startsWith(v));
		const origin = req.headers.get("Origin");
		const cors = { "Access-Control-Allow-Origin": viewers.some((v) => new URL(v).origin === origin) ? origin : new URL(viewer).origin,
			"Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "POST, OPTIONS", Vary: "Origin" };

		if (url.pathname === "/login") {
			// the page passes ?back= (its own address, with its ?repo=); only an approved page gets the token
			const back = url.searchParams.get("back") || viewer;
			const state = JSON.stringify({ back: allowed(back) ? back : viewer });
			const gh = new URL("https://github.com/login/oauth/authorize");
			gh.searchParams.set("client_id", env.CLIENT_ID);
			gh.searchParams.set("redirect_uri", url.origin + "/callback");
			gh.searchParams.set("state", btoa(state));
			return Response.redirect(gh.toString(), 302);
		}

		if (url.pathname === "/callback") {
			let state;
			try { state = JSON.parse(atob(url.searchParams.get("state") || "")); } catch { return new Response("bad state", { status: 400 }); }
			const back = allowed(state.back) ? state.back : viewer;
			const tok = await exchange(env, { code: url.searchParams.get("code") });
			if (!tok.access_token) return new Response("GitHub refused the sign-in: " + (tok.error_description || tok.error || "unknown"), { status: 400 });
			const frag = new URLSearchParams({ token: tok.access_token, refresh: tok.refresh_token || "", expires_in: String(tok.expires_in || 0) });
			return Response.redirect(back.split("#")[0] + "#" + frag.toString(), 302);
		}

		if (url.pathname === "/refresh") {
			if (req.method === "OPTIONS") return new Response(null, { headers: cors });
			if (req.method !== "POST") return new Response("POST only", { status: 405, headers: cors });
			const { refresh_token } = await req.json().catch(() => ({}));
			if (!refresh_token) return new Response("no refresh_token", { status: 400, headers: cors });
			const tok = await exchange(env, { grant_type: "refresh_token", refresh_token });
			if (!tok.access_token) return new Response(JSON.stringify(tok), { status: 401, headers: { ...cors, "Content-Type": "application/json" } });
			return new Response(JSON.stringify({ access_token: tok.access_token, refresh_token: tok.refresh_token, expires_in: tok.expires_in }),
				{ headers: { ...cors, "Content-Type": "application/json" } });
		}

		return new Response("danvas sign-in helper", { status: 404 });
	},
};

async function exchange(env, params) {
	const r = await fetch("https://github.com/login/oauth/access_token", {
		method: "POST",
		headers: { Accept: "application/json", "Content-Type": "application/json" },
		body: JSON.stringify({ client_id: env.CLIENT_ID, client_secret: env.CLIENT_SECRET, ...params }),
	});
	return r.json().catch(() => ({ error: `GitHub said ${r.status}` }));
}
