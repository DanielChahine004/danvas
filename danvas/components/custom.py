"""Custom: an arbitrary-HTML panel rendered in a sandboxed iframe.

The HTML may be passed directly or loaded from a file, and ``css``/``js`` may be
supplied as separate strings — they are composed into a single document under
the hood (a ``<style>`` block, your markup, then a ``<script>`` block), so a
snippet copied from a site like uiverse.io drops in without hand-assembling a
page::

    panel = canvas.custom(html=markup, css=styles, js=behaviour)

A small ``canvas`` helper is injected into the iframe. It mirrors the React
panel's ``canvas`` handle so the two panel kinds share one mental model:

  * ``canvas.send(data)``          -> Python   (delivered to your handlers)
  * ``canvas.onPush(fn)``          <- Python   (``panel.push(data)`` calls ``fn``;
    the callback receive channel — a Custom panel updates its own DOM, so there is
    just this one end, equivalent to a React panel's ``onFrame``)
  * ``canvas.request(data)``       -> Promise  (awaitable twin of ``send``; resolves
    with the matching :meth:`on_request` handler's return value)
  * ``canvas.viewport(cb)``        — ``cb({x, y, zoom})`` now and on every camera move
  * ``canvas.setView({x, y, zoom})`` — pan/zoom the canvas to centre a point
  * ``canvas.chat``                — the shared room: ``send(text)``, ``setName(name)``,
    ``history()`` (a Promise), ``subscribe(cb)`` and ``identity(cb)`` (both return an unsubscribe)
  * ``canvas.sendBinary(buf)`` / ``requestCamera`` / ``requestMicrophone`` — as before

Set ``themed=True`` to have the panel follow the canvas theme (its CSS can then use
``var(--pc-bg)`` / ``var(--pc-text)`` / ``var(--pc-accent)`` … and track dark mode).

On the Python side, register handlers with ``@panel.on("event")`` to route by an
``event`` field, ``@panel.on_message`` to receive every message, or
``@panel.on_request`` to answer :meth:`request` calls.
"""

import json
import re
import traceback

from .base import BaseComponent
from ._routing import _EventRouter
from ..bridge import BINARY_CUSTOM

# A panel's ``html`` is treated as a complete page (left untouched, no base reset)
# only when it brings its own document shell; otherwise it's a fragment we wrap.
_FULL_DOCUMENT_RE = re.compile(r"<\s*(?:!doctype|html|body)\b", re.IGNORECASE)


