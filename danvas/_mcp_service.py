"""Stateful Danvas adapter, independent of the MCP transport.

Only canvases created by this service are managed. All mutations are serialized;
viewer callbacks use a separate lock so snapshot requests cannot deadlock them.
"""
from __future__ import annotations

import copy
import json
import math
import re
import socket
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from . import Canvas


class Layout(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float | None = None
    y: float | None = None
    w: float | None = Field(default=None, gt=0)
    h: float | None = Field(default=None, gt=0)
    rotation: float | None = None


Kind = Literal["label", "markdown", "table", "plot", "slider", "toggle",
               "button", "text_field", "image", "react", "custom"]
OPTIONS = {
    "label": {"value", "label"},
    "markdown": {"text", "label"},
    "table": {"data", "label"},
    "plot": {"figure", "label"},
    "slider": {"min", "max", "default", "step", "on_release", "label"},
    "toggle": {"options", "default", "label"},
    "button": {"text", "label"},
    "text_field": {"placeholder", "default", "multiline", "label"},
    "image": {"src", "fit", "label"},
    "react": {"source", "jsx", "css", "props", "label"},
    "custom": {"html", "css", "js", "label"},
}
EXAMPLES: dict[str, dict[str, Any]] = {
    "label": {"value": "Ready"}, "markdown": {"text": "# Hello"},
    "table": {"data": [{"name": "Example", "value": 42}]},
    "plot": {"figure": {"data": [{"x": [1, 2], "y": [2, 4]}], "layout": {}}},
    "slider": {"min": 0, "max": 100, "default": 50},
    "toggle": {"options": ["On", "Off"], "default": "On"},
    "button": {"text": "Run"}, "text_field": {"placeholder": "Your answer"},
    "image": {"src": "https://example.com/image.png"},
    "react": {"jsx": "<button onClick={() => canvas.send({clicked:true})}>Run</button>"},
    "custom": {"html": "<h2>Hello</h2>"},
}


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Names must start with a letter and contain 1-64 letters, digits, _ or -")
    return value


def json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


class PanelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Kind
    options: dict[str, JsonValue] = Field(default_factory=dict)
    layout: Layout = Field(default_factory=Layout)


class ShapeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["geo", "text", "note", "frame", "line", "draw", "highlight"]
    options: dict[str, JsonValue] = Field(default_factory=dict)


SHAPE_KEYS = {"x", "y", "w", "h", "geo", "text", "label", "color", "fill",
              "dash", "size", "font", "align", "verticalAlign", "points", "spline",
              "rotation", "opacity"}


class Board:
    def __init__(self, name: str):
        self.name = name
        # Danvas's shipped stub omits shapes and several current live APIs.
        self.canvas: Any = Canvas()
        self.url: str | None = None
        self.specs: dict[str, PanelSpec] = {}
        self.shapes: dict[str, ShapeSpec] = {}
        self.arrows: dict[str, dict[str, str]] = {}
        self.updates: dict[str, Any] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=500)
        self.sequence = 0
        self.event_lock = threading.Lock()
        self.view: dict[str, Any] = {}

    def event(self, kind: str, name: str, value: Any) -> None:
        with self.event_lock:
            self.sequence += 1
            self.events.append({"sequence": self.sequence, "time": time.time(),
                                "type": kind, "name": name, "value": json_copy(value)})

    def unused(self, name: str) -> None:
        identifier(name)
        if name in self.specs or name in self.shapes or name in self.arrows:
            raise ValueError(f"Name already exists: {name}. Update or remove it first.")


