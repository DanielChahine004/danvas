"""Custom: an arbitrary-HTML panel rendered in a sandboxed iframe.

The HTML may be passed directly or loaded from a file, and ``css``/``js`` may be
supplied as separate strings — they are composed into a single document under
the hood (a ``<style>`` block, your markup, then a ``<script>`` block), so a
snippet copied from a site like uiverse.io drops in without hand-assembling a
page::

    panel = canvas.custom(html=markup, css=styles, js=behaviour)

A small ``canvas`` helper is injected into the iframe with a symmetric two-way
channel:

  * ``canvas.send(data)``   -> Python   (delivered to your handlers)
  * ``canvas.onPush(fn)``   <- Python   (``panel.push(data)`` calls ``fn(data)``)

On the Python side, register handlers with ``@panel.on("event")`` to route by an
``event`` field, or ``@panel.on_message`` to receive every message.
"""

import json
import traceback

from .base import BaseComponent
from ._routing import _EventRouter
from ..bridge import BINARY_CUSTOM


class Custom(_EventRouter, BaseComponent):
    component = "Custom"
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
                 permissions=None, forward_wheel=True):
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
        # Inbound ``canvas.send`` routing (on / on_message / dispatch) is shared
        # with React via _EventRouter; override event_key if your HTML tags
        # messages with a different field.
        self._init_routing(event_key)

    def _wrap(self, html):
        """Prepend the ``canvas`` helper, tagged with this component's id.

        ``send`` posts back to the app (tagged with the id so the bridge knows
        which panel spoke). ``onPush`` is the receive side: it subscribes to the
        ``message`` events that :meth:`push` delivers and hands your callback the
        raw payload, so the iframe never has to unwrap ``__danvas`` itself.
        """
        # json.dumps keeps the id safely quoted inside the script literal.
        cid = json.dumps(self.id)
        helper = (
            "<script>window.canvas={"
            "send:function(data){"
            f"parent.postMessage({{__danvas:{cid},data:data}},'*');"
            "},"
            # sendBinary transfers the ArrayBuffer zero-copy to the parent window,
            # which re-encodes it into the binary WebSocket frame and sends it to
            # Python. The buffer is detached after transfer (standard ArrayBuffer
            # transfer semantics), so callers should not reuse it.
            "sendBinary:function(buf){"
            "var ab=buf instanceof ArrayBuffer?buf:(buf.buffer||buf);"
            f"parent.postMessage({{__danvas_binary:{cid},data:ab}},'*',[ab]);"
            "},"
            "onPush:function(fn){window.addEventListener('message',function(e){"
            "if(e.data&&e.data.__danvas!==undefined){fn(e.data.__danvas);}"
            "});},"
            # requestCamera / releaseCamera: getUserMedia cannot run inside a
            # sandboxed iframe (null origin blocks the permission grant even with
            # allow="camera"). These methods ask the parent page to open the
            # camera and relay JPEG frames via push_binary — each frame arrives in
            # canvas.onPush as an ArrayBuffer, same as panel.push_binary() from
            # Python. opts: { width, height, fps, quality } (all optional).
            "requestCamera:function(opts){"
            f"parent.postMessage({{__danvas_camera:{cid},action:'start',opts:opts||{{}}}},'*');"
            "},"
            "releaseCamera:function(){"
            f"parent.postMessage({{__danvas_camera:{cid},action:'stop'}},'*');"
            "},"
            # requestMicrophone / releaseMicrophone: same sandbox constraint as
            # camera — getUserMedia({audio}) is blocked in a null-origin iframe.
            # The parent captures mic audio, converts to int16 PCM, and relays
            # each chunk the same way: sendBinary up to Python (@on_binary) and
            # liveHandlers down to canvas.onPush as an ArrayBuffer. A JSON
            # {event:'mic_start', sampleRate, channels} is sent first so Python
            # knows the stream parameters before audio data arrives.
            # opts: { bufferSize } (optional, default 4096 samples ≈ 85–93ms).
            "requestMicrophone:function(opts){"
            f"parent.postMessage({{__danvas_mic:{cid},action:'start',opts:opts||{{}}}},'*');"
            "},"
            "releaseMicrophone:function(){"
            f"parent.postMessage({{__danvas_mic:{cid},action:'stop'}},'*');"
            "}"
            "};"
            # JS errors and unhandled promise rejections are reported back to
        # Python via postMessage so they surface in the terminal.
        "window.onerror=function(msg,src,line,col,err){"
        f"parent.postMessage({{__danvas_error:{{id:{cid},"
        "msg:msg+(src?' ('+src+':'+line+')':'')}},'*');"
        "return false;};"
        "window.addEventListener('unhandledrejection',function(e){"
        "var r=e.reason;"
        f"parent.postMessage({{__danvas_error:{{id:{cid},"
        "msg:'Unhandled rejection: '+(r&&r.message||String(r))}},'*');});"
        # Wheel inside the iframe can't reach the parent (cross-document) and the
            # canvas can't preventDefault an event in a sandboxed frame. Swallow every
            # wheel here and forward the delta + cursor to the parent, which zooms the
            # canvas at that point — so scroll-to-zoom works over a panel exactly like
            # over the bare canvas. (danvas zooms on wheel everywhere; React panels
            # don't wheel-scroll their content either, so this just makes Custom
            # panels consistent.) Capture phase so we win over any content (e.g.
            # Plotly) wheel handler.
            + (
                "window.addEventListener('wheel',function(e){e.preventDefault();"
                "parent.postMessage({__danvas_wheel:{x:e.clientX,y:e.clientY,d:e.deltaY}},'*');"
                "},{passive:false,capture:true});"
                if self._forward_wheel else ""
            ) +
            # Right-drag inside the iframe pans the canvas: the parent can't see these
            # events (cross-document), so forward the deltas. Pointer-capture keeps the
            # drag alive if the cursor leaves the frame. A right-click that didn't drag
            # (<=4px) opens the canvas context menu there — parity with the bare canvas
            # and React panels. The browser context menu is always suppressed.
            # Pan deltas from screenX/screenY (absolute physical-screen coords): they
            # don't change when the panel moves under a stationary cursor, so the pan
            # can't feed back on itself (the iframe-relative clientX would, since the
            # pan moves the iframe). `_pm` accumulates the drag distance to tell a
            # click (-> context menu) from a drag.
            "var _pan=false,_sx=0,_sy=0,_pm=0;"
            "window.addEventListener('pointerdown',function(e){"
            "if(e.button===2){_pan=true;_sx=e.screenX;_sy=e.screenY;_pm=0;"
            "try{document.documentElement.setPointerCapture(e.pointerId);}catch(_){}}"
            "},true);"
            "window.addEventListener('pointermove',function(e){"
            "if(!_pan)return;var dx=e.screenX-_sx,dy=e.screenY-_sy;_sx=e.screenX;_sy=e.screenY;"
            "_pm+=Math.abs(dx)+Math.abs(dy);"
            "parent.postMessage({__danvas_pan:{dx:dx,dy:dy}},'*');"
            "},true);"
            "window.addEventListener('pointerup',function(e){"
            "if(e.button===2){_pan=false;"
            "if(_pm<=4)parent.postMessage({__danvas_menu:{x:e.clientX,y:e.clientY}},'*');}"
            "},true);"
            "window.addEventListener('contextmenu',function(e){e.preventDefault();},true);"
            # Canvas tool shortcuts (v/h/d/r/o/l/a/t/n/e/p + Escape) don't reach the
            # parent once the iframe has keyboard focus (clicking/orbiting inside it
            # focuses the iframe's own document). Forward just those keys — never
            # while typing in a field, never with a modifier — so pressing `v` to
            # switch back to the select tool works over a panel like anywhere else.
            "var _shortcuts='vhdrolatnep';"
            "window.addEventListener('keydown',function(e){"
            "if(e.ctrlKey||e.metaKey||e.altKey)return;"
            "var t=e.target||{};var tn=(t.tagName||'');"
            "if(tn==='INPUT'||tn==='TEXTAREA'||tn==='SELECT'||t.isContentEditable)return;"
            "var k=e.key.length===1?e.key.toLowerCase():e.key;"
            "if(k==='Escape'||_shortcuts.indexOf(k)>=0)"
            "parent.postMessage({__danvas_key:{key:e.key}},'*');"
            "});"
            "</script>"
        )
        if self._auto_h or self._auto_w:
            helper += self._fit_script(cid)
        return helper + html

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
        """The full document for the iframe: composed only when css/js exist,
        so a complete page passed as ``html`` alone is left untouched."""
        if self._css or self._js:
            return self.compose(self._html, self._css, self._js)
        return self._html

    def register_props(self):
        props = dict(self._props)  # label, w, h
        props["html"] = self._wrap(self._document())
        props["permissions"] = self._permissions
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