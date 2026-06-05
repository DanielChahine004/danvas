"""Canvas: the public entry point. Holds components and serves the app."""

import json
import time
import uuid
import warnings

from . import server
from .bridge import Bridge
from .kernel import Kernel


# Friendly snake_case names mapped onto tldraw's arrow shape prop names.
# ``label`` is the conventional caption (tldraw stores it in the ``text`` prop).
_ARROW_PROP_ALIASES = {
    "label": "text",
    "arrowhead_start": "arrowheadStart",
    "arrowhead_end": "arrowheadEnd",
    "label_color": "labelColor",
}


def _arrow_props(props):
    """Translate snake_case kwargs to tldraw arrow prop names."""
    return {_ARROW_PROP_ALIASES.get(k, k): v for k, v in props.items()}


class Arrow:
    """A connector between two panels, managed much like a component.

    Returned by :meth:`Canvas.connect`. The arrow binds to each panel in tldraw,
    so it reroutes automatically as the panels move or resize. Like a component
    it takes a ``label`` (shown on the arrow, and used for ``canvas.<label>``
    lookup), and it is bound to the canvas bridge so its appearance can be
    changed live::

        a = canvas.connect(src, dst, label="flow", color="blue")
        a.color = "red"               # or a.update(color="red")
        a.update(dash="dashed", label="x2")

    Valid tldraw values: ``color`` one of black/grey/violet/light-violet/blue/
    light-blue/yellow/orange/green/light-green/light-red/red/white; ``dash`` one
    of draw/solid/dashed/dotted; ``size`` one of s/m/l/xl; ``arrowhead_start`` /
    ``arrowhead_end`` one of none/arrow/triangle/square/dot/pipe/diamond/
    inverted/bar; ``bend`` a number.

    Pass it (or its ``label``) to :meth:`Canvas.disconnect` to remove it.
    """

    def __init__(self, arrow_id, start, end, bridge, props=None, label=None, name=None):
        self.id = arrow_id
        self.start = start
        self.end = end
        self.name = name
        self._bridge = bridge
        self._props = dict(props or {})
        if label is not None:
            self._props.setdefault("text", label)

    def register_message(self):
        """Build the ``arrow`` register message (current props included)."""
        return {
            "type": "arrow",
            "id": self.id,
            "start": self.start.id,
            "end": self.end.id,
            "props": dict(self._props),
        }

    def update(self, **props):
        """Change arrow properties live (color, text, dash, size, bend, ...).

        Accepts the friendly names in the class docstring. Stored so a
        reconnecting client replays the new appearance.
        """
        props = _arrow_props(props)
        self._props.update(props)
        if self._bridge is not None:
            self._bridge.broadcast(
                {"type": "update", "id": self.id, "payload": props}
            )
        return self

    # -- convenience accessors for the common props --------------------------
    @property
    def color(self):
        return self._props.get("color")

    @color.setter
    def color(self, value):
        self.update(color=value)

    @property
    def label(self):
        """The text shown on the arrow (tldraw's ``text`` prop)."""
        return self._props.get("text")

    @label.setter
    def label(self, value):
        self.update(text=value)

    @property
    def dash(self):
        return self._props.get("dash")

    @dash.setter
    def dash(self, value):
        self.update(dash=value)

    @property
    def size(self):
        return self._props.get("size")

    @size.setter
    def size(self, value):
        self.update(size=value)

    @property
    def bend(self):
        return self._props.get("bend")

    @bend.setter
    def bend(self, value):
        self.update(bend=value)