class CanvasService:
    def __init__(self, workspace: Path):
        self.workspace = workspace.expanduser().resolve()
        self.boards: dict[str, Board] = {}
        self.lock = threading.RLock()

    def board(self, name: str) -> Board:
        try:
            return self.boards[name]
        except KeyError:
            raise ValueError(f"Unknown canvas: {name}") from None

    def catalog(self) -> dict[str, Any]:
        return {kind: {"options": sorted(OPTIONS[kind]), "example": EXAMPLES[kind],
                       "update": "props object" if kind == "react" else
                                 "object with html/css/js" if kind == "custom" else
                                 "Plotly figure object" if kind == "plot" else
                                 "new content/value (same type as initial content)"}
                for kind in OPTIONS}

    def create(self, name: str) -> dict[str, Any]:
        identifier(name)
        if name in self.boards:
            raise ValueError(f"Canvas already exists: {name}")
        if len(self.boards) >= 16:
            raise ValueError("Close a canvas first; this server allows 16 open canvases")
        board = Board(name)
        self._serve(board)
        self.boards[name] = board
        return self.describe(name)

    @staticmethod
    def _serve(board: Board) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        try:
            board.canvas.serve(port=port, open_browser=False, block=False,
                               host="127.0.0.1", ui_inspector=False,
                               ui_hosting=False, namespace={})
            board.url = f"http://127.0.0.1:{port}"
        except BaseException:
            board.canvas.stop()
            raise

    def close(self, name: str) -> dict[str, Any]:
        board = self.board(name)
        board.canvas.stop()
        del self.boards[name]
        return {"closed": name}

    def close_all(self) -> None:
        with self.lock:
            for name in list(self.boards):
                self.close(name)

    def describe(self, name: str) -> dict[str, Any]:
        b = self.board(name)
        panels = []
        for spec in b.specs.values():
            p = b.canvas[spec.name]
            panels.append({"name": spec.name, "kind": spec.kind, "value": json_copy(p.value),
                           "layout": {k: getattr(p, k) for k in ("x", "y", "w", "h", "rotation")},
                           "visible": p.visible, "options": json_copy(spec.options),
                           "last_update": json_copy(b.updates.get(spec.name))})
        return {"name": name, "url": b.url, "panels": panels,
                "shapes": [b.canvas[n].register_message() for n in b.shapes],
                "arrows": list(b.arrows.values()), "viewers": b.canvas.viewers}

    def add_panel(self, canvas: str, spec: PanelSpec) -> dict[str, Any]:
        b = self.board(canvas)
        b.unused(spec.name)
        options = json_copy(spec.options)
        unknown = set(options) - OPTIONS[spec.kind]
        if unknown:
            raise ValueError(f"Unsupported {spec.kind} options: {sorted(unknown)}. See panel_catalog.")
        if spec.kind == "slider":
            lo, hi, step = options.get("min", 0), options.get("max", 100), options.get("step", 1)
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi, step)):
                raise ValueError("Slider min/max/step must be numbers")
            if lo >= hi or step <= 0:
                raise ValueError("Slider requires min < max and step > 0")
            default = options.get("default")
            if default is not None and (not isinstance(default, (int, float)) or not lo <= default <= hi):
                raise ValueError("Slider default must be within min/max")
        if spec.kind == "toggle":
            choices = options.get("options")
            if not isinstance(choices, list) or not choices or not all(isinstance(v, str) for v in choices):
                raise ValueError("Toggle options must be a non-empty list of strings")
            if options.get("default") is not None and options["default"] not in choices:
                raise ValueError("Toggle default must be one of its options")
        if spec.kind == "image":
            self._image(options.get("src"))
        figure = options.pop("figure", None) if spec.kind == "plot" else None
        if figure is not None:
            self._figure(figure)
        # Constructors validate before insert, so invalid options leave no ghost panel.
        panel = getattr(b.canvas, spec.kind)(name=spec.name, **options,
                                             **spec.layout.model_dump(exclude_none=True))
        if figure is not None:
            panel.update(figure)
        b.specs[spec.name] = spec.model_copy(deep=True)
        subscribe = panel.on_message if spec.kind in ("react", "custom") else panel.on_change
        subscribe(lambda value: b.event("input", spec.name, value))
        return {"name": spec.name, "kind": spec.kind, "id": panel.id}

    @staticmethod
    def _image(value: Any) -> None:
        if not isinstance(value, str) or not value.startswith(("https://", "http://", "data:image/")):
            raise ValueError("Images require an http(s) URL or data:image URI")

    @staticmethod
    def _figure(value: Any) -> None:
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            raise TypeError("Plot requires a Plotly figure object with a data list")

    def update_panel(self, canvas: str, name: str, value: JsonValue) -> dict[str, Any]:
        b = self.board(canvas)
        if name not in b.specs:
            raise ValueError(f"Unknown panel: {name}")
        kind, p = b.specs[name].kind, b.canvas[name]
        value = json_copy(value)
        if kind == "slider" and (not isinstance(value, (float, int)) or isinstance(value, bool)
                                  or not p.min <= value <= p.max):
            raise ValueError("Slider value must be a number within min/max")
        if kind == "toggle":
            choices = b.specs[name].options["options"]
            if not isinstance(choices, list) or value not in choices:
                raise ValueError("Toggle value must be one of its options")
        if kind in ("markdown", "button", "text_field") and not isinstance(value, str):
            raise ValueError(f"{kind} value must be a string")
        if kind == "image":
            self._image(value)
        if kind == "plot":
            self._figure(value)
        if kind in ("react", "custom"):
            if not isinstance(value, dict):
                raise ValueError(f"{kind} update requires an object")
            if kind == "react" and {"roles", "client_id"} & value.keys():
                raise ValueError("MCP React updates apply to all viewers; roles/client_id are reserved")
            if kind == "custom" and set(value) - {"html", "css", "js"}:
                raise ValueError("Custom updates accept only html, css, js")
            p.update(**value)
            value = {**b.updates.get(name, {}), **value}
        else:
            p.update(value)
        b.updates[name] = value
        return {"updated": name}

    def layout(self, canvas: str, name: str, layout: Layout) -> dict[str, Any]:
        b = self.board(canvas)
        if name not in b.specs:
            raise ValueError(f"Unknown panel: {name}")
        b.canvas[name].set_layout(**layout.model_dump(exclude_none=True))
        return {"moved": name}

    def visibility(self, canvas: str, name: str, visible: bool) -> dict[str, Any]:
        b = self.board(canvas)
        if name not in b.specs:
            raise ValueError(f"Unknown panel: {name}")
        (b.canvas.unhide if visible else b.canvas.hide)(b.canvas[name])
        return {"name": name, "visible": visible}

    @staticmethod
    def _validate_shape(options: dict[str, Any]) -> None:
        if set(options) - SHAPE_KEYS:
            raise ValueError("Unsupported shape properties")
        for key in ("x", "y", "w", "h", "rotation", "opacity"):
            if key in options:
                value = options[key]
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                    raise ValueError(f"Shape {key} must be a finite number")
                if key in ("w", "h") and value <= 0:
                    raise ValueError("Shape width and height must be positive")
                if key == "opacity" and not 0 <= value <= 1:
                    raise ValueError("Shape opacity must be between 0 and 1")
        for key in ("text", "label", "geo", "color", "fill", "dash", "size", "font", "align", "spline"):
            if key in options and not isinstance(options[key], str):
                raise ValueError(f"Shape {key} must be a string")
        if "points" in options:
            points = options["points"]
            if not isinstance(points, list) or len(points) < 2:
                raise ValueError("Paths require at least two [x,y] points")
            if any(not isinstance(p, list) or len(p) != 2 or
                   any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in p)
                   for p in points):
                raise ValueError("Path points must be finite [x,y] pairs")

    def add_shape(self, canvas: str, spec: ShapeSpec) -> dict[str, Any]:
        b = self.board(canvas)
        b.unused(spec.name)
        opts = json_copy(spec.options)
        self._validate_shape(opts)
        shape = getattr(b.canvas, spec.kind)(name=spec.name, **opts)
        b.shapes[spec.name] = spec.model_copy(deep=True)
        return {"name": spec.name, "id": shape.id}

    def update_shape(self, canvas: str, name: str, properties: dict[str, JsonValue]) -> dict[str, Any]:
        b = self.board(canvas)
        if name not in b.shapes:
            raise ValueError(f"Unknown shape: {name}")
        self._validate_shape(properties)
        if "points" in properties:
            raise ValueError("To change a path's points, remove it and add the replacement")
        props = json_copy(properties)
        b.canvas[name].update(**props)
        b.shapes[name].options.update(props)
        return {"updated": name}

    def connect(self, canvas: str, name: str, start: str, end: str, text: str) -> dict[str, Any]:
        b = self.board(canvas)
        b.unused(name)
        for endpoint in (start, end):
            if endpoint not in b.specs and endpoint not in b.shapes:
                raise ValueError(f"Unknown endpoint: {endpoint}")
        b.canvas.connect(b.canvas[start], b.canvas[end], name=name, text=text)
        b.arrows[name] = {"name": name, "start": start, "end": end, "text": text}
        return b.arrows[name]

    def remove(self, canvas: str, name: str) -> dict[str, Any]:
        b = self.board(canvas)
        for arrow in list(b.arrows.values()):
            if name in (arrow["start"], arrow["end"]) and name != arrow["name"]:
                self.remove(canvas, arrow["name"])
        if name in b.arrows:
            b.canvas.disconnect(b.canvas[name])
            del b.arrows[name]
        elif name in b.specs:
            b.canvas.remove(b.canvas[name])
            del b.specs[name]
            b.updates.pop(name, None)
        elif name in b.shapes:
            b.canvas.remove_shape(b.canvas[name])
            del b.shapes[name]
        else:
            raise ValueError(f"Unknown object: {name}")
        return {"removed": name}

    def events(self, canvas: str, after: int = 0) -> dict[str, Any]:
        b = self.board(canvas)
        with b.event_lock:
            rows = list(b.events)
            return {"events": [e for e in rows if e["sequence"] > after],
                    "latest": b.sequence, "gap": bool(rows and after < rows[0]["sequence"] - 1)}

    def set_view(self, canvas: str, x: float, y: float, zoom: float) -> dict[str, Any]:
        b = self.board(canvas)
        if not all(math.isfinite(v) for v in (x, y, zoom)):
            raise ValueError("Camera values must be finite numbers")
        if not 0.05 <= zoom <= 8:
            raise ValueError("Zoom must be between 0.05 and 8")
        b.view = {"x": x, "y": y, "zoom": zoom}
        b.canvas.set_view(**b.view)
        return b.view

    def screenshot(self, canvas: str, name: str | None = None) -> bytes:
        b = self.board(canvas)
        if name is not None and name not in b.specs:
            raise ValueError(f"Unknown panel: {name}")
        return b.canvas.screenshot(target=b.canvas[name] if name else None, timeout=10)

    def _path(self, filename: str) -> Path:
        path = (self.workspace / filename).resolve()
        if not path.is_relative_to(self.workspace) or path.suffix != ".json":
            raise ValueError("Board files must be .json files inside the configured workspace")
        return path

    def save(self, canvas: str, filename: str, overwrite: bool = False) -> dict[str, Any]:
        b = self.board(canvas)
        path = self._path(filename)
        panels = []
        for spec in b.specs.values():
            item = spec.model_dump()
            p = b.canvas[spec.name]
            item["layout"] = {k: getattr(p, k) for k in ("x", "y", "w", "h", "rotation")}
            panels.append(item)
        updates = copy.deepcopy(b.updates)
        for name, spec in b.specs.items():
            if spec.kind in ("slider", "toggle", "text_field", "table"):
                updates[name] = b.canvas[name].value
        data: dict[str, Any] = {"version": 1, "panels": panels,
                "shapes": [s.model_dump() for s in b.shapes.values()],
                "arrows": list(b.arrows.values()), "updates": updates,
                "hidden": [n for n in b.specs if not b.canvas[n].visible], "view": b.view}
        # Save managed shape positions after browser drags as well as tool updates.
        for spec in data["shapes"]:
            shape = b.canvas[spec["name"]]
            spec["options"].update(x=shape.x, y=shape.y, rotation=shape.rotation)
            props = shape.register_message()["props"]
            for dimension in ("w", "h"):
                if dimension in props:
                    spec["options"][dimension] = props[dimension]
        encoded = json.dumps(data, indent=2, allow_nan=False)
        if len(encoded.encode("utf-8")) > 10_000_000:
            raise ValueError("Board file exceeds 10 MB; reduce panel content before saving")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w" if overwrite else "x", encoding="utf-8") as f:
            f.write(encoded)
        return {"path": str(path), "panels": len(panels)}

    def load(self, name: str, filename: str) -> dict[str, Any]:
        identifier(name)
        if name in self.boards:
            raise ValueError(f"Canvas already exists: {name}")
        if len(self.boards) >= 16:
            raise ValueError("Close a canvas first; this server allows 16 open canvases")
        path = self._path(filename)
        if path.stat().st_size > 10_000_000:
            raise ValueError("Board file exceeds 10 MB")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Unsupported board format; expected version 1")
        for key, expected in (("panels", list), ("shapes", list), ("arrows", list),
                              ("updates", dict), ("hidden", list), ("view", dict)):
            if not isinstance(data.get(key), expected):
                raise TypeError(f"Board field {key} must be a {expected.__name__}")
        b = Board(name)
        self.boards[name] = b
        try:
            for spec in data["panels"]:
                self.add_panel(name, PanelSpec.model_validate(spec))
            for spec in data["shapes"]:
                self.add_shape(name, ShapeSpec.model_validate(spec))
            for arrow in data["arrows"]:
                self.connect(name, **arrow)
            for panel, value in data["updates"].items():
                self.update_panel(name, panel, value)
            for panel in data["hidden"]:
                self.visibility(name, panel, False)
            if data["view"]:
                self.set_view(name, **data["view"])
            self._serve(b)
        except BaseException:
            self.close(name)
            raise
        return self.describe(name)
