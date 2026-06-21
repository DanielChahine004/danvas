"""Single source of truth for a panel's lock / chrome flags.

Every panel shares the same set of boolean flags that gate how a *viewer* may
interact with it (drag, resize, operate its controls, select it) and whether it
draws its card chrome. Each flag is described once here as a :class:`Flag` and
the same table drives:

- the read/write properties and ``set_layout`` on
  :class:`~danvas.components.base.BaseComponent`,
- the ``register`` message built in :mod:`danvas.bridge`,
- ``Canvas.insert`` and the save/load formation in :mod:`danvas.canvas`.

So adding a new flag is a single edit here rather than a change threaded through
seven places. ``wire`` is the key sent to the browser; it is deliberately kept
distinct from the Python name (e.g. ``draggable`` → ``movable``) and **must not**
change without updating the frontend's ``lockMeta`` handling. ``default`` is both
the initial value and the "don't bother sending it" baseline in the register
message.
"""

from collections import namedtuple

from ._protocol import FLAG_WIRE_KEYS

Flag = namedtuple("Flag", "wire attr default doc")


# Python name -> Flag(wire key, backing attribute, default, property docstring).
# Insertion order is the canonical order used wherever the flags are iterated.
# The ``wire`` keys come from the canonical danvas/_protocol.py (the same
# definition the frontend's protocol.generated.js is rendered from) so the
# browser-facing names can't drift; this module owns the backing attr / default /
# docstring.
LAYOUT_FLAGS = {
    "locked": Flag(
        FLAG_WIRE_KEYS["locked"], "_locked", False,
        "Whether the panel is fully locked (no move/resize/interaction)."),
    "draggable": Flag(
        FLAG_WIRE_KEYS["draggable"], "_draggable", True,
        "Whether the user can drag the panel. Control interaction is "
        "unaffected."),
    "resizable": Flag(
        FLAG_WIRE_KEYS["resizable"], "_resizable", True,
        "Whether the user can resize the panel. Interaction is unaffected."),
    "operable": Flag(
        FLAG_WIRE_KEYS["operable"], "_operable", True,
        "Whether the user can operate the panel's controls from the UI.\n\n"
        "        Set to ``False`` to make the controls inert to the user while "
        "the panel\n        stays unlocked, so Python ``update()`` calls still "
        "render live (e.g. a\n        slider thumb that tracks an automatic "
        "value the user mustn't drag). The\n        panel can still be "
        "moved/selected; use ``locked`` to freeze everything.\n        "),
    "grabbable": Flag(
        FLAG_WIRE_KEYS["grabbable"], "_grabbable", True,
        "Whether the user can grab/select this panel at all.\n\n"
        "        Content-heavy panels (Custom, React, WebView, plots…) normally "
        "need a\n        first click to select the panel before their content "
        "becomes\n        interactive. Set to ``False`` to drop that cover "
        "*and* make the panel\n        invisible to selection: the content is "
        "live (hover and clicks work)\n        from the start, and no click, "
        "marquee, or select-all ever highlights\n        or selects the panel — "
        "only the widget itself reacts. The trade-off is\n        that the user "
        "can't move or resize it; do that from Python (or flip\n        "
        "``grabbable`` back on).\n        "),
    "frame": Flag(
        FLAG_WIRE_KEYS["frame"], "_frame", True,
        "Whether the panel draws its rectangular card chrome.\n\n"
        "        Set to ``False`` to strip the card entirely — background, "
        "border,\n        shadow, padding, label header, and the "
        "hover/selection highlight\n        rectangle — so the component's "
        "content appears to float directly on\n        the canvas. The panel "
        "still occupies its w×h box and can be moved or\n        resized as "
        "usual (marquee select still works). Pair with\n        "
        "``grabbable=False`` if clicks on the content should never select it.\n"
        "        "),
}