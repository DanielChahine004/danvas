"""React: a user-authored React component rendered as a native canvas panel.

The native counterpart to :class:`Custom`. Where ``Custom`` renders arbitrary
HTML in a *sandboxed iframe* (isolated, no theme or bridge access), ``React``
takes JSX *source* and mounts it as an ordinary React subtree **inside the
panel** — so it inherits the canvas theme, dark mode, and selection chrome, and
talks to Python directly with no postMessage hop. The JSX is compiled in the
browser at runtime (Babel, lazily loaded), so users author components from
Python with no ``npm`` build.

The component must be named ``Component`` and receives three props:

  * ``canvas`` — the bridge handle (see below);
  * ``value``  — the latest :meth:`push` data: Python → panel, no reload;
  * ``props``  — the dict from :meth:`update` / the ``props=`` arg: Python → panel,
    replayed on reconnect.

The ``canvas`` handle exposes:

  * ``send(data)`` — panel → Python, routed to your ``@on`` / ``on_message`` handlers;
  * ``request(data)`` — the **awaitable** twin of ``send``: returns a Promise that
    resolves with the return value of the matching :meth:`on_request` handler
    (``const r = await canvas.request({event:'…', …})``);
  * ``onFrame(cb)`` — subscribe (in a ``useEffect``) to the :meth:`push` /
    :meth:`push_binary` stream without re-rendering; ``cb`` gets each value (an
    ``ArrayBuffer`` for binary). Use this *or* the ``value`` prop, not both;
  * ``viewport(cb)`` — ``cb`` is called now and on every camera move with the live
    ``{ x, y, zoom }`` of the canvas centre (the numbers ``serve(view=…)`` takes);
  * ``setView({ x, y, zoom })`` — the write-twin of ``viewport``: pan/zoom the canvas
    to centre a point (any subset of the keys; omitted axes stay put);
  * ``chat`` — the canvas-wide shared room: ``send(text)``, ``setName(name)``,
    ``history()``, ``subscribe(cb)`` (returns an unsubscribe), ``identity(cb)``.

``React`` (with hooks) is in scope as ``React``; libraries named in ``scope=[…]`` are
in scope as ``libs`` (e.g. ``const d3 = libs.d3``).

    counter = canvas.react('''
      function Component({ canvas, value, props }) {
        const [n, setN] = React.useState(0)
        return <button onClick={() => { setN(n + 1); canvas.send({ clicks: n + 1 }) }}>
          {props.label}: {n}
        </button>
      }
    ''', props={"label": "Taps"})

    @counter.on_message
    def _(msg): print(msg)        # {'clicks': 3}
"""

import json
import traceback
import re

from ..bridge import BINARY_REACT
from .base import BaseComponent