class Canvas:
    def __init__(self):
        self._bridge = Bridge()
        self._components = []
        self._arrows = []
        self._named = {}  # name -> component, for canvas.<name> / canvas["<name>"]
        self._serving = False
        self._server = None
        # Shared by all Repl cells: one kernel thread runs their code serially
        # against one namespace (set by enable_repl). None until enable_repl.
        self._kernel = Kernel()
        self._namespace = None

    def enable_repl(self, namespace=None):
        """Bind the namespace that ``Repl`` cells execute against.

        Call this before inserting any :class:`~pycanvas.Repl`. Pass
        ``globals()`` from your notebook/script to share its variables with the
        on-canvas cells; omit it to auto-detect the IPython user namespace (or
        an empty namespace outside IPython). ``canvas`` is always made available
        inside it so cells can write ``canvas.<panel>`` straight away.

        Because a REPL runs arbitrary Python in this process, it is gated to
        local-only serving unless ``serve(..., allow_remote_exec=True)``.
        """
        if namespace is None:
            # Import get_ipython rather than relying on the bare builtin, which
            # IPython only installs while a cell is executing -- the imported
            # function returns the live shell from any context. Falls back to an
            # empty namespace when not running under IPython.
            try:
                from IPython import get_ipython
                ip = get_ipython()
            except ImportError:
                ip = None
            namespace = ip.user_ns if ip is not None else {}
        namespace.setdefault("canvas", self)
        self._namespace = namespace
        return self

    def insert(self, component, x=None, y=None, w=None, h=None, rotation=None,
               locked=False, movable=True, resizable=True, name=None):
        """Register a component on the canvas and return it.

        ``x``/``y`` set the panel's position in canvas coordinates; omit them to
        let the frontend auto-cascade. ``w``/``h`` set its size in pixels;
        omit them to use the component's default size.

        Three independent lock controls:

        - ``locked=True`` fully locks the panel — no move, resize, or
          interaction (toggle later with ``component.lock()`` / ``unlock()``).
        - ``movable=False`` stops the user dragging the panel but keeps its
          controls interactive (toggle with ``component.movable``).
        - ``resizable=False`` stops the user resizing it, controls still work
          (toggle with ``component.resizable``).

        Use ``movable=False, resizable=False`` (or ``component.pin()``) to pin an
        interactive panel in place. Python ``move()``/``resize()`` still work
        regardless of these — they only gate user gestures.

        ``name`` (or the component's label, if a valid identifier) exposes the
        component as ``canvas.<name>`` and ``canvas["<name>"]``.

        When called after the server is already running (``serve_background``),
        the component is pushed live to connected clients instead of only
        appearing on the next page load.
        """
        if name is None:
            label = component._props.get("label")
            if isinstance(label, str) and label.isidentifier():
                name = label
        if name is not None:
            # Names are unique handles. If something else already holds this
            # name (a prior component, or this component in an earlier state),
            # pull it off the canvas first so the stale panel disappears from
            # the UI instead of lingering unreferenced. The newcomer then takes
            # over the name and is the only panel rendered for it.
            old = self._named.get(name)
            if old is not None and old is not component:
                warnings.warn(
                    f"name {name!r} already used by a "
                    f"{old.__class__.__name__}; removing it and rebinding the "
                    f"name to the new {component.__class__.__name__}",
                    stacklevel=2,
                )
                if old in self._components:
                    self.remove(old)
                elif old in self._arrows:
                    self.disconnect(old)
            self._named[name] = component
        if x is not None and y is not None:
            component._position = (x, y)
        if w is not None:
            component._props["w"] = w
        if h is not None:
            component._props["h"] = h
        if rotation is not None:
            component._rotation = rotation
        if locked:
            component._locked = True
        if not movable:
            component._movable = False
        if not resizable:
            component._resizable = False
        component_id = uuid.uuid4().hex
        component._bind(component_id, self._bridge)
        self._bridge.add_component(component)
        self._components.append(component)
        # Wire the execution/introspection components to canvas-level resources.
        # Duck-typed so the common components stay untouched: a Repl exposes a
        # ``_kernel`` slot (shared kernel), components that read the shared REPL
        # namespace a ``_namespace`` slot (Repl, globals-mode Inspector), and an
        # Inspector a ``_canvas`` slot (to read live component state).
        if getattr(component, "_kernel", "missing") is None:
            component._kernel = self._kernel
        if getattr(component, "_namespace", "missing") is None:
            component._namespace = self._namespace
        if getattr(component, "_canvas", "missing") is None:
            component._canvas = self
        # Optional lifecycle hook: fired once the component is fully wired (id,
        # bridge, kernel/canvas). Inspector uses it to start its refresh ticker.
        on_attached = getattr(component, "_on_attached", None)
        if callable(on_attached):
            on_attached()
        if self._serving:
            self._bridge.register_live(component)
        return component

    def remove(self, component):
        """Pull a panel off the canvas. Works live while serving.

        Safe to call with a component that was already removed or never
        inserted; in that case it is a no-op.
        """
        if component not in self._components:
            return
        self._components.remove(component)
        for nm, comp in list(self._named.items()):
            if comp is component:
                del self._named[nm]
        self._bridge.remove_component(component.id)
        component._bridge = None
        on_removed = getattr(component, "_on_removed", None)
        if callable(on_removed):
            on_removed()
        return component

    def connect(self, start, end, label=None, name=None, **props):
        """Draw an arrow from panel ``start`` to panel ``end`` and return it.

        Both arguments are components previously passed to :meth:`insert`. The
        arrow binds to each panel in tldraw, so it follows them as they move or
        resize. Like a component, ``label`` captions the arrow and (when a valid
        identifier) exposes it as ``canvas.<label>`` / ``canvas["<label>"]``;
        ``name`` overrides that lookup key. Extra keyword args set its appearance
        (``color``, ``dash``, ``size``, ``bend``, ``arrowhead_start`` /
        ``arrowhead_end``; see :class:`Arrow`). Works live while serving.
        """
        if start.id is None or end.id is None:
            raise ValueError("both panels must be inserted before connecting them")
        if name is None and isinstance(label, str) and label.isidentifier():
            name = label
        arrow_id = uuid.uuid4().hex
        arrow = Arrow(
            arrow_id, start, end, self._bridge,
            props=_arrow_props(props), label=label, name=name,
        )
        self._arrows.append(arrow)
        if name is not None:
            # Same unique-name rule as insert: evict whatever currently holds
            # this name so the stale shape leaves the UI before the new arrow.
            old = self._named.get(name)
            if old is not None and old is not arrow:
                warnings.warn(
                    f"name {name!r} already used by a "
                    f"{old.__class__.__name__}; removing it and rebinding the "
                    f"name to the new Arrow",
                    stacklevel=2,
                )
                if old in self._arrows:
                    self.disconnect(old)
                elif old in self._components:
                    self.remove(old)
            self._named[name] = arrow
        self._bridge.add_arrow(arrow)
        return arrow

    def disconnect(self, arrow):
        """Remove an arrow returned by :meth:`connect`, by object or by name.

        Works live while serving. Safe to call with an arrow (or name) that was
        already removed or never created; then it is a no-op.
        """
        if isinstance(arrow, str):
            arrow = self._named.get(arrow)
        if arrow is None or arrow not in self._arrows:
            return
        self._arrows.remove(arrow)
        for nm, obj in list(self._named.items()):
            if obj is arrow:
                del self._named[nm]
        self._bridge.remove_arrow(arrow.id)
        arrow._bridge = None
        return arrow

    # -- save / load ----------------------------------------------------------
    def save(self, path, timeout=5.0):
        """Save the canvas to one JSON file: panel formation + user drawings.

        Two things are written together:

        - ``layout`` — every panel's geometry and lock state (read from Python,
          which tracks the user's live drags/resizes). Panels are code, so only
          their *placement* is saved, never their behaviour.
        - ``drawings`` — the free-form shapes/text/arrows the user added in the
          UI, which have no Python counterpart. Captured from a connected
          browser (the source of truth), so an open page is needed for these;
          with no browser open the formation is still saved on its own.

        Reload it with :meth:`load`.
        """
        data = {"layout": self._layout()}
        try:
            drawings = self._bridge.request_snapshot(timeout=timeout)
        except RuntimeError:
            drawings = None  # no browser connected — save the formation only
        if drawings is not None:
            data["drawings"] = drawings
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return self

    def load(self, source, formation=True):
        """Restore a canvas saved by :meth:`save` (a dict or path to JSON).

        Recreate your panels in code first, then call this: it snaps them back
        into their saved formation (matched by id, then by label across runs)
        and lays the saved drawings on top. Bound arrows follow their panels
        automatically. Applies live and replays to clients that connect/reload.

        Pass ``formation=False`` to load only the user-made drawings and leave
        your panels wherever your code placed them (the saved formation is
        ignored).
        """
        data = source if isinstance(source, dict) else self._read_json(source)
        if formation and data.get("layout"):
            self._restore_layout(data["layout"])
        if data.get("drawings"):
            self._bridge.load_snapshot(data["drawings"])
        return self

    def _layout(self):
        """Build the formation dict: each panel's geometry and lock state."""
        components = [
            {
                "label": c._props.get("label"),
                "id": c.id,
                "x": c.x,
                "y": c.y,
                "w": c.w,
                "h": c.h,
                "rotation": c.rotation,
                "locked": c.locked,
                "movable": c.movable,
                "resizable": c.resizable,
            }
            for c in self._components
        ]
        arrows = [
            {
                "label": a.label,
                "start": a.start._props.get("label"),
                "end": a.end._props.get("label"),
                "props": dict(a._props),
            }
            for a in self._arrows
        ]
        return {"components": components, "arrows": arrows}

    def _restore_layout(self, data):
        """Apply a formation dict (from :meth:`_layout`) onto live panels."""
        by_id = {c.id: c for c in self._components}
        by_label = {c._props.get("label"): c for c in self._components}
        for item in data.get("components", []):
            comp = by_id.get(item.get("id")) or by_label.get(item.get("label"))
            if comp is None:
                continue
            comp.set_layout(
                x=item.get("x"),
                y=item.get("y"),
                w=item.get("w"),
                h=item.get("h"),
                rotation=item.get("rotation"),
                locked=item.get("locked"),
                movable=item.get("movable"),
                resizable=item.get("resizable"),
            )

    @staticmethod
    def _read_json(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def wait_for_client(self, timeout=10.0):
        """Block until at least one browser is connected, or ``timeout`` elapses.

        Useful before :meth:`load_canvas`, which pushes to connected clients —
        give the freshly opened page a moment to connect first. Returns ``True``
        if a client connected.
        """
        deadline = time.monotonic() + timeout
        while not self._bridge._connections and time.monotonic() < deadline:
            time.sleep(0.05)
        return bool(self._bridge._connections)

    def __getattr__(self, name):
        # Only reached when normal attribute lookup fails. _named is set in
        # __init__, but guard against lookups during unpickling/early init.
        named = self.__dict__.get("_named", {})
        if name in named:
            return named[name]
        raise AttributeError(name)

    def __getitem__(self, name):
        return self._named[name]

    def _check_remote_exec(self, host, allow_remote_exec):
        """Refuse to expose a code-executing canvas on a non-local address.

        A :class:`~pycanvas.Repl` runs arbitrary Python in this process; serving
        it on anything but loopback hands remote browsers code execution with no
        auth. Block that unless the caller explicitly opts in.
        """
        if host in ("127.0.0.1", "localhost") or allow_remote_exec:
            return
        if any(c.component == "Repl" for c in self._components):
            raise RuntimeError(
                f"a Repl cell executes arbitrary Python in this process; refusing "
                f"to serve it on host={host!r} (no auth). Bind to '127.0.0.1', or "
                f"pass allow_remote_exec=True if you really intend remote code "
                f"execution on a trusted network."
            )

    def serve(self, port=8000, open_browser=True, host="127.0.0.1",
              allow_remote_exec=False):
        """Start the server, open the browser, and block until shutdown.

        ``host`` is the bind address. The default ``"127.0.0.1"`` is local-only;
        pass ``"0.0.0.0"`` to let other devices on your network connect at
        ``http://<this-machine-ip>:<port>``. If any ``Repl`` is on the canvas,
        non-local serving is refused unless ``allow_remote_exec=True`` (a REPL is
        unauthenticated remote code execution).
        """
        self._check_remote_exec(host, allow_remote_exec)
        self._serving = True
        server.run(self._bridge, port=port, open_browser=open_browser, host=host)

    def serve_background(self, port=8000, open_browser=True, wait=True, host="127.0.0.1",
                         allow_remote_exec=False):
        """Start the server without blocking; return ``self`` for chaining.

        Intended for interactive sessions (e.g. Jupyter): the call returns so
        further ``insert`` calls push panels onto the live canvas. When
        ``wait`` is true, block briefly until the server's event loop is ready
        so the first post-serve insert is guaranteed to broadcast.

        ``host`` is the bind address; pass ``"0.0.0.0"`` for LAN access (see
        ``serve``). A ``Repl`` on the canvas blocks non-local serving unless
        ``allow_remote_exec=True``.
        """
        self._check_remote_exec(host, allow_remote_exec)
        self._server = server.run_background(
            self._bridge, port=port, open_browser=open_browser, host=host
        )
        if wait:
            self._wait_until_ready()
        self._serving = True
        return self

    def stop(self):
        """Signal the background server to shut down."""
        if self._server is not None:
            self._server.should_exit = True

    def _wait_until_ready(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while self._bridge._loop is None and time.monotonic() < deadline:
            time.sleep(0.02)
