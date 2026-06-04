"""PyCanvas: a browser-based spatial canvas driven entirely from Python."""

from .canvas import Canvas
from .components import (
    BaseComponent,
    Custom,
    Inspector,
    Label,
    LivePlot,
    Plot,
    Repl,
    Slider,
    Toggle,
    VideoFeed,
)

__version__ = "0.1.0"

__all__ = [
    "Canvas",
    "Slider",
    "Label",
    "VideoFeed",
    "Custom",
    "Toggle",
    "Plot",
    "LivePlot",
    "Repl",
    "Inspector",
    "BaseComponent",
]