class React(BaseComponent):
    component = "React"

    default_w = 380
    default_h = 320

    def __init__(self, source=None, path=None, jsx=None, css=None, name="react",
                 label=None, w=None, h=None, props=None, scope=None,
                 event_key="event", queue="fifo"):
        size = {k: v for k, v in (("w", w), ("h", h)) if v is not None}
        super().__init__(name=name, label=label, queue=queue, **size)
        if path is not None:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
        # Two ways in: ``source`` is a complete component (must define
        # ``function Component``); ``jsx`` is just the markup expression, which
        # — with optional ``css`` — is composed into a Component under the hood.
        if source is not None and jsx is not None:
            raise ValueError("pass either source= (a full Component) or jsx= "
                             "(markup to be wrapped), not both")
        if jsx is not None:
            source = self.compose(jsx, css or "")
        elif css:
            raise ValueError("css= only applies to jsx=; a full source= "
                             "component should carry its own <style>")
        self._source = source or ""
        # Optional third-party libraries to make available to the component as
        # the ``libs`` global. Each name is loaded as ESM from a CDN in the
        # browser on demand (so listing none costs nothing); friendly names
        # (``d3``, ``lodash``, ``framer-motion`` / ``motion``, ``lucide`` /
        # ``lucide-react``, ``date-fns``) map to pinned, React-externalised
        # builds, and any other name is passed through to esm.sh. The component
        # reads them as ``libs`` (e.g. ``const d3 = libs.d3``).
        self._libs = [str(s) for s in (scope or [])]
        # Props handed to the component (and merged by ``update``). Carried to the
        # browser as a JSON string prop so they persist in the shape and replay to
        # a reconnecting client.
        self._data = dict(props or {})
        # Inbound ``canvas.send`` payloads are routed by ``payload[event_key]``;
        # the ``None`` slot holds catch-all handlers (``on_message`` / ``on()``).
        self._event_key = event_key
        self._routes = {None: list(self._callbacks)}
        # Request/response handlers for ``canvas.request(data)`` (see on_request):
        # event value -> the single handler whose *return value* is the reply.
        # Unlike ``_routes`` exactly one handler answers, so it's not a list.
        self._request_routes = {}
        # h="auto"/w="auto": fit the panel height/width to the rendered React
        # content. Unlike Custom (which measures inside its iframe), a native
        # React panel is measured by ReactHost, which reports the content size
        # back to resize the shape. The flags ride along in register_props as
        # ``autoH``/``autoW``.
        self._auto_h = False
        self._auto_w = False

    def register_props(self):
        props = dict(self._props)  # label, w, h
        props["source"] = self._source
        props["data"] = json.dumps(self._data)
        props["autoH"] = self._auto_h
        props["autoW"] = self._auto_w
        props["libs"] = json.dumps(self._libs)
        return props

    def _set_auto_h(self):
        """Enable content-fit height live (``comp.h = "auto"``).

        Flips the panel into auto-height and tells the running ReactHost to start
        measuring and reporting its content height. A no-op if already auto. (To
        pin a fixed height again, assign a number: ``comp.h = 240``.)
        """
        if self._auto_h:
            return
        self._auto_h = True
        self._send_update({"autoH": True})

    def _set_fixed_h(self, value):
        """Pin the height to a number, leaving auto-height mode if it was on (so
        the value isn't immediately overridden by the content fit)."""
        if self._auto_h:
            self._auto_h = False
            self._send_update({"autoH": False})
        self.set_layout(h=value)

    def _set_auto_w(self):
        """Enable content-fit width live (``comp.w = "auto"``).

        The width twin of :meth:`_set_auto_h`: flips the panel into auto-width
        and tells the running ReactHost to measure the content's natural width
        and report it back. A no-op if already auto. (To pin a fixed width again,
        assign a number: ``comp.w = 320``.)
        """
        if self._auto_w:
            return
        self._auto_w = True
        self._send_update({"autoW": True})

    def _set_fixed_w(self, value):
        """Pin the width to a number, leaving auto-width mode if it was on (so
        the value isn't immediately overridden by the content fit)."""
        if self._auto_w:
            self._auto_w = False
            self._send_update({"autoW": False})
        self.set_layout(w=value)

    @staticmethod
    def compose(jsx="", css=""):
        """Wrap a JSX expression (plus optional CSS) into a full ``Component``.

        Used internally for the ``jsx=`` / ``css=`` constructor path, but also
        callable directly when you want the assembled source. The CSS lands in
        a ``<style>`` tag scoped only by your own selectors, and the markup is
        wrapped in a ``.react-root`` div that fills the panel.
        """
        return f"""
        function Component({{ canvas, props, value }}) {{
            return (
                <>
                    <style>{{`
                        .react-root {{ width: 100%; height: 100%; }}
                        {css}
                    `}}</style>
                    <div className="react-root">
                        {jsx}
                    </div>
                </>
            );
        }}
        """

    @staticmethod
    def from_uiverse(raw_code):
        """Convert a uiverse.io React snippet (styled-components) to panel source.

        uiverse exports React widgets as a component wrapped in a
        ``styled-components`` ``StyledWrapper``. styled-components needs an npm
        build, which the in-browser Babel pipeline doesn't have — so this
        rewrites the snippet into plain React + a ``<style>`` tag:

        * each ``const X = styled.tag`...``` definition is removed and its CSS
          collected (the panel relies on native CSS nesting, supported by all
          current browsers, since styled-components CSS is nested);
        * ``<X>``/``</X>`` usages of those styled components become plain
          ``<div className="pc-uiverse">`` wrappers carrying the collected CSS;
        * imports / ``export default`` are stripped and the remaining component
          is re-exported as the ``Component`` the panel expects.

        Returns source ready for ``React(source=...)`` / ``canvas.react(...)``.
        """
        # Collect every styled-components definition: its name and its CSS.
        styled_re = re.compile(r"const\s+(\w+)\s*=\s*styled\.\w+`([\s\S]*?)`;?")
        styled = {m.group(1): m.group(2) for m in styled_re.finditer(raw_code)}
        css = "\n".join(styled.values())

        # The exported name is the component to mount; fall back to the first
        # non-styled `const Name =` definition.
        export_match = re.search(r"export\s+default\s+(\w+)", raw_code)
        if export_match:
            original_name = export_match.group(1)
        else:
            names = [m.group(1)
                     for m in re.finditer(r"const\s+(\w+)\s*=", raw_code)
                     if m.group(1) not in styled]
            original_name = names[0] if names else "UiverseComponent"

        clean = styled_re.sub("", raw_code)
        clean = re.sub(r"^\s*import\b.*$", "", clean, flags=re.MULTILINE)
        clean = re.sub(r"^\s*export\s+default\b.*$", "", clean, flags=re.MULTILINE)
        # Styled tags become plain divs; the class on the outer wrapper below
        # scopes the collected CSS to this panel.
        for name in styled:
            clean = re.sub(rf"<{name}(\s|>)", r"<div\1", clean)
            clean = clean.replace(f"</{name}>", "</div>")

        return f"""
        {clean}

        function Component({{ canvas, props, value }}) {{
            return (
                <>
                    <style>{{`
                        .pc-uiverse {{ {css} }}
                    `}}</style>
                    <div className="pc-uiverse">
                        <{original_name} {{...props}} />
                    </div>
                </>
            );
        }}
        """

    # -- write (Python -> panel) ---------------------------------------------
    def update(self, **props):
        """Patch the component's ``props`` and re-render, live.

        Merges ``props`` into the current set (so ``update(label="Hi")`` leaves
        the rest untouched) and pushes the merged dict to the panel.
        """
        self._data.update(props)
        self._send_update({"data": json.dumps(self._data)})

    def push(self, data):
        """Stream ``data`` to the component without a re-mount.

        Like :meth:`Custom.push`, this bypasses shape props (no churn / reconnect
        replay) and suits high-rate updates. The component sees it as ``value``
        by default; for high-rate or binary streams it can instead subscribe via
        ``canvas.onFrame(cb)`` (in a ``useEffect``) and paint each frame itself —
        that path skips the React re-render the ``value`` prop would trigger.
        """
        self._send_update({"post": data})

    def push_binary(self, data):
        """Stream raw bytes to the component on a **binary** WebSocket frame.

        The high-throughput counterpart to :meth:`push`: instead of JSON-encoding
        the payload, ``data`` (``bytes``/``bytearray``/``memoryview``) rides a
        binary frame — no JSON serialize, no base64 — the same fast path
        ``VideoFeed``/``AudioFeed``/``Custom.push_binary`` use. It arrives at a
        ``canvas.onFrame`` subscriber as a zero-copy ``ArrayBuffer`` (so use
        ``onFrame``, not the ``value`` prop, to receive it), ready to wrap in a
        typed array — e.g. ``new Float32Array(buf)``.

        Use it for frame- or array-grade telemetry (packed sensor buffers, a
        custom codec) where per-sample JSON cost would dominate. Honours the
        panel's ``queue`` policy, so ``queue="latest"`` drops stale buffers for a
        slow viewer just as it does for video.
        """
        self._send_binary(BINARY_REACT, bytes(data))

    def set_source(self, source):
        """Replace the component's JSX source and recompile it, live."""
        self._source = source
        self._send_update({"source": source})

    # -- input routing (panel -> Python) -------------------------------------
    def on(self, event=None):
        """Decorator: handle inbound ``canvas.send`` messages.

        ``@panel.on("tick")`` fires only for messages whose ``event`` field (see
        ``event_key``) equals ``"tick"``; ``@panel.on()`` is a catch-all. The
        handler gets the full payload dict.
        """
        def deco(fn):
            self._routes.setdefault(event, []).append(fn)
            return fn
        return deco

    def on_message(self, fn):
        """Decorator: handle *every* inbound message (a catch-all ``on()``)."""
        self._routes.setdefault(None, []).append(fn)
        return fn

    def on_request(self, event=None):
        """Decorator: *answer* a panel's ``await canvas.request(data)`` call.

        Where :meth:`on` is fire-and-forget, this is request/response: the handler
        receives the request ``data`` and its **return value** is sent back to
        resolve the panel's Promise — for ask-Python-and-use-the-answer flows
        (validate a field, fetch a row, compute server-side). Routed by
        ``data[event_key]`` like :meth:`on` (``@panel.on_request("validate")``);
        ``@panel.on_request()`` is the catch-all. Exactly one handler answers (the
        keyed one, else the catch-all), so registering the same key again replaces
        it. A handler that raises rejects the Promise with the error; the return
        value must be JSON-serialisable.

            @panel.on_request("factorize")
            def _(req): return {"factors": factorize(req["n"])}
            # in JSX:  const { factors } = await canvas.request({event:'factorize', n})
        """
        def deco(fn):
            self._request_routes[event] = fn
            return fn
        return deco

    def _handle_request(self, data):
        """Resolve a ``canvas.request`` payload to a reply value (bridge entry).

        Returns the matching handler's value; raises if none is registered — the
        bridge turns the return into a ``response`` (resolving the panel's Promise)
        and an exception into an error ``response`` (rejecting it).
        """
        event = data.get(self._event_key) if isinstance(data, dict) else None
        handler = self._request_routes.get(event)
        if handler is None:
            handler = self._request_routes.get(None)
        if handler is None:
            raise LookupError(f"no on_request handler for event {event!r}")
        return handler(data)

    def _handle_input(self, payload):
        with self._lock:
            self._value = payload
        event = payload.get(self._event_key) if isinstance(payload, dict) else None
        handlers = list(self._routes.get(event, []))
        if event is not None:
            handlers += self._routes.get(None, [])
        for cb in handlers:
            try:
                cb(payload)
            except Exception:
                traceback.print_exc()
