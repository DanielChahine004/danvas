"""Canvas: the public entry point. Holds components and serves the app."""

import json
import time
import uuid
import warnings

from . import server
from .bridge import Bridge
from .components import (
    AudioFeed,
    Chat,
    Custom,
    Inspector,
    Label,
    LivePlot,
    Plot,
    Repl,
    Slider,
    Toggle,
    VideoFeed,
    WebView,
)
from .kernel import Kernel

# Keyword names consumed by ``Canvas.insert`` itself. A factory method splits
# these off and forwards everything else to the component constructor.
# ``name`` is intentionally absent: it is the component's identity and travels on
# the component itself (set in its constructor), not a placement option.
_INSERT_KEYS = ("x", "y", "w", "h", "rotation", "locked", "movable",
                "resizable", "interactive")


# Friendly snake_case names mapped onto tldraw's arrow shape prop names. The
# arrow's ``name`` (its identity / eviction key) is handled separately and never
# sent as a shape prop; ``text`` is the caption tldraw actually draws.
_ARROW_PROP_ALIASES = {
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
    so it reroutes automatically as the panels move or resize. It is bound to the
    canvas bridge so its appearance can be changed live::

        a = canvas.connect(src, dst, name="flow", text="x1", color="blue")
        a.color = "red"               # or a.update(color="red")
        a.text = "x2"                 # change the visible caption live
        a.update(dash="dashed", text="x3")

    ``name`` is the arrow's **identity**: the ``canvas.<name>`` handle and the
    eviction key, so connecting again under the same ``name`` destroys the
    previous arrow and makes the new one the reference. Omit it and the name is
    derived from the endpoints (``"<start.name>-><end.name>"``), so re-connecting
    the same two panels replaces the old arrow rather than duplicating it.
    ``text`` is the
    **caption** drawn on the arrow; it is purely cosmetic and may change freely
    without affecting identity. When ``text`` is omitted the arrow shows no
    caption (the identity is never drawn).

    Valid tldraw values: ``color`` one of black/grey/violet/light-violet/blue/
    light-blue/yellow/orange/green/light-green/light-red/red/white; ``dash`` one
    of draw/solid/dashed/dotted; ``size`` one of s/m/l/xl; ``arrowhead_start`` /
    ``arrowhead_end`` one of none/arrow/triangle/square/dot/pipe/diamond/
    inverted/bar; ``bend`` a number.

    Pass it (or its ``name``) to :meth:`Canvas.disconnect` to remove it.
    """

    def __init__(self, arrow_id, start, end, bridge, props=None,
                 name=None, text=None):
        self.id = arrow_id
        self.start = start
        self.end = end
        self.name = name    # unique identity / canvas.<name> handle / eviction key
        self._bridge = bridge
        self._props = dict(props or {})
        # ``text`` is the visible caption, kept distinct from the identity. When
        # omitted the arrow shows no caption (the identity is never drawn).
        if text is not None:
            self._props["text"] = text

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
    def text(self):
        """The caption drawn on the arrow (tldraw's ``text`` prop)."""
        return self._props.get("text")

    @text.setter
    def text(self, value):
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
        # Let the bridge call back into the canvas for native-UI actions (the
        # toolbar Inspector toggle); harmless until serve() enables the feature.
        self._bridge._canvas = self
        self._components = []
        self._arrows = []
        self._named = {}  # name -> component, for canvas.<name> / canvas["<name>"]
        self._serving = False
        self._server = None
        self._tunnel = None
        # Shared by all Repl cells: one kernel thread runs their code serially
        # against one namespace (set by enable_repl). None until enable_repl.
        self._kernel = Kernel()
        self._namespace = None
        # Set by capture_cells()/autopanel() to the active CellCapture, so a
        # second call is idempotent and stop_capturing_cells() can find it.
        self._cell_capture = None

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

    def capture_cells(self, cols=3, slot_w=520, slot_h=420, gap=40,
                      origin=(0, 0), include_source=True, auto=True,
                      movable=True, resizable=True, locked=False,
                      interactive=True):
        """Mirror subsequent notebook cell outputs onto this canvas.

        Registers an IPython ``post_run_cell`` hook so each cell ending in an
        expression gets (or refreshes) its own panel, auto-arranged in a grid —
        no manual :meth:`insert` per cell. Cells ending in a statement
        (assignment, ``print``, loop) produce no value and are skipped. Re-running
        a cell swaps its panel in place. Best paired with ``serve(block=False)``
        so panels broadcast live. See :func:`pycanvas.autopanel` for the
        arguments; returns the capture controller. Idempotent.

        Per cell, a ``# pycanvas:`` directive line overrides placement (or opts
        out with ``skip``). Pass ``auto=False`` to invert the default: mirror
        *nothing* unless a cell carries such a directive (e.g. a bare
        ``# pycanvas: show``) — an explicit allowlist instead of a blocklist.

        ``movable``/``resizable``/``locked``/``interactive`` set the default lock
        state for every panel (e.g. ``movable=False`` to pin them all); a
        per-cell directive overrides them.

        Stop with :meth:`stop_capturing_cells`.
        """
        from .autopanel import autopanel

        return autopanel(self, cols=cols, slot_w=slot_w, slot_h=slot_h,
                         gap=gap, origin=origin, include_source=include_source,
                         auto=auto, movable=movable, resizable=resizable,
                         locked=locked, interactive=interactive)

    def stop_capturing_cells(self):
        """Stop mirroring cell outputs (unregister the ``post_run_cell`` hook).

        A no-op if :meth:`capture_cells` was never called. Existing panels stay
        on the canvas.
        """
        if self._cell_capture is not None:
            self._cell_capture.stop()
            self._cell_capture = None
        return self

    def _toggle_ui_inspector(self):
        """Spawn (or remove) the native-UI ephemeral Inspector. Toggles.

        Called by the bridge when a browser hits the toolbar Inspector button
        (only when :meth:`serve` enabled it). The panel is a normal
        :class:`~pycanvas.Inspector` under a reserved name, so re-toggling
        removes it and it broadcasts to every open view like any other panel.
        Returns the inspector when spawned, ``None`` when removed.
        """
        name = "__ui_inspector__"
        existing = self._named.get(name)
        if existing is not None:
            self.remove(existing)
            return None
        return self.insert(
            Inspector(name=name, refresh=1.0, source="components",
                      label="inspector"),
            x=120, y=120,
        )

    def insert(self, component, x=None, y=None, w=None, h=None, rotation=None,
               locked=False, movable=True, resizable=True, interactive=True,
               name=None):
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

        ``name`` is the component's unique identity — the handle that exposes it
        as ``canvas.<name>`` / ``canvas["<name>"]`` and the key that makes a later
        insert under the same name replace this one (so re-running a cell swaps
        the panel rather than stacking a duplicate). It normally comes from the
        component's own ``name`` (required on the input components); passing
        ``name`` here overrides that. The component's ``label`` is purely the
        displayed caption and defaults to the name.

        When called after the server is already running (``serve(block=False)``),
        the component is pushed live to connected clients instead of only
        appearing on the next page load.
        """
        # ``name`` is the component's unique identity: the ``canvas.<name>`` /
        # ``canvas["<name>"]`` handle and the eviction key. It normally rides on
        # the component (set in its constructor); the ``name`` arg here overrides
        # that, and a label/auto fallback covers hand-built components that set
        # neither.
        if name is None:
            name = component.name
        if name is None:
            label = component._props.get("label")
            name = label if isinstance(label, str) and label else self._auto_name(
                component.component)
        # Names are unique handles. If something else already holds this name (a
        # prior component, or this component in an earlier state), pull it off the
        # canvas first so the stale panel disappears from the UI instead of
        # lingering unreferenced. The newcomer then takes over the name and is the
        # only panel rendered for it.
        old = self._named.get(name)
        if old is not None and old is not component:
            warnings.warn(
                f"name {name!r} already used by a "
                f"{old.__class__.__name__}; removing it and rebinding the "
                f"name to the new {component.__class__.__name__}",
                stacklevel=2,
            )
            # Swap-in-place: inherit the displaced panel's live position/rotation
            # when the caller didn't pin one, so re-inserting under the same name
            # (e.g. re-running a `canvas.webview(...)` cell) keeps where the user
            # dragged it instead of snapping back to auto-cascade. Size stays the
            # caller's (it's usually passed explicitly); only geometry the user
            # hand-adjusts is carried over.
            if x is None and y is None and component._position is None \
                    and old._position is not None:
                x, y = old._position
            if rotation is None and component._rotation == 0 and old._rotation:
                rotation = old._rotation
            if old in self._components:
                self.remove(old)
            elif old in self._arrows:
                self.disconnect(old)
        component.name = name
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
        if not interactive:
            component._interactive = False
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

    def _auto_name(self, kind):
        """Return a unique fallback handle (e.g. ``slider1``) for an unnamed item.

        Used when nothing supplies a name — so every component and arrow still
        gets a distinct ``canvas[...]`` handle. ``kind`` is the type string
        (``component.component`` for panels, ``"arrow"`` for connectors).
        """
        base = kind.lower()
        i = 1
        while f"{base}{i}" in self._named:
            i += 1
        return f"{base}{i}"

    # -- component factories --------------------------------------------------
    # Shorthand for ``insert(SomeComponent(...))``. Each forwards its arguments
    # to the component constructor and the placement/lock/name options (see
    # ``_INSERT_KEYS``) to :meth:`insert`, returning the inserted component.
    # ``insert`` remains the full path for hand-built or exotic components.
    def _make(self, cls, *args, **kw):
        place = {k: kw.pop(k) for k in _INSERT_KEYS if k in kw}
        return self.insert(cls(*args, **kw), **place)

    def slider(self, name, min=0, max=100, default=None, label=None, **place):
        """Insert a :class:`~pycanvas.Slider`. See :meth:`insert` for ``place``."""
        return self._make(Slider, name, min=min, max=max, default=default,
                          label=label, **place)

    def toggle(self, name, options, default=None, label=None, **place):
        """Insert a :class:`~pycanvas.Toggle`. See :meth:`insert` for ``place``."""
        return self._make(Toggle, name, options, default=default, label=label,
                          **place)

    def label(self, name, value="", label=None, **place):
        """Insert a :class:`~pycanvas.Label`. See :meth:`insert` for ``place``."""
        return self._make(Label, name, value=value, label=label, **place)

    def video(self, name, quality=70, label=None, **place):
        """Insert a :class:`~pycanvas.VideoFeed`. See :meth:`insert` for ``place``."""
        return self._make(VideoFeed, name, quality=quality, label=label, **place)

    def audio(self, name, sample_rate=16000, channels=1, label=None, **place):
        """Insert an :class:`~pycanvas.AudioFeed`. See :meth:`insert` for ``place``."""
        return self._make(AudioFeed, name, sample_rate=sample_rate,
                          channels=channels, label=label, **place)

    def chat(self, name="chat", label=None, **place):
        """Insert a :class:`~pycanvas.Chat` panel. See :meth:`insert` for ``place``."""
        return self._make(Chat, name=name, label=label, **place)

    def custom(self, html=None, path=None, name="custom", label=None, width=380,
               height=320, **place):
        """Insert a :class:`~pycanvas.Custom`. See :meth:`insert` for ``place``."""
        return self._make(Custom, html=html, path=path, name=name, label=label,
                          width=width, height=height, **place)

    def webview(self, url, name="web", label=None, width=800, height=600, **place):
        """Insert a :class:`~pycanvas.WebView`. See :meth:`insert` for ``place``."""
        return self._make(WebView, url, name=name, label=label, width=width,
                          height=height, **place)

    def plot(self, name="plot", label=None, width=560, height=420, **place):
        """Insert a :class:`~pycanvas.Plot`. See :meth:`insert` for ``place``."""
        return self._make(Plot, name=name, label=label, width=width,
                          height=height, **place)

    def live_plot(self, name="live plot", **kw):
        """Insert a :class:`~pycanvas.LivePlot`.

        Constructor kwargs (``traces``, ``max_points``, ``mode``, ``layout``,
        ``width``, ``height``, ``label``) and :meth:`insert` placement options
        both go in ``kw``; they don't overlap.
        """
        return self._make(LivePlot, name=name, **kw)

    def repl(self, name="repl", label=None, **place):
        """Insert a :class:`~pycanvas.Repl`. See :meth:`insert` for ``place``.

        Call :meth:`enable_repl` first to bind the namespace cells run against.
        """
        return self._make(Repl, name=name, label=label, **place)

    def inspector(self, name="inspector", refresh=None, source="components",
                  namespace=None, label=None, **place):
        """Insert an :class:`~pycanvas.Inspector`. See :meth:`insert` for ``place``."""
        return self._make(Inspector, name=name, refresh=refresh, source=source,
                          namespace=namespace, label=label, **place)

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

    def connect(self, start, end, name=None, text=None, **props):
        """Draw an arrow from panel ``start`` to panel ``end`` and return it.

        Both arguments are components previously passed to :meth:`insert`. The
        arrow binds to each panel in tldraw, so it follows them as they move or
        resize. ``name`` is the arrow's unique identity — the ``canvas.<name>`` /
        ``canvas["<name>"]`` handle and the eviction key, so re-connecting under
        the same ``name`` destroys the previous arrow rather than stacking a
        duplicate (mirroring how re-inserting a panel supersedes the old one).
        When omitted it defaults to ``"<start.name>-><end.name>"``, so a second
        unnamed arrow between the same two panels replaces the first. ``text`` is
        the caption drawn on the arrow (none is shown when omitted); change it
        freely with
        ``arrow.text = ...`` / ``arrow.update(text=...)`` without disturbing
        identity. Extra keyword args set its appearance (``color``, ``dash``,
        ``size``, ``bend``, ``arrowhead_start`` / ``arrowhead_end``; see
        :class:`Arrow`). Works live while serving.
        """
        if start.id is None or end.id is None:
            raise ValueError("both panels must be inserted before connecting them")
        if name is None:
            # Derive identity from the endpoints so an unnamed arrow between the
            # same two panels reuses the handle — re-connecting them destroys the
            # previous arrow instead of stacking a duplicate, no naming required.
            name = f"{start.name}->{end.name}"
        arrow_id = uuid.uuid4().hex
        arrow = Arrow(
            arrow_id, start, end, self._bridge,
            props=_arrow_props(props), name=name, text=text,
        )
        self._arrows.append(arrow)
        # Same unique-name rule as insert: evict whatever currently holds this
        # name (an earlier arrow, or a panel of another type) so the stale shape
        # leaves the UI before the new arrow takes the name over.
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
        into their saved formation (matched by id, then by name across runs)
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
                "name": c.name,
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
                "name": a.name,
                "start": a.start.name,
                "end": a.end.name,
                "props": dict(a._props),
            }
            for a in self._arrows
        ]
        return {"components": components, "arrows": arrows}

    def _restore_layout(self, data):
        """Apply a formation dict (from :meth:`_layout`) onto live panels."""
        by_id = {c.id: c for c in self._components}
        by_name = {c.name: c for c in self._components}
        for item in data.get("components", []):
            comp = by_id.get(item.get("id")) or by_name.get(item.get("name"))
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
              allow_remote_exec=False, block=True, wait=True,
              tunnel=False, tunnel_provider="cloudflared", ui_inspector=None):
        """Start the server and open the browser.

        With ``block=True`` (the default) this runs the server and blocks until
        shutdown — the usual end-of-script call. With ``block=False`` it starts
        the server in the background and returns ``self`` immediately, so further
        ``insert`` calls push panels onto the live canvas (intended for
        interactive sessions, e.g. Jupyter). In background mode, ``wait`` blocks
        briefly until the event loop is ready so the first post-serve insert is
        guaranteed to broadcast.

        Note: the background server runs in a *daemon* thread, so with
        ``block=False`` you are responsible for keeping the process alive. That
        is automatic in a notebook/REPL (the kernel lives on), but a plain script
        that ends right after ``serve(block=False)`` will exit and tear the
        server down — call :meth:`wait` to park the main thread there instead.

        ``host`` is the bind address. The default ``"127.0.0.1"`` is local-only;
        pass ``"0.0.0.0"`` to let other devices on your network connect at
        ``http://<this-machine-ip>:<port>``. If any ``Repl`` is on the canvas,
        non-local serving is refused unless ``allow_remote_exec=True`` (a REPL is
        unauthenticated remote code execution).

        Pass ``tunnel=True`` to also expose the canvas on the public internet
        through a tunnel, so anyone — not just devices on your LAN — can open the
        printed ``https://…`` URL. ``tunnel_provider`` selects the backend
        (``"cloudflared"`` by default, needs no signup and no visitor
        interstitial; ``"localtunnel"`` is also supported). A tunnel exposes the
        loopback bind to the whole internet, so it is gated for ``Repl`` exactly
        like a public bind: refused unless ``allow_remote_exec=True``. The tunnel
        is torn down when the server stops (or via :meth:`stop`).

        ``ui_inspector`` controls the native toolbar button that lets a viewer
        spawn an ephemeral :class:`~pycanvas.Inspector` from the browser. It can
        expose your component state (and, via its globals view, kernel variable
        values) to *every* connected browser, so by default it is offered only on
        a local bind (``127.0.0.1``) with no tunnel. Pass ``ui_inspector=True``
        to force it on for a shared/tunneled canvas, or ``False`` to hide it
        entirely.
        """
        # A tunnel publishes the loopback bind to the entire internet, so the
        # "127.0.0.1 is private" assumption behind the Repl gate breaks. Gate it
        # as if binding publicly.
        self._check_remote_exec("0.0.0.0" if tunnel else host, allow_remote_exec)
        # The UI Inspector exposes state to every viewer; default it on only for
        # a private, non-tunneled bind. An explicit ui_inspector overrides that.
        local = host in ("127.0.0.1", "localhost")
        self._bridge._ui_inspector = (
            bool(ui_inspector) if ui_inspector is not None
            else (local and not tunnel)
        )
        if not block:
            self._server = server.run_background(
                self._bridge, port=port, open_browser=open_browser, host=host
            )
            if wait:
                self._wait_until_ready()
            self._serving = True
            if tunnel:
                self._start_tunnel(port, tunnel_provider)
            return self
        self._serving = True
        if tunnel:
            self._start_tunnel(port, tunnel_provider)
        try:
            server.run(self._bridge, port=port, open_browser=open_browser,
                       host=host)
        finally:
            self._stop_tunnel()

    def _start_tunnel(self, port, provider):
        """Open a public tunnel to ``port`` and announce the URL."""
        from .tunnel import open_tunnel
        self._tunnel = open_tunnel(port, provider=provider)
        print(f"PyCanvas public URL: {self._tunnel.url}"
              "   <- share this with anyone, anywhere")

    def _stop_tunnel(self):
        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel = None

    def stop(self):
        """Signal the background server to shut down and close any tunnel."""
        if self._server is not None:
            self._server.should_exit = True
        self._stop_tunnel()

    def wait(self):
        """Block the calling thread until the background server shuts down.

        Use this at the end of a *script* that started the server with
        ``serve(block=False)`` (and then, say, spun up worker threads): without
        it the script would fall off the end and exit, killing the daemon server
        thread. ``Ctrl+C`` triggers a clean shutdown. A no-op if the server isn't
        running in the background (nothing to wait on).
        """
        if self._server is None:
            return
        try:
            while not self._server.should_exit:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.stop()

    def _wait_until_ready(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while self._bridge._loop is None and time.monotonic() < deadline:
            time.sleep(0.02)
