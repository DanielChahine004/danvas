"""canvas.emit / canvas.on_event: the backend's universal trigger.

The frontend already has one universal trigger: a panel calls
``canvas.send({...})`` and Python handlers fire. This is the same idea
pointed the other way — *any* Python code (a watchdog thread, a timer, a
serial reader, an MQTT callback, another handler) calls ``canvas.emit(name,
data)`` and every ``@canvas.on_event(name)`` handler fires::

    @canvas.on_event("part-dropped")
    def _(path):
        viewer.update(path)

    # anywhere, any thread:
    canvas.emit("part-dropped", "drop/bracket.glb")

Emits are funnelled through the same shared dispatch thread as browser
input, so an inline handler never races an ``on_change`` — an emit is an
event like any other. And because each event name's handler list is a
headless peer of a panel's (it *borrows* the component dispatch machinery
wholesale), handlers get the identical feature set: ``threaded=True``,
``dedicated=True`` with ``queue="fifo"/"latest"`` backpressure, ``async
def`` support, an optional trailing ``viewer`` argument (``{}`` for
backend emits), and visibility in the Inspector's dispatch trace.
"""

from .components.base import BaseComponent


class EventChannel:
    """The handler list for one event name — a headless peer of a panel.

    Borrows :class:`BaseComponent`'s registration/dispatch methods verbatim
    rather than reimplementing the threaded/dedicated/queue/async branching;
    the attributes below are exactly the state those methods touch.
    """

    _register_callback = BaseComponent._register_callback
    _dispatch_callbacks = BaseComponent._dispatch_callbacks
    _accepts_viewer = staticmethod(BaseComponent._accepts_viewer)
    _traced = staticmethod(BaseComponent._traced)

    def __init__(self, canvas, name):
        self._canvas = canvas
        self.name = name              # labels dispatch-trace rows
        self._callbacks = []
        self._dedicated_kernels = {}

    @property
    def _bridge(self):
        return self._canvas._bridge
