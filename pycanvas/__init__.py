"""PyCanvas: a browser-based spatial canvas driven entirely from Python."""

from .autopanel import autopanel
from .canvas import Canvas
from .components import (
    AudioFeed,
    BaseComponent,
    Button,
    Chat,
    Custom,
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
    VideoFeed,
    WebView,
)
from .dispatch import panel_for

# Single source of truth is the installed package metadata (pyproject `version`).
# Fall back to 0.0.0 when running straight from a source tree that was never
# installed, so the import never fails just to read a version string.
try:
    from importlib.metadata import PackageNotFoundError, version as _version

    __version__ = _version("pycanvas")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "Canvas",
    "autopanel",
    "panel_for",
    "Merge",
    "Slider",
    "Button",
    "Label",
    "VideoFeed",
    "AudioFeed",
    "Chat",
    "Custom",
    "FileBrowser",
    "React",
    "Markdown",
    "Image",
    "Table",
    "Toggle",
    "Plot",
    "LivePlot",
    "Histogram",
    "Repl",
    "Inspector",
    "WebView",
    "BaseComponent",
]


def __getattr__(name):
    # Lazily expose ``pycanvas.Merge`` so importing the package doesn't pull in
    # the websocket *client* stack (only the merge aggregator needs it).
    if name == "Merge":
        from .merge import Merge
        return Merge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
