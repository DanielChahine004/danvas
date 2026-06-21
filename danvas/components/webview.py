"""WebView: panel an external website/URL in an embedded iframe.

Now a native React panel (mounted by ReactHost) whose body is a single
``<iframe>`` pointed at a real URL — so interactive embeds that need access to
their own origin (YouTube's player, maps, most web apps) actually run instead of
rendering a blank/black frame.

Embedding still only works for sites that permit being framed. Pages that send
``X-Frame-Options: DENY`` or a CSP ``frame-ancestors`` directive (Google,
Twitter/X, GitHub, most banks) refuse to load — that's a browser security rule,
not a danvas limitation. For YouTube, ``watch?v=``/``youtu.be`` links are
rewritten to their embeddable ``/embed/`` form automatically.
"""

from urllib.parse import parse_qs, urlparse

from .react import React

_WEBVIEW_CSS = """
.pc-webview{width:100%;height:100%;border:0;display:block;background:#fff}
"""

# A single iframe filling the panel. ``props.url`` swaps live (and replays on
# reconnect), so navigating just re-renders with a new src.
_WEBVIEW_SOURCE = """
function Component({ props }) {
  return (
    <>
      <style>{`__CSS__`}</style>
      <iframe className="pc-webview" src={props.url}
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
        allowFullScreen />
    </>
  );
}
""".replace("__CSS__", _WEBVIEW_CSS)


class WebView(React):
    default_w = 800
    default_h = 600

    def __init__(self, url, name="web", label=None, w=None, h=None, color=None):
        super().__init__(source=_WEBVIEW_SOURCE, name=name,
                         label=label if label is not None else url, w=w, h=h,
                         props={"url": self._normalize(url)})
        self._init_color(color)

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

    def navigate(self, url):
        """Point the panel at a new ``url``, live (reloads the iframe)."""
        self.update(url=self._normalize(url))