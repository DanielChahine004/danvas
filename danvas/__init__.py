"""danvas: a browser-based spatial canvas driven entirely from Python."""

from .autopanel import autopanel
from .canvas import Canvas
from .components import (
    AudioFeed,
    BaseComponent,
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
    Slider,
    Table,
    TextField,
    Toggle,
    Upload,
    UploadedFile,
    VideoFeed,
    WebView,
)
from .dispatch import panel_for
from .shapes import (
    BaseShape, DrawingShape,
    Geo, Text, Note, Draw, Highlight, Line, Frame,
)

# Single source of truth is the installed package metadata (pyproject `version`).
# Fall back to 0.0.0 when running straight from a source tree that was never
# installed, so the import never fails just to read a version string.
try:
    from importlib.metadata import PackageNotFoundError, version as _version

    __version__ = _version("danvas")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "Canvas",
    "autopanel",
    "panel_for",
    "Merge",
    "SourceClient",
    "Slider",
    "Button",
    "Label",
    "VideoFeed",
    "AudioFeed",
    "Chat",
    "Custom",
    "Download",
    "FileBrowser",
    "React",
    "Markdown",
    "Image",
    "Table",
    "TextField",
    "Toggle",
    "Plot",
    "LivePlot",
    "Histogram",
    "Inspector",
    "Upload",
    "UploadedFile",
    "WebView",
    "BaseComponent",
    "BaseShape",
    "DrawingShape",
    "Geo",
    "Text",
    "Note",
    "Draw",
    "Highlight",
    "Line",
    "Frame",
]


def __getattr__(name):
    # Lazily expose ``danvas.Merge`` / ``danvas.SourceClient`` so importing the
    # package doesn't pull in the websocket *client* stack (only the merge
    # aggregator and the dial-in source client need it).
    if name == "Merge":
        from .merge import Merge
        return Merge
    if name == "SourceClient":
        from .source import SourceClient
        return SourceClient
    if name == "connect":
        from .remote import connect
        return connect
    if name == "RemoteCanvas":
        from .remote import RemoteCanvas
        return RemoteCanvas
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
