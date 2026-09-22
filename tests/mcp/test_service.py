import json

import pytest

from danvas._mcp_service import (
    EXAMPLES,
    Board,
    CanvasService,
    Layout,
    PanelSpec,
    ShapeSpec,
)


@pytest.fixture
def service(tmp_path):
    s = CanvasService(tmp_path)
    s.boards["test"] = Board("test")
    yield s
    s.close_all()


@pytest.mark.parametrize("kind", EXAMPLES)
def test_every_panel_create_update_layout_visibility_remove(service, kind):
    result = service.add_panel("test", PanelSpec(name="panel", kind=kind, options=EXAMPLES[kind]))
    assert result["id"]
    values = {
        "label": "Changed", "markdown": "## Changed", "table": [{"x": 2}],
        "plot": {"data": [{"x": [3], "y": [4]}]}, "slider": 60, "toggle": "Off",
        "button": "Go", "text_field": "Hello", "image": "https://example.com/next.png",
        "react": {"title": "Changed"}, "custom": {"html": "<b>Changed</b>"},
    }
    service.update_panel("test", "panel", values[kind])
    service.layout("test", "panel", Layout(x=32, y=64, w=300, h=150))
    row = service.describe("test")["panels"][0]
    assert row["layout"]["x"] == 32
    assert row["layout"]["w"] == 300
    service.visibility("test", "panel", False)
    assert not service.describe("test")["panels"][0]["visible"]
    service.visibility("test", "panel", True)
    assert service.describe("test")["panels"][0]["visible"]
    service.remove("test", "panel")
    assert not service.describe("test")["panels"]


@pytest.mark.parametrize("kind,options", [
    ("label", {"path": "/etc/passwd"}), ("react", {"path": "/etc/passwd"}),
    ("slider", {"min": 2, "max": 1}), ("slider", {"step": 0}),
    ("slider", {"min": "oops"}), ("slider", {"default": 101}),
    ("toggle", {"options": []}), ("toggle", {"options": ["x"], "default": "y"}),
    ("image", {"src": "/etc/passwd"}), ("plot", {"figure": {}}),
])
def test_invalid_creation_does_not_mutate(service, kind, options):
    with pytest.raises((ValueError, TypeError)):
        service.add_panel("test", PanelSpec(name="bad", kind=kind, options=options))
    assert not service.board("test").canvas.components
    assert not service.describe("test")["panels"]


def test_name_uniqueness_and_validation(service):
    spec = PanelSpec(name="once", kind="label")
    service.add_panel("test", spec)
    with pytest.raises(ValueError, match="already exists"):
        service.add_panel("test", spec)
    with pytest.raises(ValueError, match="Names"):
        service.add_panel("test", PanelSpec(name="../bad", kind="label"))
    assert len(service.describe("test")["panels"]) == 1


@pytest.mark.parametrize("kind,options", [
    ("geo", {"text": "Box"}), ("text", {"text": "Words"}),
    ("note", {"text": "Note"}), ("frame", {"label": "Frame"}),
    ("line", {"points": [[0, 0], [100, 100]]}),
    ("draw", {"points": [[0, 0], [100, 100]]}),
    ("highlight", {"points": [[0, 0], [100, 100]]}),
])
def test_shapes_and_connections(service, kind, options):
    service.add_shape("test", ShapeSpec(name="shape", kind=kind, options=options))
    service.add_panel("test", PanelSpec(name="panel", kind="label"))
    service.connect("test", "edge", "shape", "panel", "flow")
    service.update_shape("test", "shape", {"x": 45, "color": "red"})
    assert service.describe("test")["shapes"][0]["x"] == 45
    assert len(service.describe("test")["arrows"]) == 1
    service.remove("test", "shape")
    assert not service.describe("test")["shapes"]
    assert not service.describe("test")["arrows"]
    assert len(service.describe("test")["panels"]) == 1


def test_events_cursor_and_overflow(service):
    b = service.board("test")
    for i in range(502):
        b.event("input", "button", i)
    events = service.events("test")
    assert len(events["events"]) == 500
    assert events["gap"]
    assert events["latest"] == 502
    assert len(service.events("test", after=501)["events"]) == 1
    assert not service.events("test", after=502)["events"]


def test_path_confinement_and_overwrite(service, tmp_path):
    service.save("test", "board.json")
    with pytest.raises(FileExistsError):
        service.save("test", "board.json")
    service.save("test", "board.json", overwrite=True)
    for path in ("../escape.json", "/tmp/escape.json", "file.txt"):
        with pytest.raises(ValueError, match="inside"):
            service.save("test", path)
    (tmp_path / "link").symlink_to(tmp_path.parent)
    with pytest.raises(ValueError, match="inside"):
        service.save("test", "link/escape.json")


