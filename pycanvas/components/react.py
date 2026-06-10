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

from .base import BaseComponent


class React(BaseComponent):
    component = "React"

    def __init__(self, source=None, path=None, name="react", label=None,
                 width=380, height=320, props=None, event_key="event",
                 queue="fifo"):
        super().__init__(name=name, label=label, w=width, h=height, queue=queue)
        if path is not None:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
        self._source = source or ""
        # Props handed to the component (and merged by ``update``). Carried to the
        # browser as a JSON string prop so they persist in the shape and replay to
        # a reconnecting client.
        self._data = dict(props or {})
        # Inbound ``canvas.send`` payloads are routed by ``payload[event_key]``;
        # the ``None`` slot holds catch-all handlers (``on_message`` / ``on()``).
        self._event_key = event_key
        self._routes = {None: list(self._callbacks)}

    def register_props(self):
        props = dict(self._props)  # label, w, h
        props["source"] = self._source
        props["data"] = json.dumps(self._data)
        return props

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
