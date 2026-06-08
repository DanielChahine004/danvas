"""WebView: panel an external website/URL in an embedded iframe.

Unlike :class:`Custom` (which sandboxes arbitrary user HTML *away* from its own
origin), WebView loads a real URL straight into its iframe with
``allow-same-origin`` — so interactive embeds that need access to their own
origin (YouTube's player, maps, most web apps) actually run instead of rendering
a blank/black frame.

Embedding still only works for sites that permit being framed. Pages that send
``X-Frame-Options: DENY`` or a CSP ``frame-ancestors`` directive (Google,
Twitter/X, GitHub, most banks) refuse to load — that's a browser security rule,
not a PyCanvas limitation. For YouTube, ``watch?v=``/``youtu.be`` links are
rewritten to their embeddable ``/embed/`` form automatically.
"""

from urllib.parse import parse_qs, urlparse

from .base import BaseComponent


class WebView(BaseComponent):
    component = "WebView"
    default_w = 800
    default_h = 600

    def __init__(self, url, name="web", label=None, width=800, height=600):
        super().__init__(name=name, label=label if label is not None else url,
                         w=width, h=height)
        self._url = self._normalize(url)

    @staticmethod
    def _normalize(url):
        """Rewrite a YouTube watch/short link to its embeddable ``/embed/`` form.

        The main YouTube pages refuse framing, but ``youtube.com/embed/<id>`` is
        meant for iframes. Handles ``watch?v=ID``, ``youtu.be/ID``, and carries
        over a ``t=``/``start=`` start time. Any non-YouTube (or already-embed)
        URL is returned unchanged.
        """
        try:
            u = urlparse(url)
        except ValueError:
            return url
        host = u.netloc.lower().removeprefix("www.")
        vid = None
        if host == "youtu.be":
            vid = u.path.lstrip("/").split("/", 1)[0]
        elif host in ("youtube.com", "m.youtube.com") and u.path == "/watch":
            vid = parse_qs(u.query).get("v", [None])[0]
        if not vid:
            return url
        q = parse_qs(u.query)
        start = q.get("t", q.get("start", [None]))[0]
        embed = f"https://www.youtube.com/embed/{vid}"
        if start:
            embed += f"?start={start.rstrip('s')}"
        return embed

    def register_props(self):
        props = dict(self._props)  # label, w, h
        props["url"] = self._url
        return props

    def navigate(self, url):
        """Point the panel at a new ``url``, live (reloads the iframe)."""
        self._url = self._normalize(url)
        self._send_update({"url": self._url})
