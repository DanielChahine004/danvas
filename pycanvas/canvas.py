"""Canvas: the public entry point. Holds components and serves the app."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
import warnings
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    # `Unpack[Place]` types every factory's **place kwargs (PEP 692) for editor
    # autocomplete. Annotations are lazy strings (the __future__ import above), so
    # this import never runs at import time — no runtime dependency added.
    from typing_extensions import Unpack

from . import server
from ._flags import LAYOUT_FLAGS
from .arrow import Arrow, _arrow_props
from .bridge import Bridge
from .components import (
    AudioFeed,
    Button,
    Chat,
    Custom,
    Download,
    FileBrowser,
    Histogram,
    Image,
    Inspector,
    Label,
    LivePlot,
    Markdown,
    Plot,
    React,
    Repl,
    Slider,
    Table,
    Toggle,
    Upload,
    VideoFeed,
    WebView,
)
from .kernel import Kernel

# Keyword names consumed by ``Canvas.insert`` itself. A factory method splits
# these off and forwards everything else to the component constructor.
# ``name`` is intentionally absent: it is the component's identity and travels on
# the component itself (set in its constructor), not a placement option. The lock
# /chrome flags come straight from the shared LAYOUT_FLAGS table. ``queue`` lives
# here (not in every constructor) so all factories accept it uniformly.
_INSERT_KEYS = ("x", "y", "w", "h", "width", "height", "rotation", "queue",
                "below", "above", "right_of", "left_of", "gap",
                "roles", "lock_for", *LAYOUT_FLAGS)


class Place(TypedDict, total=False):
    """Placement, visibility, and backpressure options that every factory accepts
    via ``**place`` (the same set :meth:`Canvas.insert` documents). Typed only so
    editors autocomplete the keyword arguments — kept in step with ``_INSERT_KEYS``.
    """
    x: float
    y: float
    w: int | str          # pixels, or "auto" to fit content
    h: int | str
    width: int | str      # aliases for w / h
    height: int | str
    rotation: float        # degrees clockwise
    queue: str            # "fifo" | "latest"
    below: object         # a component or its name (same for the three below)
    above: object
    right_of: object
    left_of: object
    gap: int
    roles: list[str]      # login roles allowed to see the panel ([] = all)
    lock_for: list[str]   # roles that get it non-interactive (operable=False)
    locked: bool
    draggable: bool
    resizable: bool
    operable: bool
    grabbable: bool
    frame: bool


class _FlowLayout:
    """Auto-placer for panels inserted inside its ``with`` block.

    Created by :meth:`Canvas.grid` / :meth:`Canvas.column` / :meth:`Canvas.row`.
    While active (pushed on ``canvas._layout_stack``) it hands a position to any
    insert that didn't get an explicit one — see ``Canvas.insert``.

    A ``grid`` lays uniform ``slot`` cells out ``cols`` per row. A ``column`` /
    ``row`` flows along one axis and lets each panel keep its *natural* size on
    the other (a slider stays slider-tall, a button button-tall), advancing the
    cursor by the size the panel actually occupies — so a mixed control strip
    isn't squashed to one height.
    """

    def __init__(self, canvas, kind, *, cols=1, slot=(None, None), gap=16,
                 origin=(40, 40), roles=None, client_id=None):
        self._canvas = canvas
        self._kind = kind                 # "grid" | "column" | "row"
        self._cols = cols
        self._slot_w, self._slot_h = slot  # either may be None (= natural)
        self._gap = gap
        self._ox, self._oy = origin
        self._i = 0
        self._cursor = list(origin)        # running (x, y) for column/row flow
        # When set, the layout this container computes is written as a per-viewer
        # *overlay* (via set_layout) for these roles / this client, not the shared
        # base — so one role can have its own arrangement (precedence shared <
        # role < client). None = lay out the shared base, as usual.
        self._roles = roles
        self._client_id = client_id

    def __enter__(self):
        self._canvas._layout_stack.append(self)
        return self

    def __exit__(self, *exc):
        # Only unwind ourselves; nested `with` blocks pop in LIFO order anyway.
        if self._canvas._layout_stack and self._canvas._layout_stack[-1] is self:
            self._canvas._layout_stack.pop()
        return False

    def _place(self, component, w, h, auto_h):
        """Return ``(x, y, w, h)`` for the next panel inside this container.

        The slot fills in only dimensions the caller left blank (a ``None`` slot
        dimension keeps the component's own size); an ``h="auto"`` panel keeps
        fitting its content rather than being pinned to the slot height.
        """
        if w is None and self._slot_w is not None:
            w = self._slot_w
        if h is None and self._slot_h is not None and not auto_h:
            h = self._slot_h
        # The footprint this panel occupies, for advancing a column/row cursor:
        # the size it'll actually get (explicit or slot), else its own default.
        occ_w = w if w is not None else component.w
        occ_h = h if h is not None else component.h
        if self._kind == "grid":
            col, row = self._i % self._cols, self._i // self._cols
            x = self._ox + col * (self._slot_w + self._gap)
            y = self._oy + row * (self._slot_h + self._gap)
        elif self._kind == "column":
            x, y = self._ox, self._cursor[1]
            self._cursor[1] += occ_h + self._gap
        else:  # "row"
            x, y = self._cursor[0], self._oy
            self._cursor[0] += occ_w + self._gap
        self._i += 1
        return x, y, w, h


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
        # Remembered from serve() so a Repl inserted *after* the server is already
        # running (serve(block=False)) is gated the same way one present at serve
        # time is -- a public bind without allow_remote_exec must refuse it, since
        # a live insert pushes the panel straight to remote browsers.
        self._public_bind = False
        self._allow_remote_exec = False
        # Shared by all Repl cells: one kernel thread runs their code serially
        # against one namespace (set by enable_repl). None until enable_repl.
        self._kernel = Kernel()
        self._namespace = None
        # Set by capture_cells()/autopanel() to the active CellCapture, so a
        # second call is idempotent and stop_capturing_cells() can find it.
        self._cell_capture = None
        # Background worker callables registered via background(); serve() starts
        # each as a daemon thread, but only in the process that actually serves --
        # never the hot-reload monitor (which would otherwise grab the same
        # exclusive resources, e.g. a camera, and starve the real worker).
        self._background = []
        # Counter behind the auto-generated names for unnamed show() panels.
        self._show_seq = 0
        # Stack of active auto-layout containers (grid/column/row). The innermost
        # one places any panel inserted inside its `with` block that didn't get an
        # explicit x/y or relative anchor. Empty = panels auto-cascade as before.
        self._layout_stack = []

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

    def background(self, fn, *args, **kwargs):
        """Register ``fn`` to run as a daemon thread when the canvas serves.

        Use this for the producer loops that feed panels — a camera capture, a
        sensor poll, a telemetry stream. ``serve()`` starts each registered
        callable on its own daemon thread just before it begins serving, so the
        threads stop with the process and never outlive it::

            feed = canvas.video("webcam")

            @canvas.background
            def stream():
                cap = cv2.VideoCapture(0)
                while True:
                    ok, frame = cap.read()
                    if ok:
                        feed.update(frame)

            canvas.serve(hot_reload=True)

        Crucially, the thread is started only in the process that actually
        serves. Under ``serve(hot_reload=True)`` the original process becomes a
        file-watching *monitor* that respawns a worker subprocess on every edit;
        if a camera (or any single-owner resource) were opened at import time —
        the usual ``threading.Thread(...).start()`` at module scope — the monitor
        would hold it and the real worker could never acquire it. Registering the
        loop here defers it to the serving process, so hot reload works.

        Returns ``fn`` so it can be used as a decorator. ``*args``/``**kwargs``
        are forwarded to ``fn`` when the thread starts.
        """
        self._background.append((fn, args, kwargs))
        return fn

    def _start_background(self):
        """Spawn each registered background worker as a daemon thread.

        Called from serve() in the serving process only (not the hot-reload
        monitor). Daemon so they don't block interpreter shutdown / a reload's
        process teardown.
        """
        for fn, args, kwargs in self._background:
            threading.Thread(target=fn, args=args, kwargs=kwargs,
                             daemon=True).start()

    @property
    def components(self):
        """Return a list of all components on the canvas.

        Allows iteration and batch operations on all components::

            for comp in canvas.components:
                comp.move(100, 100)
        """
        return self._components

    @property
    def arrows(self):
        """Return a list of all arrows (connectors) on the canvas.

        The counterpart to :attr:`components` for the connectors added with
        :meth:`connect`.
        """
        return self._arrows

    @property
    def viewers(self):
        """Return the currently connected viewers as a list of dicts.

        Each entry has ``id`` (an opaque per-connection identifier), ``name``
        (the viewer's editable display name), ``color`` (their roster colour),
        and ``cursor`` — their last-known pointer position in canvas/page
        coords as ``{"x", "y"}``, or ``None`` until they move it. ``cursor`` is
        only populated when cursor reporting is on (``serve(cursors=True)``;
        default on for a private local bind), and reads the *latest* position,
        so a loop can sample it at its own rate::

            tip = canvas.viewers[0]["cursor"]   # {"x": ..., "y": ...} or None

        The ``id`` is what :meth:`set_view` expects for its ``client_id``
        argument, e.g. to point one viewer's camera at something without moving
        everyone else::

            for v in canvas.viewers:
                canvas.set_view(x=0, y=0, client_id=v["id"])

        The list reflects whoever is connected *right now* — a viewer who
        disconnects drops out of it, and there is no history of past viewers.
        """
        return list(self._bridge._viewers.values())

    def on_frame(self, fn):
        """Observe every WebSocket frame: ``fn(direction, msg)``. Decorator-friendly.

        The supported way to watch the wire — no monkeypatching needed.
        ``direction`` is ``"out"`` (Python → browser) or ``"in"`` (browser →
        Python); ``msg`` is the frame dict (``register``/``update``/``remove``
        going out, ``input``/``layout``/``chat``/``draw`` coming in; binary media
        frames arrive as a ``{"type": "binary", "id", "media", "bytes"}``
        summary; heartbeats are skipped). Use it to debug "why isn't my panel
        updating", to build protocol visualizations, or to log traffic::

            @canvas.on_frame
            def log(direction, msg):
                print(direction, msg["type"], msg.get("id"))

        Taps run inline on the send/receive path, so keep them fast. A tap may
        safely drive components (e.g. mirror frames into a panel) — frames the
        tap itself causes are not re-tapped. Remove with :meth:`off_frame`.
        For plain console logging, ``serve(debug=True)`` installs one for you.
        """
        return self._bridge.add_frame_tap(fn)

    def off_frame(self, fn):
        """Remove a frame observer registered with :meth:`on_frame`."""
        self._bridge.remove_frame_tap(fn)

    def on_cursor(self, fn):
        """Stream viewer cursor moves: ``fn(viewer)``. Decorator-friendly.

        Fires whenever a viewer moves their pointer, with a snapshot dict of that
        viewer — ``id``/``name``/``color`` plus ``cursor`` (``{"x", "y"}`` in
        canvas coords). Requires cursor reporting to be on (``serve(cursors=True)``;
        default on for a private local bind)::

            @canvas.on_cursor
            def _(v):
                print(v["name"], "at", v["cursor"])

        It's a high-rate stream (throttled + dead-banded client-side, but still
        many per second per viewer), so keep the handler cheap — for steady
        sampling, polling ``canvas.viewers[i]["cursor"]`` from a loop is often
        simpler. Remove with :meth:`off_cursor`.
        """
        return self._bridge.add_cursor_tap(fn)

    def off_cursor(self, fn):
        """Remove a cursor observer registered with :meth:`on_cursor`."""
        self._bridge.remove_cursor_tap(fn)

    def _debug_frame(self, direction, msg):
        """The ``serve(debug=True)`` tap: print one console line per frame."""
        arrow = "->" if direction == "out" else "<-"
        comp = self._bridge._components.get(msg.get("id"))
        name = f" {comp.name!r}" if comp is not None else ""
        try:
            body = json.dumps(msg, default=str)
        except (TypeError, ValueError):
            body = str(msg)
        if len(body) > 200:
            body = body[:200] + "...'"
        print(f"[pycanvas] {arrow} {msg.get('type', '?')}{name} {body}")

    def capture_cells(self, cols=3, slot_w=520, slot_h=420, gap=40,
                      origin=(0, 0), include_source=True, auto=True,
                      draggable=True, resizable=True, locked=False,
                      operable=True):
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

        ``draggable``/``resizable``/``locked``/``operable`` set the default lock
        state for every panel (e.g. ``draggable=False`` to pin them all); a
        per-cell directive overrides them.

        Stop with :meth:`stop_capturing_cells`.
        """
        from .autopanel import autopanel

        return autopanel(self, cols=cols, slot_w=slot_w, slot_h=slot_h,
                         gap=gap, origin=origin, include_source=include_source,
                         auto=auto, draggable=draggable, resizable=resizable,
                         locked=locked, operable=operable)

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
               locked=False, draggable=True, resizable=True, operable=True,
               grabbable=True, frame=True, name=None, queue=None,
               below=None, above=None, right_of=None, left_of=None, gap=16,
               width=None, height=None, roles=None, lock_for=None):
        """Register a component on the canvas and return it.

        ``x``/``y`` set the panel's position in canvas coordinates; omit them to
        let the frontend auto-cascade. ``w``/``h`` set its size in pixels;
        omit them to use the component's default size. ``width``/``height`` are
        accepted as aliases for ``w``/``h`` (matching the ``column(width=…)`` /
        ``row(height=…)`` spelling) — pass one spelling or the other, not both.

        Instead of absolute coordinates, place the panel relative to one
        already on the canvas: ``below=`` / ``above=`` / ``right_of=`` /
        ``left_of=`` take a component (or its name) and position this panel
        ``gap`` pixels away from it, aligned with its edge. An explicit ``x`` or
        ``y`` overrides the corresponding derived coordinate. The anchor must
        already have a position (placed with ``x``/``y``, relatively, or moved
        by a user — auto-cascaded panels have no Python-side position until a
        browser reports one).

        ``queue`` sets the component's send-queue policy under backpressure:
        ``"fifo"`` (deliver everything, in order) or ``"latest"`` (drop stale
        pending updates — right for high-rate feeds like a figure redrawn on
        every slider tick). ``None`` keeps the component's own default. Also a
        settable property: ``comp.queue = "latest"``.

        Five independent lock controls:

        - ``locked=True`` fully locks the panel — no move, resize, or
          interaction (toggle later with ``component.lock()`` / ``unlock()``).
        - ``draggable=False`` stops the user dragging the panel but keeps its
          controls interactive (toggle with ``component.draggable``).
        - ``resizable=False`` stops the user resizing it, controls still work
          (toggle with ``component.resizable``).
        - ``operable=False`` (content-heavy panels: Custom/React/WebView/
          plots…) makes the controls inert to the user while Python
          ``update()`` calls still render live — use this to lock interactive
          controls while driving them programmatically. The panel stays movable
          (toggle with ``component.operable``).
        - ``grabbable=False`` (content-heavy panels) removes the click-to-select
          cover — the panel's content is hoverable and clickable immediately —
          and makes the panel invisible to selection entirely: no click,
          marquee, or select-all ever highlights it. Move/resize it from
          Python only (toggle with ``component.grabbable``).
        - ``frame=False`` strips the panel's card chrome — background, border,
          shadow, padding, label header, and the hover/selection highlight
          rectangle — so the component's content appears to sit directly on
          the canvas (toggle with ``component.frame``). Pair with
          ``grabbable=False`` if clicking the content should never select it.

        Use ``draggable=False, resizable=False`` (or ``component.pin()``) to pin an
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
        # A Repl is unauthenticated remote code execution. serve() refuses one on
        # a public bind without allow_remote_exec, but in background mode a Repl
        # can be inserted *after* serve() -- gate that live insert too, so the
        # check can't be sidestepped by ordering.
        if self._serving and self._public_bind and not self._allow_remote_exec \
                and getattr(component, "component", None) == "Repl":
            raise RuntimeError(
                "a Repl cell executes arbitrary Python in this process; refusing "
                "to add it to a publicly-served canvas (no auth). Serve on "
                "'127.0.0.1', or pass allow_remote_exec=True to serve() if you "
                "really intend remote code execution on a trusted network."
            )
        # ``width``/``height`` alias ``w``/``h`` so the panel spelling matches the
        # container one (``column(width=…)``). Fold them in before any sizing
        # logic runs; passing both spellings of an axis is a mistake.
        if width is not None:
            if w is not None:
                raise TypeError("pass either w= or width=, not both")
            w = width
        if height is not None:
            if h is not None:
                raise TypeError("pass either h= or height=, not both")
            h = height
        # ``h="auto"`` (Custom-based panels: markdown, custom, table, image…)
        # fits the panel height to its rendered content: flag the component (its
        # iframe then reports content height; the frontend resizes to fit) and
        # fall back to the default height until the first measurement lands.
        # Auto-height fits the panel to its rendered content (the frontend
        # measures and resizes; comp.h syncs back). Two ways to get it: the caller
        # asks with h="auto", or the component defaults to it (e.g. Label, whose
        # content is always short). They differ around layout containers — an
        # explicit h="auto" overrides a grid slot / row height, a *default* one
        # yields to it (so grids stay uniform). An explicit numeric h pins either.
        if h == "auto":
            user_auto = True
            if hasattr(component, "_auto_h"):
                component._auto_h = True
            else:
                warnings.warn(
                    "h='auto' is only supported on Custom-based panels "
                    "(custom, markdown, table, image, …); using the default "
                    "height", stacklevel=2,
                )
        else:
            user_auto = False
            if h is not None and getattr(component, "_auto_h", False):
                component._auto_h = False  # explicit numeric h pins the panel
        # Width auto-fit, mirroring the height handling above: an explicit
        # w="auto" opts in (Custom panels measure their content's natural width),
        # an explicit numeric w pins it, and a Custom panel flagged _auto_w by
        # show()/dispatch fits by default until a grid/column slot imposes a
        # width (handled in the placement block, like default auto-height).
        if w == "auto":
            if hasattr(component, "_auto_w"):
                component._auto_w = True
            else:
                warnings.warn(
                    "w='auto' is only supported on Custom-based panels; "
                    "using the default width", stacklevel=2,
                )
            w = None
        elif w is not None and getattr(component, "_auto_w", False):
            component._auto_w = False  # explicit numeric w pins the panel
        # A component fitting its own content with no height imposed by the caller.
        # It still accepts a slot/row height in _place below; only then is its
        # auto-height switched off (see the placement block).
        default_auto = h is None and getattr(component, "_auto_h", False)
        default_auto_w = w is None and getattr(component, "_auto_w", False)
        # Only an explicit h="auto" makes the layout skip its slot height.
        auto_h = user_auto
        if user_auto:
            h = None
        # Relative placement: derive x/y from an anchor panel's live geometry.
        # Resolved before the swap-in-place logic below, so an explicit relative
        # placement wins over an evicted panel's old position.
        if below is not None or above is not None or right_of is not None \
                or left_of is not None:
            new_w = w if w is not None else component.w
            new_h = h if h is not None else component.h
            rx, ry = self._relative_position(
                below, above, right_of, left_of, gap, new_w, new_h)
            if x is None:
                x = rx
            if y is None:
                y = ry
        # Auto-layout: inside a `with canvas.grid(...)`/`column`/`row` block, a
        # panel given neither an explicit position nor a relative anchor takes the
        # next slot (and the layout's default slot size, unless w/h were given).
        scoped_layout = None
        if self._layout_stack and x is None and y is None:
            flow = self._layout_stack[-1]
            fx, fy, fw, fh = flow._place(component, w, h, auto_h)
            # A grid slot / row common-height just imposed a concrete height on a
            # panel that was only *default* auto-height (e.g. a Label): honor that
            # height instead of fitting, so the grid stays uniform.
            if default_auto and fh is not None:
                component._auto_h = False
            # Likewise a slot width pins a default auto-width panel, keeping grid
            # columns uniform (autopanel passes an explicit slot_w, so its tidy
            # columns are preserved; show() outside a layout keeps fitting).
            if default_auto_w and fw is not None:
                component._auto_w = False
            if flow._roles is None and flow._client_id is None:
                x, y, w, h = fx, fy, fw, fh   # shared base (the usual case)
            else:
                # Scoped container: this placement is that audience's overlay, not
                # the shared base — applied via set_layout once the panel is bound.
                scoped_layout = (fx, fy, fw, fh, flow._roles, flow._client_id)
        if name is None:
            name = component.name
        if name is None:
            label = component._props.get("label")
            name = label if isinstance(label, str) and label else self._auto_name(
                component.component)
        # A component name doubles as the ``canvas.<name>`` attribute handle, so a
        # name that shadows a real Canvas method/property (``save``, ``slider``,
        # ``components``…) would be silently unreachable that way (``__getattr__``
        # only fires when normal lookup fails). Warn, since the handle still works
        # through ``canvas["<name>"]``. ``hasattr`` on the class reads the
        # descriptor without invoking any property getter.
        if isinstance(name, str) and hasattr(type(self), name):
            warnings.warn(
                f"component name {name!r} shadows a Canvas attribute; reach it "
                f"with canvas[{name!r}] (canvas.{name} stays the method/property)",
                stacklevel=2,
            )
        # Names are unique handles. If something else already holds this name (a
        # prior component, or this component in an earlier state), pull it off the
        # canvas first so the stale panel disappears from the UI instead of
        # lingering unreferenced. The newcomer then takes over the name and is the
        # only panel rendered for it. Re-inserting under the same name is the
        # intended swap-in-place (e.g. re-running a cell), so it's silent; only a
        # name reused across *different* kinds of object is worth a warning.
        old = self._named.get(name)
        if old is not None and old is not component:
            if type(old) is not type(component):
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
        # Apply each lock/chrome flag only when it differs from the default, so
        # the stored attribute matches what set_layout/register would send. The
        # names and backing attributes come from the shared LAYOUT_FLAGS table.
        flag_args = {
            "locked": locked, "draggable": draggable, "resizable": resizable,
            "operable": operable, "grabbable": grabbable, "frame": frame,
        }
        for fname, value in flag_args.items():
            flag = LAYOUT_FLAGS[fname]
            if bool(value) != flag.default:
                setattr(component, flag.attr, bool(value))
        if queue is not None:
            component.queue = queue  # property setter validates the policy
        if roles is not None:
            component._roles = list(roles)
        if lock_for is not None:
            component._lock_for = list(lock_for)
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
        # A role/client-scoped container stores its computed slot as that
        # audience's layout overlay (replayed to them on connect), leaving the
        # shared base unset. Done before register_live so a live insert replays
        # the overlay merged into the register frame.
        if scoped_layout is not None:
            fx, fy, fw, fh, sroles, scid = scoped_layout
            component.set_layout(x=fx, y=fy, w=fw, h=fh,
                                 roles=sroles, client_id=scid)
        if self._serving:
            self._bridge.register_live(component)
        return component

    def _relative_position(self, below, above, right_of, left_of, gap,
                           new_w, new_h):
        """Compute an (x, y) from the relative-placement anchors given to insert.

        Each anchor is a component or its name. ``below``/``above`` align the new
        panel's left edge with the anchor's and stack it ``gap`` pixels under/over
        it; ``right_of``/``left_of`` align top edges and set it beside. One anchor
        sets both coordinates; two (e.g. ``below=a, right_of=b``) each set their
        own axis. ``above``/``left_of`` need the new panel's size (``new_w``/
        ``new_h``) since they offset by it.
        """
        def resolve(ref, kind):
            if ref is None:
                return None
            comp = self._named.get(ref) if isinstance(ref, str) else ref
            if comp is None or not hasattr(comp, "w"):
                raise ValueError(f"{kind}={ref!r} is not a component on this canvas")
            if comp.x is None or comp.y is None:
                raise ValueError(
                    f"can't place {kind} {comp.name!r}: it has no position yet "
                    "(give it x/y, place it relatively, or wait for the browser "
                    "to report where auto-cascade put it)"
                )
            return comp
        below, above = resolve(below, "below"), resolve(above, "above")
        right_of, left_of = resolve(right_of, "right_of"), resolve(left_of, "left_of")
        x = y = None
        # Vertical anchors own y (and suggest x); horizontal anchors own x (and
        # suggest y). Explicit horizontal beats a vertical anchor's suggestion.
        if below is not None:
            x, y = below.x, below.y + below.h + gap
        elif above is not None:
            x, y = above.x, above.y - gap - new_h
        if right_of is not None:
            x = right_of.x + right_of.w + gap
            if y is None:
                y = right_of.y
        elif left_of is not None:
            x = left_of.x - gap - new_w
            if y is None:
                y = left_of.y
        return x, y

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

    def slider(self, name, min=0, max=100, default=None, step=1,
               on_release=False, label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Slider`. See :meth:`insert` for ``place``.

        ``step`` sets the granularity and the int-vs-float behaviour (a
        fractional step like ``0.1`` makes it a float slider). ``on_release=True``
        reports only the settled value when the user lets go, instead of every
        value during the drag.
        """
        return self._make(Slider, name, min=min, max=max, default=default,
                          step=step, on_release=on_release, label=label, **place)

    def toggle(self, name, options, default=None, label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Toggle`. See :meth:`insert` for ``place``."""
        return self._make(Toggle, name, options, default=default, label=label,
                          **place)

    def button(self, name, text=None, label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Button`. See :meth:`insert` for ``place``."""
        return self._make(Button, name, text=text, label=label, **place)

    def label(self, name, value="", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Label`. See :meth:`insert` for ``place``."""
        return self._make(Label, name, value=value, label=label, **place)

    def video(self, name, quality=70, label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.VideoFeed`. See :meth:`insert` for ``place``."""
        return self._make(VideoFeed, name, quality=quality, label=label, **place)

    def audio(self, name, sample_rate=16000, channels=1, label=None, **place: Unpack[Place]):
        """Insert an :class:`~pycanvas.AudioFeed`. See :meth:`insert` for ``place``."""
        return self._make(AudioFeed, name, sample_rate=sample_rate,
                          channels=channels, label=label, **place)

    def chat(self, name="chat", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Chat` panel. See :meth:`insert` for ``place``."""
        return self._make(Chat, name=name, label=label, **place)

    def custom(self, html=None, path=None, css=None, js=None, name="custom",
               label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Custom`. See :meth:`insert` for ``place``.

        ``html``/``css``/``js`` may be given as separate strings (e.g. pasted
        from uiverse.io) — they are composed into one document under the hood.
        Size the panel with ``w``/``h`` in ``place``.
        """
        return self._make(Custom, html=html, path=path, css=css, js=js,
                          name=name, label=label, **place)

    def download(self, name, source=None, filename=None, text=None, label=None,
                 **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Download` button. See :meth:`insert` for ``place``.

        Clicking it downloads ``source`` — a file path or ``bytes`` — to the
        viewer's machine. For content generated fresh on each click, omit
        ``source`` and register a provider with ``@download.provide``.
        ``filename`` sets the saved name (otherwise a path's basename, or the
        panel name, is used). The host code chooses what each click serves, so
        nothing the viewer sends selects a path.
        """
        return self._make(Download, name, source=source, filename=filename,
                          text=text, label=label, **place)

    def upload(self, name="upload", text=None, label=None, dest=None,
               accept=None, multiple=False, max_size=None, **place: Unpack[Place]):
        """Insert an :class:`~pycanvas.Upload` panel. See :meth:`insert` for ``place``.

        A click-or-drop zone that receives a viewer's file into Python; wire it
        with ``@upload.on_upload``. By default the bytes arrive in memory
        (``file.data``); pass ``dest=`` a directory to stream each upload to disk
        instead (``file.path``), which keeps memory flat for large files.
        ``accept`` filters the picker (e.g. ``".csv"``), ``multiple=True`` allows
        several at once, and ``max_size`` (bytes) rejects oversized uploads — set
        it on any public/tunneled canvas.
        """
        return self._make(Upload, name, text=text, label=label, dest=dest,
                          accept=accept, multiple=multiple, max_size=max_size,
                          **place)

    def file_browser(self, name="files", root=".", label=None, pattern=None,
                     show_hidden=False, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.FileBrowser`. See :meth:`insert` for ``place``.

        Navigation is confined to ``root``. ``pattern`` (an fnmatch glob like
        ``"*.csv"``) filters which files are shown. Size it with ``w``/``h`` in
        ``place``.
        """
        return self._make(FileBrowser, name=name, root=root, label=label,
                          pattern=pattern, show_hidden=show_hidden, **place)

    def react(self, source=None, path=None, jsx=None, css=None, name="react",
              label=None, props=None, scope=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.React` panel — the workhorse for custom UI.

        ``source`` is JSX defining ``function Component(...)`` (or load it from a
        file with ``path=``); alternatively pass just ``jsx`` markup plus optional
        ``css`` and the Component wrapper is added under the hood. ``css`` also
        works with ``source=`` (it rides as a ``<style>`` the host renders), so a
        full component can keep its styles in a separate string. Use
        :meth:`React.from_uiverse` to convert a uiverse.io styled-components
        snippet. ``props`` is the initial props dict; ``scope`` is third-party
        library names (e.g. ``["d3"]``) loaded as ESM and exposed as ``libs``.

        Placement, visibility (``roles`` / ``lock_for``), the lock/chrome flags,
        and ``queue`` all flow through ``**place`` — see :meth:`insert` (and the
        :class:`Place` keys your editor now autocompletes).
        """
        return self._make(React, source=source, path=path, jsx=jsx, css=css,
                          name=name, label=label, props=props, scope=scope,
                          **place)

    def markdown(self, text="", name="markdown", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Markdown` panel. See :meth:`insert` for ``place``."""
        return self._make(Markdown, text=text, name=name, label=label, **place)

    def image(self, src, name="image", label=None, fit="contain", **place: Unpack[Place]):
        """Insert an :class:`~pycanvas.Image` panel. See :meth:`insert` for ``place``.

        ``src`` is a path, URL, image bytes, Matplotlib/PIL figure, or array.
        """
        return self._make(Image, src, name=name, label=label, fit=fit, **place)

    def table(self, data, name="table", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Table` panel. See :meth:`insert` for ``place``.

        ``data`` is a pandas DataFrame/Series, a list of dicts/rows, or a dict.
        """
        return self._make(Table, data, name=name, label=label, **place)

    def show(self, value, name=None, label=None, **place: Unpack[Place]):
        """Auto-render any value as the best-fitting panel and insert it.

        Picks the component the way a notebook decides how to render an output
        (DataFrame → table, figure/array → image, Plotly → plot, rich
        ``_repr_*`` → its HTML, dict/list → JSON, string → label/markdown, else a
        repr label) via :func:`pycanvas.panel_for`. With no ``name`` a unique one
        is generated; re-using a ``name`` replaces that panel in place. Returns
        the inserted component. See :meth:`insert` for ``place``.
        """
        from .dispatch import panel_for
        if name is None:
            self._show_seq += 1
            name = f"panel_{self._show_seq}"
        comp = panel_for(value, name=name, label=label)
        # insert() handles eviction of whatever currently holds this name, so
        # re-showing under the same name replaces in place on its own.
        return self.insert(comp, **place)

    def webview(self, url, name="web", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.WebView`. See :meth:`insert` for ``place``."""
        return self._make(WebView, url, name=name, label=label, **place)

    def plot(self, name="plot", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Plot`. See :meth:`insert` for ``place``."""
        return self._make(Plot, name=name, label=label, **place)

    def live_plot(self, name="live plot", **kw):
        """Insert a :class:`~pycanvas.LivePlot`.

        Constructor kwargs (``traces``, ``max_points``, ``mode``, ``layout``,
        ``smoothing``, ``w``, ``h``, ``label``) and :meth:`insert` placement
        options both go in ``kw``; they don't overlap. ``traces`` only fixes the
        legend order — pushing an unseen key adds a trace on the fly.
        """
        return self._make(LivePlot, name=name, **kw)

    def histogram(self, name="histogram", **kw):
        """Insert a :class:`~pycanvas.Histogram` — a distribution-over-time panel.

        Constructor kwargs (``bins``, ``mode``, ``value_range``, ``max_steps``,
        ``label``, ``w``, ``h``) and :meth:`insert` placement options both go in
        ``kw``. Feed it with ``panel.add(values, step)``; needs ``plotly``.
        """
        return self._make(Histogram, name=name, **kw)

    def grid(self, cols=2, slot=(520, 360), gap=24, origin=(40, 40),
             roles=None, client_id=None):
        """Auto-arrange panels added inside a ``with`` block into a grid.

        Inside the block, any panel inserted without an explicit ``x``/``y`` (or a
        ``below=``/``right_of=`` anchor) drops into the next cell — left to right,
        top to bottom, ``cols`` per row — taking the slot size unless you pass
        ``w``/``h``::

            with canvas.grid(cols=2, slot=(560, 300)):
                canvas.live_plot("loss")
                canvas.live_plot("accuracy")
                canvas.image(fig)            # next row

        ``slot`` is each cell's ``(width, height)``, ``gap`` the spacing between
        cells, ``origin`` the grid's top-left canvas coordinate. An explicit
        position or relative anchor still wins for that panel. Nest or sequence
        blocks freely to build columns of charts beside columns of media. For a
        strip of mixed-height controls, prefer :meth:`column` / :meth:`row`,
        which keep each panel's natural size instead of a uniform cell.

        Pass ``roles=`` and/or ``client_id=`` to lay the block out for just those
        viewers — each panel's slot is written as that audience's *overlay* (via
        :meth:`~pycanvas.React.set_layout`) instead of the shared base, so one
        role can have its own arrangement. Best for a role's *exclusive* panels;
        a panel shared across roles is created once (in one block), so give the
        other roles their layout with a separate scoped block over fresh panels
        or `set_layout(roles=…)` directly.
        """
        return _FlowLayout(self, "grid", cols=cols, slot=slot, gap=gap,
                           origin=origin, roles=roles, client_id=client_id)

    def column(self, width=None, gap=16, origin=(40, 40), w=None,
               roles=None, client_id=None):
        """Auto-stack panels added inside a ``with`` block into one column.

        Each panel keeps its **natural height** (a slider stays slider-tall, a
        button button-tall), so a mixed control strip isn't squashed to one
        height. ``width`` sets a common width (``None`` keeps each panel's own);
        ``gap`` is the vertical spacing, ``origin`` the top-left corner. An
        explicit position or relative anchor still wins for that panel. ``w`` is
        accepted as an alias for ``width``. ``roles=`` / ``client_id=`` scope the
        arrangement to those viewers (see :meth:`grid`).
        """
        if w is not None:
            if width is not None:
                raise TypeError("pass either width= or w=, not both")
            width = w
        return _FlowLayout(self, "column", slot=(width, None), gap=gap,
                           origin=origin, roles=roles, client_id=client_id)

    def row(self, height=None, gap=16, origin=(40, 40), h=None,
            roles=None, client_id=None):
        """Auto-arrange panels added inside a ``with`` block into one row.

        The horizontal counterpart of :meth:`column`: panels flow left to right,
        each keeping its **natural width**. ``height`` sets a common height
        (``None`` keeps each panel's own); ``gap`` is the horizontal spacing.
        ``h`` is accepted as an alias for ``height``. ``roles=`` / ``client_id=``
        scope the arrangement to those viewers (see :meth:`grid`).
        """
        if h is not None:
            if height is not None:
                raise TypeError("pass either height= or h=, not both")
            height = h
        return _FlowLayout(self, "row", slot=(None, height), gap=gap,
                           origin=origin, roles=roles, client_id=client_id)

    def repl(self, name="repl", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Repl`. See :meth:`insert` for ``place``.

        Call :meth:`enable_repl` first to bind the namespace cells run against.
        """
        return self._make(Repl, name=name, label=label, **place)

    def inspector(self, name="inspector", refresh=None, source="components",
                  namespace=None, label=None, **place: Unpack[Place]):
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

    def clear(self):
        """Remove all panels and arrows from the canvas. Works live while serving."""
        for c in list(self._components):
            self.remove(c)
        for a in list(self._arrows):
            self.disconnect(a)
        return self

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
            # Re-connecting the same endpoints (an Arrow replacing an Arrow) is
            # the intended in-place swap, so stay quiet; only warn when the name
            # was held by a different kind of object (a panel).
            if not isinstance(old, Arrow):
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
    def save(self, path, timeout=5.0, blocking=True):
        """Save the canvas to one JSON file: panel formation + user drawings.

        Two things are written together:

        - ``layout`` — every panel's geometry and lock state (read from Python,
          which tracks the user's live drags/resizes). Panels are code, so only
          their *placement* is saved, never their behaviour.
        - ``drawings`` — the free-form shapes/text/arrows the user added in the
          UI, which have no Python counterpart. Captured from a connected
          browser (the source of truth), so an open page is needed for these;
          with no browser open the formation is still saved on its own.

        With ``blocking=False`` the snapshot request is fired on a background
        thread and a :class:`~concurrent.futures.Future` is returned immediately.
        Call ``.result()`` on it to wait and confirm the file was written::

            fut = canvas.save("board.json", blocking=False)
            # ... do other work in the next cell ...
            fut.result()    # raises on timeout or I/O error

        Reload with :meth:`load`.
        """
        if not blocking:
            import concurrent.futures
            fut: concurrent.futures.Future = concurrent.futures.Future()
            threading.Thread(
                target=self._save_bg, args=(path, timeout, fut), daemon=True
            ).start()
            return fut
        return self._save_sync(path, timeout)

    def _save_sync(self, path, timeout):
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

    def _save_bg(self, path, timeout, fut):
        try:
            self._save_sync(path, timeout)
            fut.set_result(self)
        except Exception as exc:
            fut.set_exception(exc)

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
                # Every lock/chrome flag, straight from the shared table.
                **{name: getattr(c, name) for name in LAYOUT_FLAGS},
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
                warnings.warn(
                    f"load: panel {item.get('name')!r} not found on canvas — "
                    "recreate it before calling load()",
                    stacklevel=3,
                )
                continue
            comp.set_layout(
                x=item.get("x"),
                y=item.get("y"),
                w=item.get("w"),
                h=item.get("h"),
                rotation=item.get("rotation"),
                # Flags absent from an older save stay None (left unchanged).
                **{name: item.get(name) for name in LAYOUT_FLAGS},
            )

    @staticmethod
    def _read_json(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def wait_for_client(self, timeout=10.0):
        """Block until at least one browser is connected, or ``timeout`` elapses.

        Useful before :meth:`load`, which pushes to connected clients — give the
        freshly opened page a moment to connect first. Returns ``True`` if a
        client connected.
        """
        self._bridge._any_connected.wait(timeout=timeout)
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

    # Keys accepted in a ``view`` config, each paired with the coercion applied
    # before it is sent to the browser. Unknown keys are rejected so a typo
    # (e.g. ``zooom``) surfaces immediately rather than being silently ignored.
    _VIEW_KEYS = {
        "x": float, "y": float, "zoom": float,
        "min_zoom": float, "max_zoom": float,
        "locked": bool, "ui": bool, "grid": bool, "read_only": bool,
    }

    @classmethod
    def _normalize_view(cls, view):
        """Validate/coerce a ``serve(view=...)`` dict into the wire form.

        Returns ``None`` for ``None`` (leave every tldraw default in place) and
        raises on an unknown key or a non-numeric/zoom value, so configuration
        mistakes fail loudly at ``serve`` time instead of silently doing nothing.
        """
        if view is None:
            return None
        if not isinstance(view, dict):
            raise TypeError("view must be a dict of options, e.g. "
                            "view={'zoom': 1.5, 'ui': False}")
        out = {}
        for key, value in view.items():
            coerce = cls._VIEW_KEYS.get(key)
            if coerce is None:
                raise ValueError(
                    f"unknown view option {key!r}; valid options are "
                    f"{', '.join(sorted(cls._VIEW_KEYS))}"
                )
            out[key] = coerce(value)
        if "min_zoom" in out and "max_zoom" in out \
                and out["min_zoom"] > out["max_zoom"]:
            raise ValueError("view min_zoom must not exceed max_zoom")
        return out

    def serve(self, port=8000, open_browser=True, host="127.0.0.1",
              allow_remote_exec=False, block=True, wait=True,
              tunnel=False, tunnel_provider="cloudflared", ui_inspector=None,
              cursors=None, view=None, desktop=None, window_title="PyCanvas",
              window_size=(1200, 800), password=None, passwords=None,
              hot_reload=False, debug=False):
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

        ``hot_reload=True`` watches the ``.py`` files alongside the running
        script and restarts the whole process whenever one changes, so edits —
        a different ``default=``, a moved panel, ``ui=False`` — take effect on
        save. The browser tab reconnects to the restarted server on its own (no
        new tab opens). Only available with ``block=True`` (a script entry
        point); ``block=False`` with ``hot_reload=True`` raises an error, and
        calling it outside a ``python yourscript.py`` run (e.g. a notebook)
        raises too. With ``tunnel=True`` the tunnel is opened once by the
        long-lived watcher process (not the restarting worker), so the public URL
        stays the same across reloads and the provider isn't re-created on every
        save — visitors just see a momentary blip during each restart.

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

        ``cursors`` enables viewer pointer reporting: each browser streams its
        cursor position (throttled, in canvas coords) so Python can read it as
        ``canvas.viewers[i]["cursor"]``. It's viewer telemetry — the host sees
        every viewer's pointer — so it's gated like ``ui_inspector``: default on
        only for a private local bind, ``cursors=True``/``False`` to override.

        ``password`` gates access to the whole canvas: when set, a visitor is
        shown a small password page first and the WebSocket is refused until they
        pass it, so a shared LAN or tunneled URL isn't open to anyone who finds
        it. The check is per-browser-session (a cookie), so each viewer enters it
        once. It is independent of ``allow_remote_exec`` — a password controls who
        may connect, not whether a Repl may run, so a public Repl still needs the
        explicit opt-in even behind a password.

        ``view`` configures how the tldraw canvas is presented and navigated, so
        the same canvas can be a free creative workspace or a fixed UI. Pass a
        dict with any of these keys (all optional):

        * ``x`` / ``y`` / ``zoom`` — initial camera: centre the view on canvas
          point ``(x, y)`` at ``zoom`` (1.0 = 100%). Any subset works; this is
          applied once on first load so a viewer who pans away isn't snapped back.
        * ``locked`` — ``True`` freezes pan and zoom entirely (a fixed kiosk view).
        * ``min_zoom`` / ``max_zoom`` — clamp how far the viewer can zoom.
        * ``ui`` — ``False`` hides tldraw's toolbars/menus for a chrome-free
          surface (defaults to shown).
        * ``grid`` — ``True`` shows the background grid.
        * ``read_only`` — ``True`` puts tldraw in read-only mode (no drawing).

        ``desktop`` selects a native app window (via pywebview) instead of the
        system browser. It defaults to ``None`` = auto: on inside a baked
        executable (``sys.frozen``), off otherwise — so the same script opens a
        browser in development and a contained window when run as the packaged
        ``.exe``. Force it either way with ``desktop=True``/``False``.
        ``window_title``/``window_size`` set that window's caption and pixel
        size. Desktop mode runs on the main thread and blocks until the window is
        closed (``block`` doesn't apply); if pywebview isn't installed it warns
        and falls back to the browser. See :meth:`bake` to build the executable.

        ``debug=True`` logs every WebSocket frame to the console — what Python
        sends (``->``) and what each browser sends back (``<-``) — so "the panel
        isn't updating" turns into evidence: either the frame is on the wire or
        it isn't. (Programmatic equivalent: :meth:`on_frame`.) Connection lines
        ("viewer connected / disconnected") are always printed, debug or not.
        """
        if os.environ.get("_PYCANVAS_RELOAD_CHECK") == "1":
            # Hot-reload pre-flight (see hotreload.run_monitor): the monitor
            # runs the edited script in this mode to confirm it imports and runs
            # before tearing down the live worker. Reaching serve() means the
            # module body executed without error -- which is all the check needs
            # -- so exit cleanly *without* binding a port or starting threads, so
            # the check never collides with the worker that's still serving.
            sys.exit(0)
        if hot_reload:
            if not block:
                raise ValueError(
                    "hot_reload=True requires block=True (not block=False). "
                    "Hot reloading restarts the entire process, which is "
                    "incompatible with background mode."
                )
            main_file = getattr(sys.modules.get("__main__"), "__file__", None)
            if main_file is None:
                raise RuntimeError(
                    "hot_reload=True requires running as a script "
                    "(`python yourscript.py`), not from an interactive "
                    "session."
                )
            if os.environ.get("_PYCANVAS_RELOAD_WORKER") != "1":
                from .hotreload import run_monitor
                # The monitor outlives every worker restart, so it — not the
                # short-lived worker — owns the tunnel: one tunnel is opened here
                # at the fixed port and stays put across reloads (no per-edit
                # cloudflared churn, and a stable public URL). Workers serve the
                # port behind it; see own_tunnel below.
                run_monitor(main_file, tunnel=tunnel, port=port,
                            tunnel_provider=tunnel_provider)
                return self
            if os.environ.get("_PYCANVAS_RELOAD_RESTART") == "1":
                # Already opened on first launch; a reload should reuse the
                # existing tab (the frontend reconnects its websocket
                # automatically) instead of popping another one.
                open_browser = False
                # Tell the reconnecting browser this is a fresh run so it drops
                # the previous run's panels (their ids change each run) before we
                # replay this run's — otherwise stale panels linger beside the new.
                self._bridge._reload = True
        # Under hot reload the persistent monitor process owns the tunnel (above),
        # so this worker must not open its own — but it's still publicly reachable
        # *through* that tunnel, so every public-exposure decision below stays keyed
        # on `tunnel`, not `own_tunnel`. Outside hot reload they're identical.
        own_tunnel = tunnel and os.environ.get("_PYCANVAS_RELOAD_WORKER") != "1"
        # A tunnel publishes the loopback bind to the entire internet, so the
        # "127.0.0.1 is private" assumption behind the Repl gate breaks. Gate it
        # as if binding publicly.
        self._check_remote_exec("0.0.0.0" if tunnel else host, allow_remote_exec)
        # Remember the bind's reach so a Repl inserted live (serve(block=False))
        # gets the same gate; a password doesn't lift it (auth'd users still get
        # RCE), so allow_remote_exec stays the explicit opt-in.
        self._public_bind = tunnel or host not in ("127.0.0.1", "localhost")
        self._allow_remote_exec = allow_remote_exec
        # The UI Inspector exposes state to every viewer; default it on only for
        # a private, non-tunneled bind. An explicit ui_inspector overrides that.
        local = host in ("127.0.0.1", "localhost")
        self._bridge._ui_inspector = (
            bool(ui_inspector) if ui_inspector is not None
            else (local and not tunnel)
        )
        # Cursor reporting is viewer telemetry (the host can read every viewer's
        # pointer via canvas.viewers), so gate it like the Inspector: default on
        # only for a private, non-tunneled bind; an explicit cursors= overrides.
        self._bridge._cursors = (
            bool(cursors) if cursors is not None
            else (local and not tunnel)
        )
        # Wire logging: a frame tap that prints every JSON frame (and binary
        # summaries) with the component's friendly name. ASCII arrows on purpose
        # — Windows consoles often run cp1252, which can't print "▼"/"▲".
        if debug:
            self._bridge.add_frame_tap(self._debug_frame)
        # Merge serve's view onto any config already set via set_view() rather
        # than clobbering it, so `set_view(ui=False); serve()` (or bake(), which
        # calls serve with no view) keeps the earlier settings. An explicit
        # serve(view=...) still wins key-by-key.
        serve_view = self._normalize_view(view)
        if serve_view is not None:
            self._bridge._view = {**(self._bridge._view or {}), **serve_view}
        # Start any registered background workers now -- we're in the serving
        # process (the hot-reload monitor returned above), so producer loops that
        # grab single-owner resources (a camera, a serial port) run here, never
        # in the monitor.
        self._start_background()
        # Native-window mode: default to on only inside a baked executable, so a
        # plain `python script.py` still opens the browser. Blocks on the webview
        # loop (main thread), so the non-blocking branch below is skipped.
        use_desktop = bool(getattr(sys, "frozen", False)) if desktop is None \
            else bool(desktop)
        if use_desktop:
            self._serve_desktop(port, host, own_tunnel, tunnel_provider,
                                window_title, window_size, password,
                                passwords=passwords)
            return self
        if not block:
            self._server = server.run_background(
                self._bridge, port=port, open_browser=open_browser, host=host,
                password=password, passwords=passwords,
            )
            if wait:
                self._wait_until_ready()
            self._serving = True
            if own_tunnel:
                self._start_tunnel(port, tunnel_provider)
            return self
        self._serving = True
        if own_tunnel:
            self._start_tunnel(port, tunnel_provider)
        try:
            server.run(self._bridge, port=port, open_browser=open_browser,
                       host=host, password=password, passwords=passwords)
        finally:
            self._stop_tunnel()

    def _serve_desktop(self, port, host, tunnel, tunnel_provider, title, size,
                       password=None, passwords=None):
        """Serve in the background and show the canvas in a native window.

        Used by desktop mode (a baked executable, or ``serve(desktop=True)``).
        Falls back to a normal blocking browser serve if pywebview is missing, so
        a build without the desktop extra still runs — just in the browser.
        """
        try:
            import webview
        except ImportError:
            warnings.warn(
                "pywebview is not installed; opening in the browser instead. "
                "Install the desktop extra: pip install 'pycanvas[desktop]'"
            )
            self._serving = True
            if tunnel:
                self._start_tunnel(port, tunnel_provider)
            try:
                server.run(self._bridge, port=port, open_browser=True, host=host,
                           password=password, passwords=passwords)
            finally:
                self._stop_tunnel()
            return
        # Start the server in the background, then drive the window on the main
        # thread (pywebview requires that). webview.start() blocks until the
        # window closes; tear the server down afterwards.
        self._server = server.run_background(
            self._bridge, port=port, open_browser=False, host=host,
            password=password, passwords=passwords,
        )
        self._wait_until_ready()
        self._serving = True
        if tunnel:
            self._start_tunnel(port, tunnel_provider)
        try:
            width, height = size
            webview.create_window(title, f"http://127.0.0.1:{port}",
                                  width=int(width), height=int(height))
            webview.start()
        finally:
            self.stop()

    def bake(self, name="PyCanvas", *, icon=None, onefile=True, windowed=True,
             distpath="dist", entry=None, exclude=None, include=None,
             window_size=(1200, 800), port=8000):
        """Package this canvas's script into a standalone desktop app.

        Run normally (``python your_script.py``), ``bake`` builds a single
        self-contained executable from that script with PyInstaller — bundling
        Python, the pycanvas backend, and the pre-built frontend — and returns
        the path to it. The built app needs nothing installed: launching it runs
        your script and shows the canvas in a native window (pywebview), serving
        on ``127.0.0.1`` exactly as in development.

        The same script is both source and app: inside the built executable
        ``sys.frozen`` is set, so ``bake`` skips the build and simply runs the
        canvas in a window (``name``/``window_size``/``port`` configure it).
        Place it where you'd call :meth:`serve`::

            canvas.bake(name="RobotConsole")   # python -> builds; .exe -> runs

        ``name`` is the executable/window title; ``icon`` is an optional ``.ico``/
        ``.icns``; ``onefile`` packs everything into one file (``False`` makes a
        folder, which launches faster); ``windowed`` hides the console window;
        ``distpath`` is the output directory; ``entry`` overrides the script to
        package (defaults to the running one). Only the packages your script
        imports are bundled (not the whole environment); ``include`` force-adds
        ones the analysis can't see (dynamic/plugin imports), and ``exclude``
        skips modules — use it when a broken or unused optional dependency would
        otherwise crash the build (e.g. ``exclude=["torch"]``).

        Heavy optional dependencies are bundled only when this canvas uses the
        component that needs them — numpy for an AudioFeed, OpenCV for a
        VideoFeed — so a slider-only app doesn't drag them in. When numpy is
        bundled on a conda environment, the MKL DLLs it needs are detected and
        bundled automatically too (a pip/venv NumPy needs nothing). The public
        tunnel (``pycloudflared``) and IPython are excluded by default — a
        standalone local app needs neither, and either one pulls in a large
        unrelated dependency tree; pass them in ``include`` if you really need
        them. Building requires the
        desktop extra: ``pip install 'pycanvas[desktop]'``. To build without
        editing your script, the equivalent CLI is
        ``python -m pycanvas.bake your_script.py``.
        """
        if getattr(sys, "frozen", False):
            # Inside the built executable: don't rebuild — run the app in a
            # native window. Same code path the .exe takes on every launch.
            return self.serve(port=port, desktop=True, window_title=name,
                              window_size=window_size)
        from . import bake as _bake
        if entry is None:
            import __main__
            entry = getattr(__main__, "__file__", None)
            if not entry:
                raise RuntimeError(
                    "could not detect the script to package (no __main__ file); "
                    "pass entry='your_script.py' or use "
                    "`python -m pycanvas.bake your_script.py`"
                )
        # The script has already run by the time bake() is called, so sys.modules
        # reflects exactly what it imported. If it pulled in a heavy optional dep
        # bake otherwise treats as component-only (numpy/Pillow/OpenCV), bundle it
        # — and keep bake's default media-dep exclusions from dropping it. numpy in
        # particular needs its conda MKL DLLs, which only ride along when it's
        # collected (see build_app), so this is what makes a script that imports
        # numpy directly produce a working exe.
        include = list(include or [])
        for pkg in ("numpy", "PIL", "cv2"):
            if pkg in sys.modules and pkg not in include:
                include.append(pkg)

        out = _bake.build_app(
            entry, name=name, icon=icon, onefile=onefile,
            windowed=windowed, distpath=distpath, exclude=exclude,
            include=include,
            # Tell the build which heavy optional deps to bundle, based on the
            # components this canvas actually uses (numpy for AudioFeed, OpenCV
            # for VideoFeed, Pillow for Image) — a slider-only app stays lean.
            # Keyed by class name (several components share the "Custom" type).
            components={type(c).__name__ for c in self._components},
        )
        print(f"PyCanvas baked: {out}")
        return out

    def set_view(self, view=None, client_id=None, roles=None, **opts):
        """Change viewport/navigation properties live on connected browsers.

        Accepts the same options as ``serve(view=...)`` (initial camera, zoom
        limits, ``locked``, ``ui``, ``grid``, ``read_only``), given as a dict
        and/or keyword args, and pushes them to connected canvases::

            canvas.set_view(ui=False)                      # hide toolbars everywhere
            canvas.set_view({"zoom": 2.0})                 # zoom all viewers to 200%
            canvas.set_view(locked=True)                   # freeze pan/zoom everywhere
            canvas.set_view(x=100, y=200, client_id="...")  # move view for one user
            canvas.set_view(read_only=True, ui=False, roles=["user"])  # by login role

        The change is scoped by which of ``client_id``/``roles`` you pass:

        * neither — broadcasts to all viewers and becomes the global default that
          later viewers inherit on connect.
        * ``roles`` (a role name or list of names, matching ``serve(passwords=)``)
          — applies to viewers logged in under those roles, now and on every
          future connect, so e.g. admins keep the toolbar and drawing while
          ``"user"`` viewers get a chrome-free, read-only canvas.
        * ``client_id`` (a viewer's unique id from the roster) — affects only
          that one viewer.

        Precedence on connect is global < per-role < per-client, so a more
        specific scope wins. Only the keys you pass change; the rest keep their
        current value. Passing ``x``/``y``/``zoom`` re-centres the camera
        immediately (subject to any lock); omitting them leaves viewers where
        they were looking. Returns ``self``.
        """
        merged_in = dict(view or {})
        merged_in.update(opts)
        delta = self._normalize_view(merged_in) or {}
        if not delta:
            return self

        if client_id is not None:
            # Most specific: one viewer's per-client view state.
            self._bridge._view_per_client[client_id] = {
                **(self._bridge._view_per_client.get(client_id) or {}),
                **delta
            }
            self._bridge.send_to_client(client_id, {"type": "view", "view": delta})
        elif roles is not None:
            # Per-role: update each role's view state and push to anyone already
            # connected under that role. Future connects pick it up via _view_for.
            role_list = [roles] if isinstance(roles, str) else list(roles)
            for role in role_list:
                self._bridge._view_per_role[role] = {
                    **(self._bridge._view_per_role.get(role) or {}),
                    **delta
                }
                self._bridge.send_to_role(role, {"type": "view", "view": delta})
        else:
            # Broadcast to all: update global view state.
            self._bridge._view = {**(self._bridge._view or {}), **delta}
            self._bridge.broadcast({"type": "view", "view": delta})
        return self

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
