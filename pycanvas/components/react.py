"""React: a user-authored React component rendered as a native canvas panel.

The native counterpart to :class:`Custom`. Where ``Custom`` renders arbitrary
HTML in a *sandboxed iframe* (isolated, no theme or bridge access), ``React``
takes JSX *source* and mounts it as an ordinary React subtree **inside the
panel** — so it inherits the canvas theme, dark mode, and selection chrome, and
talks to Python directly with no postMessage hop. The JSX is compiled in the
browser at runtime (Babel, lazily loaded), so users author components from
Python with no ``npm`` build.

The component must be named ``Component`` and receives three props:

  * ``canvas`` — ``{ send(data) }``: panel → Python, routed to your handlers;
  * ``value``  — the latest :meth:`push` data: Python → panel, no reload;
  * ``props``  — the dict from :meth:`update` / the ``props=`` arg: Python → panel,
    replayed on reconnect.

``React`` (with hooks) is in scope as ``React``.

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

from .base import BaseComponent


class React(BaseComponent):
    component = "React"

    default_w = 380
    default_h = 320

    def __init__(self, source=None, path=None, jsx=None, css=None, name="react",
                 label=None, w=None, h=None, props=None, event_key="event",
                 queue="fifo"):
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
        # Props handed to the component (and merged by ``update``). Carried to the
        # browser as a JSON string prop so they persist in the shape and replay to
        # a reconnecting client.
        self._data = dict(props or {})
        # Inbound ``canvas.send`` payloads are routed by ``payload[event_key]``;
        # the ``None`` slot holds catch-all handlers (``on_message`` / ``on()``).
        self._event_key = event_key
        self._routes = {None: list(self._callbacks)}
        # h="auto": fit the panel height to the rendered React content. Unlike
        # Custom (which measures inside its iframe), a native React panel is
        # measured by ReactHost, which reports the content height back to resize
        # the shape. The flag rides along in register_props as ``autoH``.
        self._auto_h = False

    def register_props(self):
        props = dict(self._props)  # label, w, h
        props["source"] = self._source
        props["data"] = json.dumps(self._data)
        props["autoH"] = self._auto_h
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
        """Stream ``data`` to the component's ``value`` prop without a re-mount.

        Like :meth:`Custom.push`, this bypasses shape props (no churn / reconnect
        replay) and suits high-rate updates; the component sees it as ``value``.
        """
        self._send_update({"post": data})

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
