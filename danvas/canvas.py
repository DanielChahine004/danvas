"""Canvas: the public entry point. Holds components and serves the app."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
import warnings
from collections import namedtuple
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    # `Unpack[Place]` types every factory's **place kwargs (PEP 692) for editor
    # autocomplete. Annotations are lazy strings (the __future__ import above), so
    # this import never runs at import time — no runtime dependency added.
    from typing_extensions import Unpack

from . import server
from ._flags import LAYOUT_FLAGS
from .kernel import spawn
from .arrow import Arrow, _arrow_props
from .bridge import Bridge
from .components import Inspector  # spawned directly by the toolbar UI toggle
from ._layout import _FlowLayout, _LayoutMixin  # noqa: F401  (_FlowLayout re-exported)
from ._factories import _FactoryMixin
from .shapes import (
    BaseShape, DrawingShape,
    Geo, Text, Note, Draw, Highlight, Line, Frame,
    _segments_from_points, _line_points,
)


# serve() helper return types (see Canvas._resolve_exposure /
# _maybe_handoff_reload). ``_Exposure`` is the viewer-reach decision; the gating
# truth-table lives in a pure function so it can be unit-tested. ``_ReloadHandoff``
# tells serve() whether to return early (the call spawned the file-watch monitor)
# and whether to override open_browser (a reload restart reuses the existing tab).
_Exposure = namedtuple("_Exposure", "public_bind ui_inspector ui_graveyard cursors")
_ReloadHandoff = namedtuple("_ReloadHandoff", "should_return open_browser")


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
    decorative: bool      # sugar: grabbable=False + operable=False + frame=False


_NAV_MODES = frozenset(("free", "scroll_y", "scroll_x"))


def _coerce_navigation(value):
    """Normalise a ``navigation=`` argument to ``{"mode": ..., "zoom": ...}``."""
    if isinstance(value, str):
        if value not in _NAV_MODES:
            raise ValueError(
                f"navigation must be one of {sorted(_NAV_MODES)!r}, got {value!r}")
        return {"mode": value, "zoom": 1.0}
    if isinstance(value, (tuple, list)) and len(value) == 2:
        mode, zoom = value
        if mode not in _NAV_MODES:
            raise ValueError(
                f"navigation must be one of {sorted(_NAV_MODES)!r}, got {mode!r}")
        return {"mode": str(mode), "zoom": float(zoom)}
    raise TypeError(
        f"navigation must be a mode string or (mode, zoom) tuple, got {value!r}")


def _caller_globals():
    """The globals of the first frame *outside* danvas — the user's script or
    notebook that constructed the Canvas — so the Inspector's globals view works
    without an explicit ``serve(namespace=globals())``. Walks past danvas's own
    frames (so internal construction doesn't capture danvas's module), and returns
    a live reference (later-assigned variables show up too). ``None`` if it can't
    be determined (e.g. an exotic embedding)."""
    try:
        frame = sys._getframe(1)
    except Exception:  # noqa: BLE001 - no frame support on some runtimes
        return None
    depth = 0
    while frame is not None and depth < 30:
        name = frame.f_globals.get("__name__") or ""
        if name != "danvas" and not name.startswith("danvas."):
            return frame.f_globals
        frame = frame.f_back
        depth += 1
    return None


class Canvas(_FactoryMixin, _LayoutMixin):
    def __init__(self):
        self._bridge = Bridge()
        # Let the bridge call back into the canvas for native-UI actions (the
        # toolbar Inspector toggle); harmless until serve() enables the feature.
        self._bridge._canvas = self
        self._components = []
        self._arrows = []
        self._shapes = []   # managed canvas shapes (geo/text/note/draw/line/frame/highlight)
        self._named = {}  # name -> component/arrow/shape, for canvas.<name>
        self._serving = False
        self._server = None
        self._tunnel = None
        self._public_bind = False
        # Default the Inspector's "globals" view to wherever this Canvas was
        # created (the user's script/notebook), so the variable explorer works
        # out of the box. An explicit serve(namespace=...) / Inspector(namespace=)
        # overrides it, and the view is gated to a private local bind by default,
        # so this never exposes your variables to a shared canvas on its own.
        self._namespace = _caller_globals()
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
        # serve(persist=...) state. ``_persist_path`` is the JSON file the canvas
        # auto-loads on startup and auto-saves to on every user change; None when
        # persistence is off. The debounce coalesces a burst of edits (a text
        # shape emits a draw diff per keystroke) into at most one write per
        # window. The lock guards the timer/scheduling from the two threads that
        # can trigger a save (the event loop for draws, a dispatch thread for
        # layout) plus the flush on shutdown.
        self._persist_path = None
        self._persist_timer = None
        self._persist_lock = threading.Lock()
        # Stack of active auto-layout containers (grid/column/row). The innermost
        # one places any panel inserted inside its `with` block that didn't get an
        # explicit x/y or relative anchor. Empty = panels auto-cascade as before.
        self._layout_stack = []

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
        monitor). Uses the same ``spawn`` primitive as the ``threaded=True``
        input handlers — a daemon thread that lives as long as the worker runs
        (a producer loop runs for the app's lifetime), so neither blocks
        interpreter shutdown / a reload's teardown, and a crash is logged.
        """
        for fn, args, kwargs in self._background:
            spawn(fn, *args, name="danvas-background", **kwargs)

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

    def on_dispatch(self, fn):
        """Observe handler execution: ``fn(event)``. Decorator-friendly.

        The Python-side twin of :meth:`on_frame` — where ``on_frame`` watches the
        wire, this watches *which handler ran*. As each input/layout handler is
        queued, starts, and finishes (or errors), ``fn`` is called with a trace
        event dict (``trace``/``seq``/``comp``/``event``/``handler``/``mode``/
        ``phase``/``t``/``dur_ms`` — see :meth:`Bridge.add_dispatch_tap`). All the
        handlers one browser action fans out to share a ``trace`` id, so a tap can
        group and render a live execution trace — queued, then in-progress, then
        done::

            @canvas.on_dispatch
            def _(e):
                print(e["phase"], e["handler"], e.get("dur_ms"))

        Handlers that run ``threaded=True`` report their start/finish from their
        own thread, so concurrent handlers' events interleave — the tap may be
        called from several threads at once, so keep it fast and thread-safe.
        Registering a tap turns the (otherwise skipped) dispatch instrumentation
        on; with none registered it costs nothing. Remove with :meth:`off_dispatch`.
        """
        return self._bridge.add_dispatch_tap(fn)

    def off_dispatch(self, fn):
        """Remove a dispatch observer registered with :meth:`on_dispatch`."""
        self._bridge.remove_dispatch_tap(fn)

    def trace_calls(self, enabled=True):
        """Turn on *deep* dispatch tracing: follow each handler into your own
        functions, not just the handler itself.

        With this on, :meth:`on_dispatch` taps also receive ``start``/``done``
        events for the calls a handler makes *into your project's code*, each
        carrying a ``depth`` (0 = the handler, 1 = a function it calls, …) so a
        trace view can indent the call tree. Calls into danvas, the standard
        library, and third-party packages are skipped — the tree stays your code.

        It works by installing a ``sys.setprofile`` probe for the duration of each
        handler, which costs more than the shallow handler trace, so it's off by
        default and meant to be switched on while debugging. Returns ``self``.
        """
        self._bridge._trace_deep = bool(enabled)
        return self

    def trace_history(self):
        """Return the recorded dispatch history for after-the-fact debugging.

        Once the canvas is serving, every handler dispatch is recorded into a
        bounded ring of the most recent actions (default 50), whether or not a
        trace panel is open — so you can inspect what just happened. Returns a
        list (oldest → newest) of ``{trace, comp, event, frames}``, where each
        frame is ``{handler, depth, mode, status, dur_ms}`` (``status`` is
        ``"running"`` / ``"done"`` / ``"error"``; an errored frame also carries
        ``error``). The list is a copy — mutating it won't touch the live buffer.

        Recording is shallow (handler-level) unless deep tracing is on
        (:meth:`trace_calls`), which also records the nested user-code calls.
        """
        return self._bridge._trace_history_snapshot()

    def trace(self, name="dispatch_trace", label="dispatch trace", deep=True,
              **place):
        """Insert a live, back-traceable **dispatch-trace panel** on the canvas.

        It shows each browser action as it runs — the handlers it fires and, with
        ``deep=True`` (default), the calls those handlers make into your own
        functions — as an indented call tree, amber while running, green when
        done, red on error, with timings, newest action on top. It's a danvas
        React panel (authored in Python), so it needs no separate build; under the
        hood it turns on :meth:`trace_calls` and streams the :meth:`on_dispatch`
        events into the panel. ``**place`` positions it like any other panel (see
        :meth:`insert`). Returns the panel.

        Meant for development — the deep probe has a cost — so add it while you're
        wiring interactions and remove it when you're done.
        """
        from . import _trace
        from .components import React

        # Construct React directly (not via canvas.react) so the height reaches
        # the constructor: a number there turns *off* auto-height, making the panel
        # a fixed, resizable box whose history scrolls — instead of one that grows
        # to fit every action and ignores a manual resize. Seed it with the history
        # recorded so far (props replay on mount) so it opens already populated.
        w = place.pop("w", place.pop("width", 420))
        h = place.pop("h", place.pop("height", 340))
        panel = React(source=_trace.PANEL_JSX, name=name, label=label,
                      props={"history": self.trace_history()}, w=w, h=h)
        # The panel seeds its view from props["history"] on mount, but that seed
        # was frozen here at open time. The live ring keeps growing, so anything
        # that re-mounts the panel from the server's props — a page reload, a
        # reconnect, a second viewer — would re-seed from the stale (often empty)
        # snapshot and drop every action recorded since. Refresh the seed from the
        # live history each time the panel's props are composed for (re)register,
        # so a fresh mount always starts from what's actually happened so far.
        def _register_props_for(role=None, client_id=None, _orig=panel.register_props_for):
            panel._data["history"] = self.trace_history()
            return _orig(role, client_id)
        panel.register_props_for = _register_props_for
        self.insert(panel, **place)
        if deep:
            self.trace_calls(True)
        # Remember the tap on the panel so a launcher (the Inspector's Trace
        # button) can detach it when it closes the panel, instead of leaking a
        # tap that pushes to a gone panel.
        tap = lambda e: panel.push(e)
        panel._dispatch_tap = tap
        # A dev panel: deleting it in the browser should just close it (re-open
        # from the Inspector's Trace button), not send it to the graveyard.
        panel._ephemeral = True
        self.on_dispatch(tap)
        _trace.start_thread_sampler(self, panel)
        return panel

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

    def on_connect(self, fn):
        """Run ``fn(viewer)`` once each time a viewer connects. Decorator-friendly.

        ``viewer`` is the same dict handed to every handler —
        ``id``/``name``/``color``/``cursor``/``device``/``role`` — so a single
        hook lets you tailor the canvas to *who* (or *what*) just joined. The
        common case is adapting the layout to a phone, reusing the per-viewer
        ``client_id=`` scoping that :meth:`~danvas.components.base.BaseComponent.set_layout`
        already supports::

            @canvas.on_connect
            def adapt(viewer):
                if viewer["device"] == "mobile":
                    for i, panel in enumerate(panels):
                        panel.set_layout(client_id=viewer["id"],
                                         x=0, y=i * 220, w=360)

        The same pattern targets any viewer attribute (``role``, ``name``, …),
        so you don't need a separate scoping axis per attribute. It fires after
        the viewer's initial state has been sent, so a ``set_layout``/``update``
        here arrives as a live tweak on top — run on the dispatch thread (off the
        event loop), so it's safe to drive components from it. ``device`` is a
        best-effort, spoofable User-Agent classification, so use it for
        presentation, never authorization (gate those on ``role``). Remove with
        :meth:`off_connect`.
        """
        return self._bridge.add_connect_tap(fn)

    def off_connect(self, fn):
        """Remove a connect observer registered with :meth:`on_connect`."""
        self._bridge.remove_connect_tap(fn)

    def on_disconnect(self, fn):
        """Run ``fn(viewer)`` once each time a viewer leaves. Decorator-friendly.

        The symmetric twin of :meth:`on_connect`: ``viewer`` is the departed
        viewer's last-known dict (``id``/``name``/.../``role``). Use it to free
        whatever you set up for that viewer — release a per-viewer resource, log
        how long they stayed, drop them from your own bookkeeping::

            sessions = {}

            @canvas.on_connect
            def _(v): sessions[v["id"]] = time.time()

            @canvas.on_disconnect
            def _(v):
                started = sessions.pop(v["id"], None)
                if started: print(v["name"], "stayed", time.time() - started, "s")

        It fires after the viewer is already off the roster, so don't try to
        message them from here — it's for cleanup. Runs on the dispatch thread
        (off the event loop). Remove with :meth:`off_disconnect`.
        """
        return self._bridge.add_disconnect_tap(fn)

    def off_disconnect(self, fn):
        """Remove a disconnect observer registered with :meth:`on_disconnect`."""
        self._bridge.remove_disconnect_tap(fn)

    @property
    def shapes(self):
        """Return a list of all managed shapes on the canvas.

        These are the shapes created with :meth:`geo`, :meth:`text`,
        :meth:`note`, :meth:`draw`, :meth:`line`, :meth:`frame`, and
        :meth:`highlight` — not the panels (:attr:`components`) and not the
        user's free-form drawings (:attr:`drawings`).
        """
        return list(self._shapes)

    @property
    def drawings(self):
        """Live snapshot of user-drawn (ephemeral) shapes as a dict.

        Keys are shape ids (``'shape:…'`` strings).  Values are
        :class:`~danvas.shapes.DrawingShape` objects with ``update()`` and
        ``remove()`` methods that broadcast draw diffs to every browser.
        The snapshot is fresh on each access — it reflects the current server
        shadow store, which is kept in step with every browser's drawing state::

            for sid, s in canvas.drawings.items():
                if s.type == 'text':
                    s.update(color='red')
        """
        return {
            k: DrawingShape(v, self._bridge)
            for k, v in self._bridge._drawings.items()
        }

    def on_draw(self, fn):
        """Register ``fn`` to be called whenever user-drawn shapes change.

        ``fn(event)`` receives a dict with three keys:

        - ``added``   — list of :class:`~danvas.shapes.DrawingShape` for
          shapes just created by a user
        - ``updated`` — list of :class:`~danvas.shapes.DrawingShape` for
          shapes just modified (each reflects the new state)
        - ``removed`` — list of shape id strings for deleted shapes

        Fires off the event loop on the dispatch thread, so it is safe to call
        ``shape.update()``, drive panels, or read ``canvas.drawings`` from
        inside it.  Remove with :meth:`off_draw`::

            @canvas.on_draw
            def _(event):
                for shape in event['added']:
                    print('new', shape.type, 'at', shape.x, shape.y)
        """
        return self._bridge.add_draw_tap(fn)

    def off_draw(self, fn):
        """Remove a draw observer registered with :meth:`on_draw`."""
        self._bridge.remove_draw_tap(fn)

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
        print(f"[danvas] {arrow} {msg.get('type', '?')}{name} {body}")

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
        so panels broadcast live. See :func:`danvas.autopanel` for the
        arguments; returns the capture controller. Idempotent.

        Per cell, a ``# danvas:`` directive line overrides placement (or opts
        out with ``skip``). Pass ``auto=False`` to invert the default: mirror
        *nothing* unless a cell carries such a directive (e.g. a bare
        ``# danvas: show``) — an explicit allowlist instead of a blocklist.

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

    def _toggle_ui_inspector(self, at=None):
        """Spawn (or remove) the native-UI ephemeral Inspector. Toggles.

        Called by the bridge when a browser hits the toolbar Inspector button
        (only when :meth:`serve` enabled it). The panel is a normal
        :class:`~danvas.Inspector` under a reserved name, so re-toggling
        removes it and it broadcasts to every open view like any other panel.
        Returns the inspector when spawned, ``None`` when removed.

        ``at`` is the spawning viewer's viewport *centre* (``{"x", "y"}`` in
        canvas coords, sent by the browser): the inspector opens centred in that
        viewer's current view, so a viewer who has panned away still gets it
        on-screen and centred. Falls back to a fixed position when unknown.
        """
        name = "__ui_inspector__"
        existing = self._named.get(name)
        if existing is not None:
            self.remove(existing)
            return None
        insp = Inspector(name=name, refresh=1.0, source="components",
                         label="inspector")
        if isinstance(at, dict) and at.get("x") is not None and at.get("y") is not None:
            # Place the panel so its centre sits at the viewport centre.
            w = getattr(insp, "default_w", 380)
            h = getattr(insp, "default_h", 320)
            x, y = at["x"] - w / 2, at["y"] - h / 2
        else:
            x, y = 120, 120
        # Pin the wire id to the reserved name so the frontend's toolbar Inspector
        # button can detect this panel (it watches register/remove for this id) and
        # reflect its open/closed state.
        return self.insert(insp, x=x, y=y, component_id=name)

    def insert(self, component, x=None, y=None, w=None, h=None, rotation=None,
               locked=False, draggable=True, resizable=True, operable=None,
               grabbable=None, frame=None, decorative=False, name=None, queue=None,
               below=None, above=None, right_of=None, left_of=None, gap=16,
               width=None, height=None, roles=None, lock_for=None,
               component_id=None):
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

        - ``decorative=True`` is the one-liner for a purely visual overlay that
          floats on the canvas: no chrome, never selectable, and click-through to
          whatever sits beneath it (a label, a slider, the canvas itself). It is
          exactly ``grabbable=False, operable=False, frame=False`` composed — use
          it instead of spelling out all three. Any of the three can still be
          pinned by passing it explicitly (the explicit value wins). Ideal for
          cursor-following badges, watermarks, and HUD glyphs.

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
        #
        # If any anchor has no position yet (it was auto-cascaded and the browser
        # hasn't reported back), we defer rather than raise: the component is
        # inserted without coordinates (joins the masonry flow) and a one-shot
        # on_layout fires on the first unpositioned anchor to apply the relative
        # placement once the browser reports its position. This lets panels be
        # declared in natural order — anchor first, relative panel second — even
        # when neither has an explicit x/y.
        if below is not None or above is not None or right_of is not None \
                or left_of is not None:
            new_w = w if w is not None else component.w
            new_h = h if h is not None else component.h
            # Identify any anchor that still lacks a position.
            def _resolve_anchor(ref):
                return self._named.get(ref) if isinstance(ref, str) else ref
            _b = _resolve_anchor(below)
            _a = _resolve_anchor(above)
            _r = _resolve_anchor(right_of)
            _l = _resolve_anchor(left_of)
            _unpositioned = [
                c for c in (_b, _a, _r, _l)
                if c is not None and (c.x is None or c.y is None)
            ]
            if _unpositioned:
                # Defer: register a one-shot on_layout on the first unpositioned
                # anchor. When it (or any other anchor) gets a position from the
                # browser, try to apply the placement. _done guards against repeat
                # fires (the handler stays registered but becomes a no-op).
                _done = [False]
                _x_explicit, _y_explicit = x, y
                # Capture all the locals we need explicitly so the closure
                # is self-contained beyond insert()'s stack frame.
                _canvas, _comp_ref = self, component
                _below_c, _above_c = below, above
                _right_of_c, _left_of_c = right_of, left_of
                _gap_c, _nw_c, _nh_c = gap, new_w, new_h
                def _deferred(_layout_comp):
                    # Single-arg so _accepts_viewer doesn't pass viewer as a
                    # second positional arg and corrupt the closure captures.
                    if _done[0]:
                        return
                    try:
                        rx, ry = _canvas._relative_position(
                            _below_c, _above_c, _right_of_c, _left_of_c,
                            _gap_c, _nw_c, _nh_c)
                    except ValueError:
                        return   # another anchor still unpositioned; wait
                    _done[0] = True
                    _comp_ref.set_layout(
                        x=_x_explicit if _x_explicit is not None else rx,
                        y=_y_explicit if _y_explicit is not None else ry,
                    )
                _unpositioned[0].on_layout(_deferred)
            else:
                # All anchors already have positions: resolve immediately.
                rx, ry = self._relative_position(
                    below, above, right_of, left_of, gap, new_w, new_h)
                if x is None:
                    x = rx
                if y is None:
                    y = ry
            # Register cascade deps regardless of whether placement was deferred
            # so height changes propagate correctly once positions are known.
            if _b is not None:
                _b._below_deps.append((component, gap))
            if _r is not None:
                _r._right_of_deps.append((component, gap))
        # Auto-layout: inside a `with canvas.grid(...)`/`column`/`row` block, a
        # panel given neither an explicit position nor a relative anchor takes the
        # next slot (and the layout's default slot size, unless w/h were given).
        scoped_layout = None
        _container_placed = None   # set when a Container (not _FlowLayout) did placement
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
            from ._layout import Container as _Container
            if isinstance(flow, _Container):
                _container_placed = flow
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
        # Snapshot Python-defined layout so canvas.reset_layout() can restore it.
        # Captured here, after all placement logic resolves, before any browser
        # feedback can alter the values. Deleted panels (canvas.remove) are skipped
        # by reset_layout() because they leave self._bridge._components at removal.
        component._initial_layout = {
            "x": component.x, "y": component.y,
            "w": component.w, "h": component.h,
        }
        if rotation is not None:
            component._rotation = rotation
        # ``decorative=True`` is sugar for a non-interactive floating overlay: no
        # card chrome, never selectable, and click-through to whatever is beneath
        # (e.g. the cursor orbs in examples/moving_widget). It just composes three
        # real flags — grabbable=False, operable=False, frame=False — each of which
        # the caller can still pin explicitly (an explicit value wins; these three
        # are left ``None`` in the signature precisely so an override is
        # distinguishable from "unset").
        if decorative:
            if grabbable is None:
                grabbable = False
            if operable is None:
                operable = False
            if frame is None:
                frame = False
        # Resolve any flag the caller left unset to the shared default before the
        # apply loop (it treats a value differing from the default as an override).
        if operable is None:
            operable = LAYOUT_FLAGS["operable"].default
        if grabbable is None:
            grabbable = LAYOUT_FLAGS["grabbable"].default
        if frame is None:
            frame = LAYOUT_FLAGS["frame"].default
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
        # The wire id is normally a fresh UUID. ``component_id`` lets a caller pin
        # a stable, known id — used for the reserved ``__ui_inspector__`` panel so
        # the frontend's toolbar button can track it on the wire (the register/
        # remove frames carry the id, not the danvas name); see _toggle_ui_inspector.
        component_id = component_id or uuid.uuid4().hex
        component._bind(component_id, self._bridge)
        self._bridge.add_component(component)
        self._components.append(component)
        # Wire introspection components: Inspector exposes ``_namespace`` (for
        # its globals view) and ``_canvas`` (to read live component state).
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
        # After live registration, broadcast the updated container tree so the
        # frontend's auto-repack knows about the new member.  Done after
        # register_live so the panel already exists in the frontend when the
        # container_sync arrives.
        if _container_placed is not None:
            _container_placed._post_place()
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
                    f"can't place {kind} {comp.name!r}: it has no position yet"
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
    # canvas.slider / button / react / markdown / show / … live in _FactoryMixin
    # (danvas/_factories.py); _make() and _INSERT_KEYS moved there too.

    # -- canvas shape factories -----------------------------------------------

    def _add_shape(self, shape):
        """Register a managed shape, handle name eviction, wire to bridge."""
        if shape.name is None:
            shape.name = self._auto_name(shape._type)
        name = shape.name
        old = self._named.get(name)
        if old is not None and old is not shape:
            if isinstance(old, BaseShape):
                if old in self._shapes:
                    self._shapes.remove(old)
                self._bridge.remove_shape(old.id)
            elif old in self._arrows:
                self.disconnect(old)
            elif old in self._components:
                self.remove(old)
        self._named[name] = shape
        self._shapes.append(shape)
        self._bridge.add_shape(shape)
        return shape

    def _shape_place(self, x, y, right_of, left_of, below, above, gap,
                     new_w=200, new_h=200):
        """Resolve optional relative placement keywords for shape factories.

        Any canvas object (panel or shape) or a name string is accepted as an
        anchor.  Returns the final ``(x, y)`` to use.
        """
        def resolve(ref):
            return self._named.get(ref) if isinstance(ref, str) else ref

        def dim(ref, d):
            v = getattr(ref, d, None)
            if v is None and hasattr(ref, "_props"):
                v = ref._props.get(d)
            return float(v) if v is not None else 200.0

        right_of = resolve(right_of)
        left_of  = resolve(left_of)
        below    = resolve(below)
        above    = resolve(above)

        if below is not None:
            x, y = below.x, below.y + dim(below, "h") + gap
        elif above is not None:
            x, y = above.x, above.y - gap - new_h
        if right_of is not None:
            x = right_of.x + dim(right_of, "w") + gap
            if below is None and above is None:
                y = right_of.y
        elif left_of is not None:
            x = left_of.x - gap - new_w
            if below is None and above is None:
                y = left_of.y

        return float(x), float(y)

    def geo(self, x=0, y=0, w=200, h=150, geo="rectangle", name=None,
            right_of=None, left_of=None, below=None, above=None, gap=16,
            **props):
        """Place a geo shape (rectangle, ellipse, cloud, star, …) on the canvas.

        ``geo`` selects the sub-type; ``w``/``h`` set dimensions.  Style
        kwargs: ``color``, ``fill``, ``dash``, ``size``, ``font``, ``align``.
        ``name`` is the eviction key (re-inserting under the same name replaces
        the old shape).  Returns a live handle::

            box = canvas.geo(x=100, y=100, w=200, h=80, geo='ellipse',
                             color='blue', fill='semi')
            box.text = 'hello'   # live update
            box.color = 'red'
        """
        if right_of is not None or left_of is not None \
                or below is not None or above is not None:
            x, y = self._shape_place(x, y, right_of, left_of, below, above,
                                     gap, new_w=w, new_h=h)
        shape_id = uuid.uuid4().hex
        return self._add_shape(Geo(shape_id, x, y, w=w, h=h, geo=geo,
                                   name=name, **props))

    def text(self, x=0, y=0, text="", name=None,
             right_of=None, left_of=None, below=None, above=None, gap=16,
             **props):
        """Place a plain floating text shape on the canvas.

        Style kwargs: ``color``, ``size``, ``font`` (draw/sans/serif/mono).
        Returns a live handle::

            lbl = canvas.text(x=200, y=50, text='Hello', font='sans', size='xl')
            lbl.text = 'World'
        """
        if right_of is not None or left_of is not None \
                or below is not None or above is not None:
            x, y = self._shape_place(x, y, right_of, left_of, below, above, gap)
        shape_id = uuid.uuid4().hex
        return self._add_shape(Text(shape_id, x, y, text=text, name=name, **props))

    def note(self, x=0, y=0, text="", name=None,
             right_of=None, left_of=None, below=None, above=None, gap=16,
             **props):
        """Place a sticky note on the canvas.

        Style kwargs: ``color``, ``size``, ``font``, ``align``.
        Returns a live handle::

            n = canvas.note(x=400, y=100, text='TODO', color='yellow')
            n.text = 'DONE'
        """
        if right_of is not None or left_of is not None \
                or below is not None or above is not None:
            x, y = self._shape_place(x, y, right_of, left_of, below, above, gap)
        shape_id = uuid.uuid4().hex
        return self._add_shape(Note(shape_id, x, y, text=text, name=name, **props))

    def draw(self, points, x=None, y=None, name=None,
             right_of=None, left_of=None, below=None, above=None, gap=16,
             **props):
        """Place a freehand stroke on the canvas.

        ``points`` is a list of ``(x, y)`` or ``(x, y, pressure)`` tuples, or
        a list of segment dicts ``{type, points}``.  The bounding-box
        origin becomes the shape's ``x``/``y`` unless overridden.  Style
        kwargs: ``color``, ``fill``, ``dash``, ``size``::

            canvas.draw([(0,0),(40,20),(80,5),(120,30)], color='red', size='l')
        """
        ox, oy, segments = _segments_from_points(points)
        fx = x if x is not None else ox
        fy = y if y is not None else oy
        if right_of is not None or left_of is not None \
                or below is not None or above is not None:
            fx, fy = self._shape_place(fx, fy, right_of, left_of, below, above, gap)
        shape_id = uuid.uuid4().hex
        return self._add_shape(Draw(shape_id, fx, fy, segments, name=name, **props))

    def highlight(self, points, x=None, y=None, name=None,
                  right_of=None, left_of=None, below=None, above=None, gap=16,
                  **props):
        """Place a semi-transparent highlighter stroke on the canvas.

        Same point format as :meth:`draw`.  Style kwargs: ``color``, ``size``::

            canvas.highlight([(10,10),(200,10)], color='yellow', size='l')
        """
        ox, oy, segments = _segments_from_points(points)
        fx = x if x is not None else ox
        fy = y if y is not None else oy
        if right_of is not None or left_of is not None \
                or below is not None or above is not None:
            fx, fy = self._shape_place(fx, fy, right_of, left_of, below, above, gap)
        shape_id = uuid.uuid4().hex
        return self._add_shape(Highlight(shape_id, fx, fy, segments,
                                         name=name, **props))

    def line(self, points, x=None, y=None, name=None,
             right_of=None, left_of=None, below=None, above=None, gap=16,
             **props):
        """Place a polyline (or cubic spline) on the canvas.

        ``points`` is a list of ``(x, y)`` tuples; the first point becomes the
        shape's position and all others are stored relative to it.
        ``spline='cubic'`` makes the line curve smoothly through them.
        Style kwargs: ``color``, ``dash``, ``size``::

            canvas.line([(0,0),(100,50),(200,0)], color='black', spline='cubic')
        """
        if not points:
            raise ValueError("line() requires at least one point")
        ox, oy = float(points[0][0]), float(points[0][1])
        fx = x if x is not None else ox
        fy = y if y is not None else oy
        if right_of is not None or left_of is not None \
                or below is not None or above is not None:
            fx, fy = self._shape_place(fx, fy, right_of, left_of, below, above, gap)
        pts_dict = _line_points(points)
        shape_id = uuid.uuid4().hex
        return self._add_shape(Line(shape_id, fx, fy, pts_dict, name=name, **props))

    def frame(self, x=0, y=0, w=400, h=300, label="", name=None,
              right_of=None, left_of=None, below=None, above=None, gap=16,
              **props):
        """Place an artboard frame on the canvas.

        ``label`` is the visible frame title (use ``name`` for the Python
        identity / eviction key).  Returns a live handle::

            art = canvas.frame(x=50, y=300, w=800, h=500, label='Slide 1')
            art.label = 'Slide 2'
            art.w = 1000
        """
        if right_of is not None or left_of is not None \
                or below is not None or above is not None:
            x, y = self._shape_place(x, y, right_of, left_of, below, above,
                                     gap, new_w=w, new_h=h)
        shape_id = uuid.uuid4().hex
        return self._add_shape(Frame(shape_id, x, y, w=w, h=h, label=label,
                                     name=name, **props))

    def remove_shape(self, shape):
        """Remove a managed shape returned by :meth:`geo` / :meth:`text` / etc.

        Works live while serving.  Safe to call on a shape that was already
        removed; then it is a no-op.
        """
        if isinstance(shape, str):
            shape = self._named.get(shape)
        if shape is None or shape not in self._shapes:
            return
        self._shapes.remove(shape)
        for nm, obj in list(self._named.items()):
            if obj is shape:
                del self._named[nm]
        self._bridge.remove_shape(shape.id)
        shape._bridge = None
        return shape

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
        component._visible = False
        self._bridge.remove_component(component.id)
        component._bridge = None
        on_removed = getattr(component, "_on_removed", None)
        if callable(on_removed):
            on_removed()
        # A trace panel feeds itself via an on_dispatch tap; detach it on removal
        # (button-close or a browser delete) so it doesn't keep pushing to a panel
        # that's gone.
        tap = getattr(component, "_dispatch_tap", None)
        if tap is not None:
            self.off_dispatch(tap)
        return component

    def hide(self, component):
        """Remove a panel from the browser without destroying its Python state.

        The component stays in :attr:`components`, keeps its id, value,
        and all registered callbacks. ``update()`` / ``push()`` calls while
        hidden are silently dropped. Call :meth:`show` to make it reappear.

        A no-op if the component is not currently visible (already hidden,
        not yet inserted, or fully removed).
        """
        if not getattr(component, "_visible", False):
            return
        component._visible = False
        # Remove from the bridge registry so reconnecting clients don't get it.
        self._bridge._components.pop(component.id, None)
        # Tell current clients to remove the shape.
        self._bridge.broadcast({"type": "remove", "id": component.id})

    def unhide(self, component):
        """Make a previously hidden panel reappear on the canvas.

        Re-registers the component with the bridge and pushes its full state
        to all currently connected clients, exactly as if it had just been
        inserted. The panel reappears at its last known position with all
        Python state (value, callbacks) intact.

        A no-op if the component is already visible or was fully removed
        (use :meth:`insert` for a fresh insert instead).
        """
        if getattr(component, "_visible", False):
            return
        if component not in self._components:
            return  # fully removed — use insert() instead
        component._visible = True
        component._graveyarded = False
        self._bridge._components[component.id] = component
        self._bridge.register_live(component)

    def reset_layout(self):
        """Restore every live panel to its Python-defined position and size.

        Replays the (x, y, w, h) captured at :meth:`insert` time for every panel
        currently registered on the canvas. Panels removed via :meth:`remove` are
        skipped automatically because they leave the bridge registry at removal time.
        Panels the user deleted in the canvas UI (without going through Python) are
        still in the registry and will have their stored geometry refreshed; they
        will reappear on next reconnect at their original positions.

        For single-viewer canvases this fully undoes all hand-drags. For role/client-
        scoped layouts the shared base is restored; per-viewer overlays (set via
        ``set_layout(roles=…)`` or ``set_layout(client_id=…)``) are left untouched.
        """
        for comp in list(self._bridge._components.values()):
            il = getattr(comp, "_initial_layout", None)
            if il is None:
                continue
            kwargs = {"w": il["w"], "h": il["h"]}
            if il["x"] is not None and il["y"] is not None:
                kwargs["x"] = il["x"]
                kwargs["y"] = il["y"]
            comp.set_layout(**kwargs)

    def clear(self):
        """Remove all panels and arrows from the canvas. Works live while serving."""
        for c in list(self._components):
            self.remove(c)
        for a in list(self._arrows):
            self.disconnect(a)
        return self

    def define(self, name, source=None, path=None):
        """Register a shared React component usable by name in every ``react()`` panel.

        Pass JSX ``source`` (or a file ``path=``) that declares a component named
        ``name`` — e.g. ``define("StatusPill", "function StatusPill({kind, children}) "
        "{ return <span className={'pill '+kind}>{children}</span> }")``. It is
        delivered to the browser once and made available in every React panel's
        scope, so any panel can render ``<StatusPill kind="ok">In stock</StatusPill>``
        without re-declaring it. This is how you kill the per-panel duplication of a
        shared table/button/badge: define it once here, use it everywhere.

        ``name`` must be a valid identifier (it's the component's name in JSX). Call
        this *before* creating the panels that use it; defining (or redefining) a
        component while serving recompiles the live panels with the new source.
        Pair with :meth:`style` for the component's shared CSS. Returns ``self``.
        """
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(
                f"define() name must be a valid identifier (the JSX component "
                f"name), got {name!r}")
        if path is not None:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
        if not source or not source.strip():
            raise ValueError("define() needs source= (JSX) or path= to a .jsx file")
        self._bridge._shared_components[name] = source
        if self._serving:
            self._bridge.broadcast_shared()
        return self

    def style(self, css):
        """Add a global stylesheet shared by every native (React) panel.

        The ``css`` is injected once into the page ``<head>`` — unlike a panel's
        own ``css=`` (rendered inside that one panel), this is shared by all of
        them, so the styles for components registered with :meth:`define` live in
        one place instead of being copied into every panel. Selectors are
        page-global, so scope them with your own class prefix (e.g. ``.pc-pill``)
        exactly as you would a panel's ``css=``.

        Calls accumulate (each adds rules, like multiple ``<style>`` tags), so call
        it once per stylesheet. Sandboxed ``Custom`` iframes are isolated and don't
        receive these styles. Applies live while serving. Returns ``self``.
        """
        css = css or ""
        if self._bridge._shared_styles:
            self._bridge._shared_styles += "\n" + css
        else:
            self._bridge._shared_styles = css
        if self._serving:
            self._bridge.broadcast_shared()
        return self

    def connect(self, start, end, name=None, text=None, **props):
        """Draw an arrow from panel ``start`` to panel ``end`` and return it.

        Both arguments are components previously passed to :meth:`insert`. The
        arrow binds to each panel, so it follows them as they move or
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
    def describe(self):
        """A plain-data inventory of the canvas — for an LLM (or a log) to read.

        Returns a list of dicts, one per panel then per arrow, each with the
        runtime state you *can't* see by reading the source: the resolved
        ``x/y/w/h`` (after auto-layout and any user drags), the current
        ``value``, ``visible`` and ``locked``. Pairs with :meth:`screenshot`
        (pixels) — this is the cheap text half: enough to verify the right
        components exist, are wired, laid out, and holding the values you expect.
        Values are length-capped reprs, so a giant table/array stays readable.
        """
        from .components.inspector import _short
        name_of = {id(c): n for n, c in self._named.items()}
        rows = []
        for c in self._components:
            rows.append({
                "name": name_of.get(id(c), ""),
                "label": c._props.get("label", ""),
                "type": type(c).__name__,   # "Slider", not the "React" wire type
                "value": _short(c.value),
                "visible": c.visible,
                "locked": c.locked,
                "x": c.x, "y": c.y, "w": c.w, "h": c.h,
            })
        for a in self._arrows:
            rows.append({
                "name": name_of.get(id(a), ""),
                "label": a.text or "",
                "type": "Arrow",
                "value": _short(f"{a.start.id} → {a.end.id}"),
                "visible": True, "locked": "",
                "x": "", "y": "", "w": "", "h": "",
            })
        return rows

    def screenshot(self, target=None, path=None, timeout=10.0):
        """Render the canvas (or specific panels) to a PNG via a connected browser.

        ``target`` is what to frame: ``None`` captures the whole canvas; a single
        panel captures just it; a list of panels frames them to their bounding
        box. Scene export — shapes at their canvas coordinates, independent of
        where any viewer's camera is, so the same call always yields the same
        image. Returns the PNG as ``bytes`` (and writes ``path`` if given).

        Requires an open browser tab — the browser is the only thing that can
        render. For headless/autonomous capture, point a tool at the served URL.
        """
        if target is None:
            shape_ids = []                      # empty → whole page
        else:
            panels = target if isinstance(target, (list, tuple)) else [target]
            shape_ids = [f"shape:{p.id}" for p in panels]
        png = self._bridge.request_image(shape_ids, timeout=timeout)
        if path is not None:
            with open(path, "wb") as f:
                f.write(png)
        return png

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
        """Build the formation dict: each panel's geometry, lock state, and any
        user-set value (input controls only — see ``_persist_state``)."""
        components = []
        for c in self._components:
            item = {
                "name": c.name,
                "id": c.id,
                "x": c.x,
                "y": c.y,
                "w": c.w,
                "h": c.h,
                "rotation": c.rotation,
                "opacity": c.opacity,
                # Every lock/chrome flag, straight from the shared table.
                **{name: getattr(c, name) for name in LAYOUT_FLAGS},
            }
            # User-set value (Slider/Toggle/TextField). Empty for content panels,
            # whose state is reproduced by re-running the code, so it's omitted.
            state = c._persist_state()
            if state:
                item["state"] = state
            components.append(item)
        arrows = [
            {
                "name": a.name,
                "start": a.start.name,
                "end": a.end.name,
                "props": dict(a._props),
            }
            for a in self._arrows
        ]
        # Managed shapes (canvas.geo/text/line/…). Each has a stable handle name
        # (auto-assigned — geo1, text1, … — when not given), so a user move/resize
        # can be matched back across a process restart, just like a panel.
        shapes = [
            {
                "name": s.name,
                "x": s.x,
                "y": s.y,
                "w": s._props.get("w"),
                "h": s._props.get("h"),
                "rotation": s.rotation,
                "opacity": s.opacity,
            }
            for s in self._shapes
        ]
        # Deletions: the handle names of everything the user sent to the graveyard
        # (panels, shapes, arrows). Saved by name so a restart can re-delete them
        # even though the code re-creates each on its next run.
        graveyard = [
            obj.name for obj in self._bridge._graveyarded.values()
            if getattr(obj, "name", None)
        ]
        return {"components": components, "arrows": arrows, "shapes": shapes,
                "graveyard": graveyard}

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
                opacity=item.get("opacity"),
                # Flags absent from an older save stay None (left unchanged).
                **{name: item.get(name) for name in LAYOUT_FLAGS},
            )
            # Restore the user-set value (input controls). Silent: routes through
            # the panel's update(), which pushes to the browser but never fires
            # on_change. Absent in older saves / for content panels -> skipped.
            state = item.get("state")
            if state:
                comp._restore_state(state)
        # Restore managed-shape geometry by name (geo1, text1, …). No clients are
        # connected at load time, so set the attributes directly; register_message
        # then replays the saved geometry to every joining client.
        shape_by_name = {s.name: s for s in self._shapes}
        for item in data.get("shapes", []):
            s = shape_by_name.get(item.get("name"))
            if s is None:
                continue
            for k in ("x", "y", "rotation", "opacity"):
                v = item.get(k)
                if v is not None:
                    setattr(s, k, v)
            for k in ("w", "h"):
                v = item.get(k)
                if v is not None and k in s._props:
                    s._props[k] = v
        # Re-apply deletions: re-graveyard (by name) everything the user deleted,
        # so a deleted panel/shape/arrow stays deleted across a restart even though
        # the code re-created it. Match through the unified name registry.
        for name in data.get("graveyard", []):
            obj = self._named.get(name)
            if obj is not None:
                self._bridge._graveyard(obj.id)

    @staticmethod
    def _read_json(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # -- automatic persistence (serve(persist=...)) ---------------------------
    @staticmethod
    def _default_persist_path():
        """Where ``persist=True`` saves when no path is given: ``<script>.canvas.json``.

        Derived from the running script (``__main__.__file__``) so the file sits
        next to it and is named after it. Falls back to ``danvas.canvas.json``
        in the working directory when there is no script file (an interactive
        REPL or a notebook, where ``__main__`` has no ``.py`` ``__file__``). Both
        end in ``.canvas.json`` so the default ``*.canvas.json`` gitignore catches
        them.
        """
        main = sys.modules.get("__main__")
        script = getattr(main, "__file__", None)
        if script and script.endswith(".py"):
            script = os.path.abspath(script)
            stem = os.path.splitext(os.path.basename(script))[0]
            return os.path.join(os.path.dirname(script), f"{stem}.canvas.json")
        return os.path.abspath("danvas.canvas.json")

    def _persist_setup(self, persist):
        """Resolve the persist file, load it if present, and arm the autosave.

        ``persist`` is the ``serve(persist=...)`` value: ``True`` for the default
        path, or a string path. Called once from serve() in the serving process.
        """
        path = persist if isinstance(persist, str) else self._default_persist_path()
        self._persist_path = path
        if os.path.exists(path):
            try:
                self._persist_load(path)
            except Exception:
                # A corrupt/half-written file must not stop the canvas from
                # serving -- warn and start fresh; the next save overwrites it.
                warnings.warn(f"persist: could not load {path!r}; starting fresh",
                              stacklevel=2)
                traceback.print_exc()
        # Arm last, so the load above (which mutates layout) doesn't trigger a
        # redundant save of what we just read back in.
        self._bridge._on_mutation = self._schedule_persist

    def _persist_load(self, path):
        """Apply a persist file: restore the panel formation and seed drawings.

        Reuses :meth:`_restore_layout` for the formation and seeds the bridge's
        live drawing set directly (so the saved drawings replay on connect and
        feed forward into the next autosave), rather than the full-document
        ``load_snapshot`` path that :meth:`load` uses.
        """
        data = self._read_json(path)
        if data.get("layout"):
            self._restore_layout(data["layout"])
        drawings = data.get("drawings")
        if isinstance(drawings, dict):
            self._bridge._drawings = dict(drawings)

    def _schedule_persist(self):
        """Debounce: (re)arm a one-shot timer that flushes after a quiet window.

        Called from the bridge on every user layout/draw change, possibly from
        the event loop (draw) or a dispatch thread (layout). Coalescing a burst
        of edits into one write keeps a mid-typing text shape (a diff per
        keystroke) from hammering the disk.
        """
        with self._persist_lock:
            if self._persist_path is None:
                return
            if self._persist_timer is not None:
                self._persist_timer.cancel()
            self._persist_timer = threading.Timer(1.0, self._persist_flush)
            self._persist_timer.daemon = True
            self._persist_timer.start()

    def _persist_flush(self):
        """Write the current canvas state to the persist file, now.

        The debounce target, and also called synchronously on shutdown to
        capture the final state. Cancels any pending timer so a flush and a
        debounced write can't both fire.
        """
        with self._persist_lock:
            path = self._persist_path
            if path is None:
                return
            if self._persist_timer is not None:
                self._persist_timer.cancel()
                self._persist_timer = None
        data = {"layout": self._layout(), "drawings": dict(self._bridge._drawings)}
        # Write to a temp file in the same directory and atomically replace, so a
        # crash mid-write can never leave a truncated (unloadable) file behind.
        tmp = f"{path}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise

    def wait_for_client(self, timeout=10.0):
        """Block until at least one browser is connected, or ``timeout`` elapses.

        Useful before :meth:`load`, which pushes to connected clients — give the
        freshly opened page a moment to connect first. Returns ``True`` if a
        client connected.
        """
        self._bridge._any_connected.wait(timeout=timeout)
        return bool(self._bridge._connections)

    def __getattr__(self, name):
        # ``canvas.<name>`` is the ergonomic accessor — sugar for ``canvas[name]``.
        # It only reaches here when normal attribute lookup fails, so a name that
        # collides with a real method/property (``inspector``, ``components``, …)
        # returns that attribute instead and never resolves to the component; the
        # insert-time warning flags the collision and points to ``canvas[name]``,
        # which always works. (_named is set in __init__; guard early/unpickle.)
        named = self.__dict__.get("_named", {})
        if name in named:
            return named[name]
        raise AttributeError(name)

    def __getitem__(self, name):
        """The canonical way to reach a component by its ``name=``.

        ``canvas["status"]`` always resolves to the component, even when the name
        shadows a Canvas attribute (where ``canvas.status`` would not). Prefer this
        form for names that might collide with a method; ``canvas.<name>`` stays a
        convenient shorthand for the rest.
        """
        try:
            return self._named[name]
        except KeyError:
            avail = ", ".join(map(repr, sorted(self._named))) or "none yet"
            raise KeyError(
                f"no component named {name!r} on this canvas; available names: "
                f"{avail}"
            ) from None

    def __contains__(self, name):
        """``"status" in canvas`` — True when a component has that ``name=``."""
        return name in self._named

    # Keys accepted in a ``view`` config, each paired with the coercion applied
    # before it is sent to the browser. Unknown keys are rejected so a typo
    # (e.g. ``zooom``) surfaces immediately rather than being silently ignored.
    _VIEW_KEYS = {
        "x": float, "y": float, "zoom": float,
        "min_zoom": float, "max_zoom": float,
        "locked": bool, "ui": bool, "grid": bool, "read_only": bool,
        "navigation": _coerce_navigation,
    }

    @classmethod
    def _normalize_view(cls, view):
        """Validate/coerce a ``serve(view=...)`` dict into the wire form.

        Returns ``None`` for ``None`` (leave every canvas default in place) and
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
              block=True, wait=True,
              tunnel=False, tunnel_provider="cloudflared", ui_inspector=None,
              ui_graveyard=None, cursors=None, view=None, desktop=None, window_title="danvas",
              window_size=(1200, 800), password=None, passwords=None,
              login_message=None, persist=False, hot_reload=False, debug=False,
              namespace=None, tldraw_license_key=None, watch=None):
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
        raises too.

        ``watch`` adds extra files for ``hot_reload`` to watch beyond the
        top-level ``.py`` files: a glob string or list of globs, resolved
        relative to the script's directory (``watch="*.jsx"``,
        ``watch=["*.css", "panels/**/*.json"]``). A change to any match restarts
        the worker, which re-reads files loaded via ``path=`` — handy for a
        ``canvas.react(path="panel.jsx")`` whose JSX lives in its own file. (For
        a single panel, :meth:`React.watch` live-reloads it *without* a restart;
        ``watch`` is the whole-process equivalent for arbitrary assets.) With ``tunnel=True`` the tunnel is opened once by the
        long-lived watcher process (not the restarting worker), so the public URL
        stays the same across reloads and the provider isn't re-created on every
        save — visitors just see a momentary blip during each restart.

        ``host`` is the bind address. The default ``"127.0.0.1"`` is local-only;
        pass ``"0.0.0.0"`` to let other devices on your network connect at
        ``http://<this-machine-ip>:<port>``.

        Pass ``tunnel=True`` to also expose the canvas on the public internet
        through a tunnel, so anyone — not just devices on your LAN — can open the
        printed ``https://…`` URL. ``tunnel_provider`` selects the backend
        (``"cloudflared"`` by default, needs no signup and no visitor
        interstitial; ``"localtunnel"`` is also supported). The tunnel is torn
        down when the server stops (or via :meth:`stop`).

        ``ui_inspector`` controls the native toolbar button that lets a viewer
        spawn an ephemeral :class:`~danvas.Inspector` from the browser. It can
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
        once. A password-protected canvas also shows a built-in **Sign out**
        button (clears the session, returns the password page) so a viewer can
        switch accounts.

        ``login_message`` adds a host note to that password page (above the
        field) — handy with ``passwords=`` to tell viewers which password to use,
        e.g. ``login_message='Spectators enter "view"; teams enter your team
        password.'`` It is shown as plain text (HTML-escaped, newlines kept).

        ``view`` configures how the canvas is presented and navigated, so
        the same canvas can be a free creative workspace or a fixed UI. Pass a
        dict with any of these keys (all optional):

        * ``x`` / ``y`` / ``zoom`` — initial camera: centre the view on canvas
          point ``(x, y)`` at ``zoom`` (1.0 = 100%). Any subset works; this is
          applied once on first load so a viewer who pans away isn't snapped back.
        * ``locked`` — ``True`` freezes pan and zoom entirely (a fixed kiosk view).
        * ``min_zoom`` / ``max_zoom`` — clamp how far the viewer can zoom.
        * ``ui`` — ``False`` hides the canvas's toolbars/menus for a chrome-free
          surface (defaults to shown).
        * ``grid`` — ``True`` shows the background grid.
        * ``read_only`` — ``True`` puts the canvas in read-only mode (no drawing).

        ``desktop`` selects a native app window (via pywebview) instead of the
        system browser. It defaults to ``None`` = auto: on inside a baked
        executable (``sys.frozen``), off otherwise — so the same script opens a
        browser in development and a contained window when run as the packaged
        ``.exe``. Force it either way with ``desktop=True``/``False``.
        ``window_title``/``window_size`` set that window's caption and pixel
        size. Desktop mode runs on the main thread and blocks until the window is
        closed (``block`` doesn't apply); if pywebview isn't installed it warns
        and falls back to the browser. See :meth:`bake` to build the executable.

        ``persist`` keeps the canvas across runs by saving it to a local JSON
        file and reloading it on startup — the automatic twin of :meth:`save` /
        :meth:`load`. ``persist=True`` uses a default path next to your script
        (``<script>.canvas.json``); pass a string to choose the file. When the
        file exists it is loaded once the panels your script created exist, so
        each panel snaps back to where the user last dragged it and their
        free-form drawings reappear; the file is then rewritten (debounced)
        whenever a viewer moves/resizes a panel or edits a drawing, and once more
        on a clean shutdown (``Ctrl+C`` / :meth:`stop`). Panels are code, so only
        their *placement* is persisted, never their existence or behaviour —
        delete a panel from your script and its stale saved position is simply
        ignored. Leave it ``False`` (the default) to run entirely fresh from the
        script every time, reading and writing nothing.

        ``debug=True`` logs every WebSocket frame to the console — what Python
        sends (``->``) and what each browser sends back (``<-``) — so "the panel
        isn't updating" turns into evidence: either the frame is on the wire or
        it isn't. (Programmatic equivalent: :meth:`on_frame`.) Connection lines
        ("viewer connected / disconnected") are always printed, debug or not.

        ``namespace`` is the variable namespace the Inspector's "globals" view
        lists. It defaults to the globals of wherever you created the ``Canvas``
        (your script/notebook), so the variable explorer works without passing
        anything; pass an explicit dict to override it, or ``{}`` to show none.
        The globals view is gated to a private local bind by default, so your
        variables aren't exposed on a shared/tunneled canvas unless you opt in.

        ``tldraw_license_key`` is **deprecated and ignored** — the frontend is
        tldraw-free, so no production licence key is needed. The argument is
        accepted (and discarded) only so older call sites don't break.
        """
        # Hot-reload / reload pre-flight handoff. May exit (the import
        # pre-flight), hand off to the file-watch monitor (return early), or
        # force open_browser off when a reload restart should reuse the tab.
        handoff = self._maybe_handoff_reload(hot_reload, block, port, tunnel,
                                             tunnel_provider, watch)
        if namespace is not None:
            self._namespace = namespace
        if handoff.should_return:
            return self
        if handoff.open_browser is not None:
            open_browser = handoff.open_browser
        # Under hot reload the persistent monitor process owns the tunnel, so this
        # worker must not open its own — but it's still publicly reachable
        # *through* that tunnel, so every public-exposure decision stays keyed on
        # `tunnel`, not `own_tunnel`. Outside hot reload they're identical.
        own_tunnel = tunnel and os.environ.get("_danvas_RELOAD_WORKER") != "1"
        # A tunnel publishes the loopback bind to the entire internet; treat it
        # as a public bind for exposure/telemetry decisions.
        # Resolve how far this serve reaches and the telemetry defaults (UI
        # Inspector + cursor reporting).
        exposure = self._resolve_exposure(host, tunnel, ui_inspector,
                                          ui_graveyard, cursors)
        self._public_bind = exposure.public_bind
        self._bridge._ui_inspector = exposure.ui_inspector
        self._bridge._ui_graveyard = exposure.ui_graveyard
        self._bridge._cursors = exposure.cursors
        # When a password is set, advertise it so the frontend offers a built-in
        # sign-out button (clears the session cookie via /__logout__, then the
        # login page reappears — letting a viewer switch accounts). No auth → no
        # button, nothing to sign out of.
        self._bridge._auth = bool(password or passwords)
        # Optional note rendered on the password page (e.g. which password each
        # kind of viewer should enter). Stored on the bridge; create_app reads it.
        self._bridge._login_message = login_message
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
        # Arm dispatch-history recording (canvas.trace_history): from now on every
        # handler dispatch is recorded into a bounded ring, so a trace panel opened
        # later shows what already happened. In the serving worker only (the
        # monitor returned above). Shallow unless deep tracing is also turned on.
        self._bridge._trace_recording = True
        # Start any registered background workers now -- we're in the serving
        # process (the hot-reload monitor returned above), so producer loops that
        # grab single-owner resources (a camera, a serial port) run here, never
        # in the monitor.
        self._start_background()
        # Wire persistence (serve(persist=...)): load the saved formation +
        # drawings now (before any client connects, so the seed replays on
        # connect) and arm the debounced autosave. In the serving worker only --
        # the hot-reload monitor returned above, so it never reads/writes the
        # file or races the worker for it.
        if persist:
            self._persist_setup(persist)
        # Native-window mode: default to on only inside a baked executable, so a
        # plain `python script.py` still opens the browser. Blocks on the webview
        # loop (main thread), so the non-blocking branch below is skipped.
        use_desktop = bool(getattr(sys, "frozen", False)) if desktop is None \
            else bool(desktop)
        # permessage-deflate is worth its CPU only on a bandwidth-constrained
        # public tunnel, not a fast local/LAN link (see server._ws_opts). Keyed
        # on `tunnel`, not `own_tunnel`: a hot-reload worker serves *through* the
        # monitor's tunnel, so it's still a tunneled (slow-link) bind.
        compress = tunnel
        if use_desktop:
            self._serve_desktop(port, host, own_tunnel, tunnel_provider,
                                window_title, window_size, password,
                                passwords=passwords, compress=compress)
            return self
        if not block:
            self._serve_background(port, open_browser, host, password,
                                   passwords, own_tunnel, tunnel_provider, wait,
                                   compress=compress)
            return self
        self._serve_blocking(port, open_browser, host, password, passwords,
                             own_tunnel, tunnel_provider, compress=compress)

    def _resolve_exposure(self, host, tunnel, ui_inspector, ui_graveyard, cursors):
        """Resolve how far this serve() reaches, plus the telemetry defaults.

        A pure function of the bind arguments (no side effects), so the gating
        truth-table is unit-testable in isolation. ``public_bind`` is whether
        browsers off this machine can reach the canvas (a tunnel, or a
        non-loopback host). The UI Inspector, graveyard, and cursor reporting
        all expose viewer state/telemetry to the host, so each defaults on
        *only* for a private, non-tunneled bind unless the caller forces it.
        """
        local = host in ("127.0.0.1", "localhost")
        default_private = local and not tunnel
        return _Exposure(
            public_bind=tunnel or not local,
            ui_inspector=bool(ui_inspector) if ui_inspector is not None
            else default_private,
            ui_graveyard=bool(ui_graveyard) if ui_graveyard is not None
            else default_private,
            cursors=bool(cursors) if cursors is not None else default_private,
        )

    def _maybe_handoff_reload(self, hot_reload, block, port, tunnel,
                              tunnel_provider, watch=None):
        """Handle the hot-reload pre-flight and monitor handoff for serve().

        Returns ``_ReloadHandoff(should_return, open_browser)``: when
        ``should_return`` is True serve() should return immediately (this call
        spawned the file-watching monitor, which owns the real serving). The
        ``open_browser`` field is ``False`` on a reload *restart* (reuse the
        existing tab) and ``None`` otherwise (leave serve()'s argument untouched).
        May call ``sys.exit`` for the reload import pre-flight, and validates that
        ``hot_reload`` is only used from a blocking script entry point.
        """
        if os.environ.get("_danvas_RELOAD_CHECK") == "1":
            # Hot-reload pre-flight (see hotreload.run_monitor): the monitor runs
            # the edited script in this mode to confirm it imports and runs before
            # tearing down the live worker. Reaching serve() means the module body
            # executed without error -- exit cleanly *without* binding a port or
            # starting threads, so the check never collides with the live worker.
            sys.exit(0)
        if not hot_reload:
            return _ReloadHandoff(False, None)
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
                "(`python yourscript.py`), not from an interactive session."
            )
        if os.environ.get("_danvas_RELOAD_WORKER") != "1":
            # Spawn a *clean* monitor subprocess that never ran user code, so
            # user-launched daemon threads (camera, sensor, etc.) don't leak into
            # the monitor and double-grab resources alongside the worker.
            # The original process stays alive blocking on the monitor so that
            # it remains the terminal's foreground job — Ctrl+C then reaches it
            # (and, via the shared console, the monitor and worker too) rather
            # than being swallowed by the shell after os._exit would have returned
            # the prompt with the server still running in the background.
            import secrets as _secrets, subprocess as _subprocess
            env = {**os.environ}
            env.setdefault("_danvas_RELOAD_SECRET", _secrets.token_urlsafe(32))
            # Extra files to watch (serve(watch=...)) ride in an env var as a JSON
            # list, so the monitor restarts the worker when any matching file
            # changes — e.g. a JSX/CSS panel loaded from disk via path=.
            if watch:
                import json as _json
                patterns = [watch] if isinstance(watch, str) else list(watch)
                env["_danvas_RELOAD_WATCH"] = _json.dumps(patterns)
            _mon = _subprocess.Popen(
                [sys.executable, "-m", "danvas._hotreload_monitor",
                 main_file, str(port),
                 str(int(bool(tunnel))),
                 str(tunnel_provider or "cloudflared")],
                env=env,
            )
            try:
                _mon.wait()
            except KeyboardInterrupt:
                # The monitor (and its worker) also received Ctrl+C from the
                # shared console group — give it a moment to handle its own
                # KeyboardInterrupt and run its finally/stop(proc) cleanup
                # before we reach for TerminateProcess (which on Windows is
                # unblockable and skips the monitor's finally block, orphaning
                # the uvicorn worker on the port).
                try:
                    _mon.wait(timeout=8)
                except _subprocess.TimeoutExpired:
                    _mon.terminate()
                    try:
                        _mon.wait(timeout=5)
                    except _subprocess.TimeoutExpired:
                        _mon.kill()
            sys.exit(0)
        if os.environ.get("_danvas_RELOAD_RESTART") == "1":
            # Already opened on first launch; a reload reuses the existing tab
            # (the frontend reconnects its websocket) instead of popping another.
            # Tell the reconnecting browser this is a fresh run so it drops the
            # previous run's panels (ids change each run) before this run's replay.
            self._bridge._reload = True
            return _ReloadHandoff(False, False)
        return _ReloadHandoff(False, None)

    def _serve_background(self, port, open_browser, host, password, passwords,
                          own_tunnel, tunnel_provider, wait, compress=False):
        """Start the server in a daemon thread and return (serve(block=False)).

        ``wait`` blocks briefly until the event loop is ready so the first
        post-serve insert is guaranteed to broadcast.
        """
        self._server = server.run_background(
            self._bridge, port=port, open_browser=open_browser, host=host,
            password=password, passwords=passwords, compress=compress,
        )
        if wait:
            self._wait_until_ready()
        self._serving = True
        if own_tunnel:
            self._start_tunnel(port, tunnel_provider)

    def _serve_blocking(self, port, open_browser, host, password, passwords,
                        own_tunnel, tunnel_provider, compress=False):
        """Run the server on this thread until shutdown (serve(block=True))."""
        self._serving = True
        if own_tunnel:
            self._start_tunnel(port, tunnel_provider)
        try:
            server.run(self._bridge, port=port, open_browser=open_browser,
                       host=host, password=password, passwords=passwords,
                       compress=compress)
        finally:
            self._persist_flush()  # capture final state (no-op when persist off)
            self._stop_tunnel()

    def _serve_desktop(self, port, host, tunnel, tunnel_provider, title, size,
                       password=None, passwords=None, compress=False):
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
                "Install the desktop extra: pip install 'danvas[desktop]'"
            )
            self._serving = True
            if tunnel:
                self._start_tunnel(port, tunnel_provider)
            try:
                server.run(self._bridge, port=port, open_browser=True, host=host,
                           password=password, passwords=passwords,
                           compress=compress)
            finally:
                self._stop_tunnel()
            return
        # Start the server in the background, then drive the window on the main
        # thread (pywebview requires that). webview.start() blocks until the
        # window closes; tear the server down afterwards.
        self._server = server.run_background(
            self._bridge, port=port, open_browser=False, host=host,
            password=password, passwords=passwords, compress=compress,
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

    def bake(self, name="danvas", *, icon=None, onefile=True, windowed=True,
             distpath="dist", entry=None, exclude=None, include=None,
             window_size=(1200, 800), port=8000):
        """Package this canvas's script into a standalone desktop app.

        Run normally (``python your_script.py``), ``bake`` builds a single
        self-contained executable from that script with PyInstaller — bundling
        Python, the danvas backend, and the pre-built frontend — and returns
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
        desktop extra: ``pip install 'danvas[desktop]'``. To build without
        editing your script, the equivalent CLI is
        ``python -m danvas.bake your_script.py``.
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
                    "`python -m danvas.bake your_script.py`"
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
        print(f"danvas baked: {out}")
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
        print(f"danvas public URL: {self._tunnel.url}"
              "   <- share this with anyone, anywhere")

    def _stop_tunnel(self):
        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel = None

    def stop(self):
        """Signal the background server to shut down and close any tunnel."""
        # Flush the final canvas state before tearing down -- in background /
        # desktop mode this is the controlled-closure path (no blocking
        # serve() finally runs). A no-op when persistence is off.
        self._persist_flush()
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