"""Canvas: the public entry point. Holds components and serves the app."""

import time
import uuid

from . import server
from .bridge import Bridge


class Canvas:
    def __init__(self):
        self._bridge = Bridge()
        self._components = []
        self._named = {}  # name -> component, for canvas.<name> / canvas["<name>"]
        self._serving = False
        self._server = None

    def insert(self, component, x=None, y=None, w=None, h=None, rotation=None, name=None):
        """Register a component on the canvas and return it.

        ``x``/``y`` set the panel's position in canvas coordinates; omit them to
        let the frontend auto-cascade. ``w``/``h`` set its size in pixels;
        omit them to use the component's default size.

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
            self._named[name] = component
        if x is not None and y is not None:
            component._position = (x, y)
        if w is not None:
            component._props["w"] = w
        if h is not None:
            component._props["h"] = h
        if rotation is not None:
            component._rotation = rotation
        component_id = uuid.uuid4().hex
        component._bind(component_id, self._bridge)
        self._bridge.add_component(component)
        self._components.append(component)
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
        return component

    def __getattr__(self, name):
        # Only reached when normal attribute lookup fails. _named is set in
        # __init__, but guard against lookups during unpickling/early init.
        named = self.__dict__.get("_named", {})
        if name in named:
            return named[name]
        raise AttributeError(name)

    def __getitem__(self, name):
        return self._named[name]

    def serve(self, port=8000, open_browser=True):
        """Start the server, open the browser, and block until shutdown."""
        self._serving = True
        server.run(self._bridge, port=port, open_browser=open_browser)

    def serve_background(self, port=8000, open_browser=True, wait=True):
        """Start the server without blocking; return ``self`` for chaining.

        Intended for interactive sessions (e.g. Jupyter): the call returns so
        further ``insert`` calls push panels onto the live canvas. When
        ``wait`` is true, block briefly until the server's event loop is ready
        so the first post-serve insert is guaranteed to broadcast.
        """
        self._server = server.run_background(
            self._bridge, port=port, open_browser=open_browser
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
