"""PyCanvas: a browser-based spatial canvas driven entirely from Python."""

from .canvas import Canvas
from .components import (
    BaseComponent,
    Custom,
    Label,
    LivePlot,
    Plot,
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
    "BaseComponent",
]
