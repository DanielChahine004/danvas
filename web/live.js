// live.js: reload this page when the site is redeployed, so an open tab shows the new version
// without a manual refresh.
//
//   <script src="live.js" defer></script>                          the page, its scripts and stylesheets
//   <script src="live.js" data-watch="index.json reels.json" defer></script>   and data it fetches
//
// While the tab is visible it asks the server every 20 s for each file's version (a HEAD request: the
// ETag GitHub Pages sends, nothing downloaded). When one changes it reloads in place and returns to
// where the reader was scrolled. A page can hold the reload while the reader is busy (an edit being
// saved, a dialog open) by defining window.liveBusy = () => true|false.
(() => {
	const me = document.currentScript, EVERY = 20000;
	const extra = (me?.dataset.watch || "").split(/\s+/).filter(Boolean).map((p) => new URL(p, me.src).href);
	const seen = {};
	const files = () => [location.href.split("#")[0], ...extra,
		...[...document.querySelectorAll("script[src], link[rel=stylesheet][href]")].map((e) => e.src || e.href)]
		.filter((u, i, all) => new URL(u).origin === location.origin && all.indexOf(u) === i);
	async function version(u) {
		const r = await fetch(u, { method: "HEAD", cache: "no-store" });
		return r.ok ? r.headers.get("etag") || r.headers.get("last-modified") : null;
	}
	async function check() {
		if (document.visibilityState !== "visible") return;
		let changed = false;
		for (const u of files()) {
			try {
				const v = await version(u);
				if (v && seen[u] && seen[u] !== v) changed = true;
				if (v) seen[u] = v;
			} catch { /* offline: try again next time */ }
		}
		if (changed && !(window.liveBusy && window.liveBusy())) {
			try { sessionStorage.setItem("live.at", JSON.stringify([location.href, scrollX, scrollY])); } catch {}
			location.reload();
		}
	}
	try {   // back to where the reader was, once the page has laid itself out
		const [href, x, y] = JSON.parse(sessionStorage.getItem("live.at") || "null") || [];
		sessionStorage.removeItem("live.at");
		if (href === location.href) addEventListener("load", () => setTimeout(() => scrollTo(x, y), 300));
	} catch {}
	setInterval(check, EVERY);
	document.addEventListener("visibilitychange", check);
	check();
})();