def test_bad_load_rollback(service, tmp_path):
    service.save("test", "bad.json")
    path = tmp_path / "bad.json"
    data = json.loads(path.read_text())
    data["panels"] = [{"name": "good", "kind": "label"}, {"kind": "invalid"}]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        service.load("new", "bad.json")
    assert "new" not in service.boards
    assert "test" in service.boards


def test_invalid_updates_do_not_change_panel(service):
    service.add_panel("test", PanelSpec(name="slider", kind="slider", options={"default": 50}))
    for value in (-1, 101, "a", True, None):
        with pytest.raises(ValueError):
            service.update_panel("test", "slider", value)
    assert service.board("test").canvas["slider"].value == 50


@pytest.mark.parametrize("method,args", [
    ("update_panel", ("missing", "x")),
    ("layout", ("missing", Layout(x=1))),
    ("visibility", ("missing", False)),
    ("update_shape", ("missing", {})),
    ("connect", ("edge", "missing", "missing", "")),
    ("remove", ("missing",)),
    ("screenshot", ("missing",)),
])
def test_unknown_objects_are_errors(service, method, args):
    with pytest.raises(ValueError, match="Unknown"):
        getattr(service, method)("test", *args)


def test_screenshot_requires_browser(service):
    service.add_panel("test", PanelSpec(name="p", kind="label"))
    with pytest.raises(RuntimeError, match="browser"):
        service.screenshot("test")


@pytest.mark.parametrize("kind,value", [
    ("react", "bad"), ("react", {"roles": "admin"}),
    ("custom", {"path": "secret"}), ("markdown", 5),
    ("toggle", "absent"), ("plot", {}), ("image", "/etc/passwd"),
])
def test_bad_update_values(service, kind, value):
    service.add_panel("test", PanelSpec(name="panel", kind=kind, options=EXAMPLES[kind]))
    with pytest.raises((ValueError, TypeError)):
        service.update_panel("test", "panel", value)
    assert not service.board("test").updates


def test_bad_shapes_and_camera(service):
    with pytest.raises(ValueError):
        service.add_shape("test", ShapeSpec(name="bad", kind="geo", options={"arbitrary": True}))
    service.add_shape("test", ShapeSpec(name="shape", kind="geo"))
    with pytest.raises(ValueError):
        service.update_shape("test", "shape", {"arbitrary": True})
    for zoom in (0, 9, float("nan")):
        with pytest.raises(ValueError):
            service.set_view("test", 0, 0, zoom)


def test_bad_board_format_and_size(service, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"version":2}')
    with pytest.raises(ValueError, match="format"):
        service.load("new", "bad.json")
    path.write_text(' ' * 10_000_001)
    with pytest.raises(ValueError, match="10 MB"):
        service.load("new", "bad.json")
    with pytest.raises(ValueError, match="already exists"):
        service.load("test", "bad.json")
    with pytest.raises(ValueError, match="already exists"):
        service.create("test")
    assert list(service.boards) == ["test"]


@pytest.mark.parametrize("options", [{"w": -1}, {"h": "wide"}, {"opacity": 2},
                                     {"text": []}, {"points": [[0, 0]]},
                                     {"points": [[0, 0], [1, "bad"]]}])
def test_invalid_shape_data_is_rejected(service, options):
    with pytest.raises(ValueError):
        service.add_shape("test", ShapeSpec(name="shape", kind="geo", options=options))
    assert not service.board("test").shapes


def test_malformed_document_and_size_limit(service, tmp_path):
    (tmp_path / "incomplete.json").write_text('{"version":1}')
    with pytest.raises(TypeError, match="Board field"):
        service.load("new", "incomplete.json")
    service.add_panel("test", PanelSpec(name="large", kind="label", options={"value": "x" * 10_000_001}))
    with pytest.raises(ValueError, match="10 MB"):
        service.save("test", "large.json")
    assert not (tmp_path / "large.json").exists()


def test_shape_live_size_persists(service, tmp_path):
    service.add_shape("test", ShapeSpec(name="box", kind="geo"))
    service.board("test").canvas["box"].update(w=320, h=240)
    service.save("test", "resized.json")
    data = json.loads((tmp_path / "resized.json").read_text())
    assert data["shapes"][0]["options"]["w"] == 320
    assert data["shapes"][0]["options"]["h"] == 240
