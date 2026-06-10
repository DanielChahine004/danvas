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
    Inspector,
    Label,
    LivePlot,
    Plot,
    React,
    Repl,
    Slider,
    Toggle,
    VideoFeed,
    WebView,
)

__version__ = "0.1.0"

__all__ = [
    "Canvas",
    "autopanel",
    "Merge",
    "Slider",
    "Button",
    "Label",
    "VideoFeed",
    "AudioFeed",
    "Chat",
    "Custom",
    "FileBrowser",
    "Toggle",
    "Plot",
    "LivePlot",
    "React",
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
