"""Shared panel state for embedded panels (Custom / React).

Native controls sync because their *value* is Python state: an edit goes up
as an input, Python stores it, the bridge echoes it to every other viewer,
and late joiners get it in the register replay. Embedded panels had props
and pushes from Python but no browser-writable slot — anything the page did
to itself (a 3D camera, a form, `useState`) stayed local to that browser.

`state` is that slot: one JSON dict per panel, shared by every viewer.

- In the page: `canvas.state` (current), `canvas.onState(fn)` (changes, and
  once on registration), `canvas.setState(patch)` (merge + share).
- In Python: `panel.state` (read), `panel.state = {...}` / `panel.set_state(**patch)`
  (write + share), `@panel.on_state` (a viewer wrote; args `(state, viewer)`).
- On the wire: the page's write is a `set_props` frame carrying the full
  `state` (last-writer-wins, like the rest of the property plane); the owner
  applies it through this property setter and broadcasts an `update` with
  `state`; the register frame carries `state` for late joiners. The writing
  browser applies its own write locally first and drops the echo of it.
"""
import inspect


class _SharedState:
    def _init_state(self, initial=None):
        self._state = dict(initial or {})
        self._state_hooks = []

    @property
    def state(self):
        """The panel's shared state dict (read; assign to replace + share)."""
        return self._state

    @state.setter
    def state(self, value):
        if not isinstance(value, dict):
            raise TypeError("state must be a dict (JSON-able)")
        self._state = dict(value)
        self._send_update({"state": self._state})

    def set_state(self, **patch):
        """Merge ``patch`` into :attr:`state` and share it with every viewer."""
        self.state = {**self._state, **patch}

    def on_state(self, fn=None):
        """Register ``fn(state, viewer)`` (or ``fn(state)``) to run when a
        VIEWER writes the state (``canvas.setState`` in the page). Python's
        own writes don't fire it. Decorator or plain call."""
        def register(f):
            self._state_hooks.append(f)
            return f
        return register(fn) if fn is not None else register

    def _fire_state(self, viewer):
        for fn in list(self._state_hooks):
            try:
                n = len(inspect.signature(fn).parameters)
            except (TypeError, ValueError):
                n = 2
            try:
                fn(self._state, viewer or {}) if n >= 2 else fn(self._state)
            except Exception:
                import traceback
                traceback.print_exc()
