"""panel.handlers / canvas.events: read back what's wired to trigger what.

One read-only property with one shape everywhere: ``{trigger:
[HandlerInfo]}`` — the original function, a file:line-qualified name, and
the dispatch mode — across base components, router panels (Custom/React),
the specialty stores (Table select/edit, Upload, FileBrowser, Download
provide), and the canvas-level emit channels.
"""

import danvas
from danvas.components.base import HandlerInfo


def test_slider_change_handler_with_dispatch_mode():
    canvas = danvas.Canvas()
    s = canvas.slider("r")

    @s.on_change(dedicated=True, queue="latest")
    def update_geometry(v):
        pass

    (info,) = s.handlers["change"]
    assert isinstance(info, HandlerInfo)
    assert info.fn is update_geometry
    assert info.mode == "dedicated" and info.queue == "latest"
    assert "update_geometry" in info.name and ":" in info.name  # file:line


def test_button_reports_click_not_change():
    canvas = danvas.Canvas()
    b = canvas.button("go")
    b.on_click(lambda: None)
    assert list(b.handlers) == ["click"]


def test_router_panel_reports_all_stores():
    canvas = danvas.Canvas()
    p = canvas.custom(name="p", html="<b>x</b>")
    p.on("tick")(lambda m: None)
    p.on_message(lambda m: None)
    p.on_binary(threaded=True)(lambda d: None)
    p.on_request("validate")(lambda r: 1)

    h = p.handlers
    assert set(h) == {"on:tick", "message", "binary", "request:validate"}
    assert h["binary"][0].mode == "threaded"


def test_table_specialty_stores():
    canvas = danvas.Canvas()
    t = canvas.table({"A": [1]})
    t.on_select(lambda ix: None)
    t.on_edit(lambda r, c, v: None)
    assert "select" in t.handlers and "edit" in t.handlers


def test_unwired_panel_reads_empty():
    canvas = danvas.Canvas()
    assert canvas.label("l", "x").handlers == {}


def test_canvas_events_mirrors_on_event():
    canvas = danvas.Canvas()

    @canvas.on_event("part-dropped")
    def dropped(path):
        pass

    (info,) = canvas.events["part-dropped"]
    assert info.fn is dropped and info.mode == "inline"
    assert canvas.events.keys() == {"part-dropped"}


def test_handlers_fire_order_matches_report_order():
    canvas = danvas.Canvas()
    s = canvas.slider("r")
    s.on_change(lambda v: None)

    @s.on_change
    def second(v):
        pass

    infos = s.handlers["change"]
    assert len(infos) == 2 and infos[1].fn is second
