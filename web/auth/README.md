# Sign in with GitHub

The static page (`web/`) opens a private repository with a token for the person
visiting. GitHub turns a login into a token only for someone holding the app's
client secret, which cannot live in a public page. `worker.js` holds it, on
Cloudflare Workers' free plan, and stores nothing.

1. **GitHub App.** GitHub → Settings → Developer settings → GitHub Apps → New.
   - Homepage URL: the page's URL.
   - Callback URL: `https://danvas-auth.<your-subdomain>.workers.dev/callback`
     (Cloudflare shows the subdomain after the first deploy; edit this then).
   - Untick Webhook → Active.
   - Repository permissions → Contents: **Read-only**. Nothing else.
   - Create it, note the **Client ID**, and generate a **client secret**.
2. **Worker.** In this folder, with Node installed:

        npx wrangler login
        npx wrangler secret put CLIENT_SECRET     # paste the secret
        # set CLIENT_ID and VIEWER in wrangler.toml, then
        npx wrangler deploy

3. **Page.** Put the worker's URL and the app's address in `web/boot.js`
   (`AUTH` and `GH_APP`).

**Other pages.** Someone hosting their own copy of the page can use this sign-in
too: add its address to `VIEWERS` (in `wrangler.toml`, or Cloudflare → the
worker → Settings → Variables). Only listed addresses ever receive a token.

A token reaches only repositories the app is installed on, and only those the
signed-in person could already read. To share a private repository, add people
as collaborators on it; they sign in with their own accounts.