class Custom(_EventRouter, BaseComponent):
    component = "Custom"
    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {},
        "props": {"html": "str -- the iframe document; a bare fragment is "
                          "fine (the frontend wraps it with the base reset "
                          "and injects the canvas API + interaction shim "
                          "unless the document already carries a "
                          "window.canvas= marker)",
                  "forwardWheel": "bool -- forward wheel to canvas zoom "
                                  "(default true)",
                  "permissions": "str|null -- iframe allow= policy",
                  "themed": "bool -- follow the canvas theme variables",
                  "keepMounted": "bool -- exempt from viewport culling "
                                 "(hidden, not destroyed, off-screen; "
                                 "browser-local state survives scrolling)"},
        "updates": {"data_patch": "merge changed data fields",
                    "post": "opaque value delivered to the document's "
                            "canvas.onPush"},
        "events": "free-form -- whatever the document's canvas.send posts",
        "binary": "CUSTOM (code 3) out via push(); INPUT (code 5) in via "
                  "the document's canvas.sendBinary",
    }
    default_w = 380
    default_h = 320
    BINARY_TYPE = BINARY_CUSTOM
    # Bounds for ``w="auto"`` content-fit width (content box, px): a panel may
    # shrink to be snug around its content but not collapse to a sliver, nor
    # sprawl wider than a readable column. See ``_fit_script``.
    _AUTO_W_MIN = 180
    _AUTO_W_MAX = 680

    def __init__(self, html=None, path=None, css=None, js=None, name="custom",
                 label=None, w=None, h=None, color=None, event_key="event",
                 permissions=None, forward_wheel=True, themed=False,
                 keep_mounted=False):
        # ``h="auto"`` fits the panel's height to its rendered content: the
        # iframe measures its document and the frontend resizes the shape (and
        # reports the result back, so ``comp.h`` syncs). It keeps re-fitting on
        # reflow, so narrowing the panel grows the height to match.
        self._auto_h = h == "auto"
        if self._auto_h:
            h = None  # default height until the first content measurement lands
        # ``w="auto"`` fits the panel's *width* to its content's natural width.
        # Unlike height this is a one-shot at load (the iframe measures the
        # content's max-content width once and the frontend resizes), so the
        # panel opens sized to its content but a later manual resize isn't
        # snapped back. Re-showing a value rebuilds the iframe and re-measures.
        self._auto_w = w == "auto"
        if self._auto_w:
            w = None  # default width until the first content measurement lands
        # ``w``/``h`` are optional overrides; when omitted the panel falls back
        # to ``default_w``/``default_h`` (set per subclass) via BaseComponent.
        size = {k: v for k, v in (("w", w), ("h", h)) if v is not None}
        super().__init__(name=name, label=label, **size)
        self._init_color(color)
        if path is not None:
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
        # The three pieces are kept separate so any one can be replaced later
        # (see update); they are composed into one document on the way out.
        self._html = html or ""
        self._css = css or ""
        self._js = js or ""
        # Semicolon-separated Permissions Policy string for the iframe's `allow`
        # attribute. A list is accepted for convenience: ["camera", "microphone"]
        # → "camera; microphone". Controls access to device APIs (getUserMedia etc.)
        # which browsers block in sandboxed iframes without an explicit grant.
        if isinstance(permissions, (list, tuple)):
            permissions = "; ".join(permissions)
        self._permissions = permissions or ""
        # When True (default), wheel events inside the iframe are forwarded to the
        # parent so scroll-to-zoom over a panel zooms the canvas, matching the bare
        # canvas. Set False for panels whose content does its own wheel handling
        # (e.g. a 3D viewer that zooms its own camera) so the canvas stays put.
        self._forward_wheel = forward_wheel
        # ``keep_mounted=True`` exempts the panel from viewport culling: the
        # iframe is HIDDEN (visibility) instead of destroyed when scrolled
        # out of view, so browser-local state — a 3D camera, tool toggles,
        # an in-progress interaction — survives scroll-out/scroll-in. For
        # heavy panels (Model3D) this also skips a full engine reboot and
        # data re-push per scroll-in. Off by default: a cheap panel is
        # better re-created than kept resident.
        self._keep_mounted = bool(keep_mounted)
        # ``themed=True`` makes the iframe follow the canvas theme: the frontend
        # forwards the live ``--pc-*`` CSS variables and the dark/light flag into the
        # document, so the panel's CSS can use ``var(--pc-bg)`` / ``var(--pc-text)``
        # / ``var(--pc-accent)`` etc. and track dark-mode toggles — the same theme a
        # React panel inherits for free. Off by default (a sandboxed iframe is
        # otherwise theme-isolated), so existing panels are unaffected.
        self._themed = bool(themed)
        # Inbound ``canvas.send`` routing (on / on_message / dispatch) is shared
        # with React via _EventRouter; override event_key if your HTML tags
        # messages with a different field.
        self._init_routing(event_key)

    def _wrap(self, html):
        """Prepend the owner-side prelude to the iframe document.

        The ``canvas`` API and the canvas-gesture forwarding moved to the
        frontend (``customShim.ts``): CustomView injects them into any
        document that doesn't already carry a ``window.canvas=`` marker, with
        the *browser-local* composed panel id baked in -- id-correct through a
        hub (an owner-baked id loses its namespace tag), and SDKs in other
        languages need no copy of the script. Only the content-fit script
        stays owner-side: it depends on this panel's ``h="auto"``/``w="auto"``
        flags, and the parent matches its reports by source window, not id.
        """
        if self._auto_h or self._auto_w:
            return self._fit_script(json.dumps(self.id)) + html
        return html

    def _fit_script(self, cid):
        """The in-iframe content-fit script for ``h="auto"`` / ``w="auto"``.

        Height (when on) is measured continuously: the iframe reports its
        document's natural height and a ResizeObserver re-fits on every reflow,
        so narrowing the panel grows the height to match. Width (when on) is a
        one-shot at load: the body is briefly shrink-wrapped to its content's
        ``max-content`` width — measured independently of the current frame
        width — reported once, then restored so the body fills the frame again
        and content can still reflow on a later manual resize. The parent
        applies both (see ``fitFromIframe``).
        """
        parts = ["<script>(function(){"]
        if self._auto_h:
            # Measure the *body's* content height, not documentElement's. We
            # force `html,body{height:auto;overflow:hidden}` in arm() below, but
            # <html> still fills the iframe viewport, so its scrollHeight is
            # pinned at the frame height (>= its clientHeight) and the panel
            # could never shrink below its starting size. The body, with
            # height:auto, reports the true content height; fall back to
            # documentElement only if there's no body yet.
            parts.append(
                "var fitH=function(){"
                "var b=document.body,d=document.documentElement;"
                "var h=Math.ceil(b?b.scrollHeight:(d?d.scrollHeight:0));"
                f"parent.postMessage({{__danvas_fit:{{id:{cid},h:h}}}},'*');"
                "};"
            )
        if self._auto_w:
            # max-content ignores the available (frame) width, so the body's
            # scrollWidth under it is the content's true preferred width — the
            # SVG/figure's intrinsic width, the widest JSON line — regardless of
            # how wide the panel currently is. Restore the inline width right
            # after so the body goes back to filling the frame (block default),
            # leaving the panel freely resizable and its content able to reflow.
            #
            # Clamp to a sane panel range: the raw preferred width can collapse
            # to a sliver — a list of short numbers is one token per line, a
            # small inline SVG can report next to nothing — narrower than even
            # the panel's own header label. MIN keeps it readable (and the label
            # legible); MAX stops a wide widget sprawling across the canvas.
            parts.append(
                "var fitW=function(){"
                "var b=document.body;if(!b)return;"
                "var prev=b.style.width;b.style.width='max-content';"
                "var w=b.scrollWidth;b.style.width=prev;"
                f"w=Math.min({self._AUTO_W_MAX},"
                f"Math.max({self._AUTO_W_MIN},Math.ceil(w)));"
                f"parent.postMessage({{__danvas_fit:{{id:{cid},w:w}}}},'*');"
                "};"
            )
        parts.append("var arm=function(){")
        if self._auto_h:
            parts.append(
                "var st=document.createElement('style');"
                "st.textContent='html,body{height:auto !important;"
                "min-height:0 !important;overflow:hidden !important}';"
                "document.head.appendChild(st);"
                # Expose an auto-height hook on <body> so content whose layout is
                # normally pinned to the panel height (a full-height flex column
                # with an inner scroll area, e.g. Table) can switch to sizing
                # *from* its content instead — otherwise the measured height
                # depends on the panel height and the fit loop oscillates.
                "if(document.body)document.body.classList.add('pc-auto-h');"
            )
        # Width before height: a one-shot fit at the content's natural width so
        # the height (when it also auto-fits) is then measured without wrapping.
        if self._auto_w:
            parts.append("fitW();")
        if self._auto_h:
            parts.append(
                "fitH();"
                "if(window.ResizeObserver){"
                "new ResizeObserver(fitH).observe(document.body);}"
            )
        parts.append("};")
        parts.append(
            "if(document.readyState==='loading'){"
            "document.addEventListener('DOMContentLoaded',arm);}"
            "else{arm();}"
        )
        # On load (images/fonts settled) re-run the one-shot pieces.
        on_load = ("fitW();" if self._auto_w else "") + \
                  ("fitH();" if self._auto_h else "")
        parts.append(f"window.addEventListener('load',function(){{{on_load}}});")
        parts.append("})();</script>")
        return "".join(parts)

    @staticmethod
    def compose(html="", css="", js=""):
        """Assemble separate HTML/CSS/JS strings into one iframe document.

        Used internally whenever ``css`` or ``js`` is given, but also callable
        directly when you want the composed string itself. The wrapper adds a
        minimal reset and centers the content in the frame.
        """
        return (
            "<style>"
            "* { box-sizing: border-box; margin: 0; padding: 0;"
            " font-family: system-ui, sans-serif; }"
            "body { background: transparent; display: flex;"
            " justify-content: center; align-items: center;"
            " min-height: 100vh; overflow: hidden; }"
            f"{css}"
            "</style>"
            f"{html}"
            f"<script>{js}</script>"
        )

    def _document(self):
        """The full document for the iframe.

        A *fragment* — the common case — is wrapped with the shared base reset
        (sane margins, ``box-sizing``, a transparent background, content centred)
        whether it came as ``html`` alone or as ``css``/``js``, so an html-only
        panel no longer has to hand-write its own ``<style>`` reset. A *complete
        page* (one that brings its own ``<html>``/``<body>``/``<!doctype>``) owns
        its whole document and is left untouched."""
        if self._css or self._js:
            return self.compose(self._html, self._css, self._js)
        if _FULL_DOCUMENT_RE.search(self._html or ""):
            return self._html
        return self.compose(self._html)

    def register_props(self):
        props = dict(self._props)  # label, w, h
        props["html"] = self._wrap(self._document())
        props["permissions"] = self._permissions
        props["themed"] = self._themed
        if self._keep_mounted:
            props["keepMounted"] = True
        # The frontend injects the interaction shim; this flag is its wheel
        # opt-out (panels whose content does its own wheel handling).
        props["forwardWheel"] = self._forward_wheel
        return props

    def update(self, html=None, css=None, js=None):
        """Replace the panel's content (reloads the iframe).

        Each piece left as ``None`` keeps its current value, so e.g.
        ``panel.update(css=new_css)`` restyles without touching the markup.
        """
        if html is not None:
            self._html = html
        if css is not None:
            self._css = css
        if js is not None:
            self._js = js
        self._send_update({"html": self._wrap(self._document())})

    def _set_auto_h(self):
        """Enable content-fit height live (``comp.h = "auto"``).

        Flips the panel into auto-height and re-sends the document so the iframe
        starts measuring its content and reporting the height back — the same
        machinery as passing ``h="auto"`` at insert, but available any time. A
        no-op if already auto. (To go back to a fixed height, assign a number:
        ``comp.h = 240``.)
        """
        if self._auto_h:
            return
        self._auto_h = True
        # Re-wrap with the fit script so the running iframe begins reporting its
        # height. Safe before serving — _send_update is a no-op with no bridge,
        # and register_props picks up the flag on first render.
        self._send_update({"html": self._wrap(self._document())})

    def _set_fixed_h(self, value):
        """Pin the height to a number, leaving auto-height mode if it was on (so
        the value isn't immediately overridden by the iframe's content fit)."""
        if self._auto_h:
            self._auto_h = False
            self._send_update({"html": self._wrap(self._document())})
        self.set_layout(h=value)

    def _set_auto_w(self):
        """Enable content-fit width live (``comp.w = "auto"``).

        Re-sends the document so the running iframe performs the one-shot
        natural-width measurement and reports it back. A no-op if already auto.
        (To go back to a fixed width, assign a number: ``comp.w = 320``.)
        """
        if self._auto_w:
            return
        self._auto_w = True
        self._send_update({"html": self._wrap(self._document())})

    def _set_fixed_w(self, value):
        """Pin the width to a number, leaving auto-width mode if it was on (so a
        later content rebuild doesn't re-fit over the value)."""
        if self._auto_w:
            self._auto_w = False
            self._send_update({"html": self._wrap(self._document())})
        self.set_layout(w=value)

    def push(self, data):
        """Stream live data into the panel's iframe *without* reloading it.

        In the iframe, receive it with ``canvas.onPush(fn)`` — ``fn`` is called
        with ``data`` (any JSON-serializable value) for each push. Unlike
        :meth:`update`, this keeps the iframe — and its focus, listeners, and
        scroll position — intact, so it suits high-rate streaming (e.g. video
        frames) and live two-way panels.
        """
        self._send_update({"post": data})

    # -- input routing (browser -> Python) -----------------------------------
    # on() / on_message() / _handle_input() come from _EventRouter, shared with
    # React (so a Custom widget needn't subclass to reimplement its own routing).