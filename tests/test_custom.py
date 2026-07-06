import danvas


class FakeBridge:
    def __init__(self):
        self.plain = []

    def broadcast(self, msg, exclude=None, **_kw):
        self.plain.append(msg)

    def broadcast_binary(self, data, **_kw):
        pass


def _panel():
    p = danvas.Custom(html="<div></div>", name="p")
    p._bind("c1", FakeBridge())
    return p


def test_on_routes_by_event_field():
    panel = _panel()
    seen = []
    panel.on("rotate")(lambda msg: seen.append(("rot", msg["deg"])))
    panel.on("reset")(lambda msg: seen.append(("rst", None)))

    panel._handle_input({"event": "rotate", "deg": 42})
    panel._handle_input({"event": "reset"})
    panel._handle_input({"event": "unhandled"})  # no handler -> ignored

    assert seen == [("rot", 42), ("rst", None)]


def test_on_message_is_catch_all_and_fires_alongside_keyed():
    panel = _panel()
    every, keyed = [], []
    panel.on_message(lambda msg: every.append(msg.get("event")))
    panel.on("rotate")(lambda msg: keyed.append(msg["deg"]))

    panel._handle_input({"event": "rotate", "deg": 7})
    panel._handle_input({"event": "other"})

    assert keyed == [7]                 # keyed handler only for its event
    assert every == ["rotate", "other"] # catch-all sees both


def test_custom_event_key_is_configurable():
    panel = danvas.Custom(html="", name="p", event_key="type")
    panel._bind("c1", FakeBridge())
    seen = []
    panel.on("ping")(lambda msg: seen.append(msg))
    panel._handle_input({"type": "ping", "n": 1})
    assert seen == [{"type": "ping", "n": 1}]


def _frontend_shim():
    # The in-iframe `canvas` helper moved to the frontend (customShim.ts,
    # injected by CustomView with the browser-local composed id); custom.py
    # keeps only the auto-fit script. The shim-surface tests read the real
    # injected source.
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "danvas", "frontend",
                        "src", "react", "customShim.ts")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_onpush_helper_is_injected_by_the_frontend():
    shim = _frontend_shim()
    # The symmetric helper exposes both directions in the iframe.
    assert "onPush:function" in shim
    assert "send:function" in shim
    # …and Python no longer bakes it (the frontend injects, marker-guarded).
    html = _panel().register_props()["html"]
    assert "window.canvas=" not in html


def test_push_streams_without_reload():
    panel = _panel()
    panel.push({"deg": 90})
    assert panel._bridge.plain[-1]["payload"] == {"post": {"deg": 90}}


# -- base reset is applied to html-only fragments (not just css/js panels) ---

def test_html_only_fragment_gets_base_reset():
    # An html-only fragment used to be returned raw, forcing callers to hand-write
    # their own <style> reset. It now gets the same base reset (and centring) that
    # css/js panels already get.
    p = danvas.Custom(html="<div>hi</div>", name="frag")
    doc = p._document()
    assert "box-sizing" in doc          # the reset is present
    assert "justify-content" in doc     # content is centred, like css/js panels


def test_full_document_is_left_untouched():
    page = "<!doctype html><html><body><h1>x</h1></body></html>"
    p = danvas.Custom(html=page, name="page")
    assert p._document() == page        # owns its own document; no reset injected


def test_css_js_path_still_composes_unchanged():
    p = danvas.Custom(html="<div>hi</div>", css="div{color:red}", name="styled")
    doc = p._document()
    assert "box-sizing" in doc and "color:red" in doc


# -- Custom <-> React shim parity (request/viewport/setView) -----------------

def test_custom_shim_exposes_react_parity_methods():
    shim = _frontend_shim()
    # The iframe `canvas` handle mirrors the React panel's: ask-owner +
    # camera-awareness, not just send/onPush.
    for token in ("request:function", "setView:function", "viewport:function",
                  "send:function", "onPush:function", "sendBinary:function"):
        assert token in shim, f"shim missing {token}"


def test_custom_answers_on_request_like_react():
    # on_request / _handle_request are shared via _EventRouter, so a Custom panel
    # resolves canvas.request the same way a React panel does.
    p = _panel()

    @p.on_request("double")
    def _(req):
        return {"doubled": req["n"] * 2}

    assert p._handle_request({"event": "double", "n": 21}) == {"doubled": 42}


def test_custom_on_request_catch_all_and_missing():
    import pytest
    p = _panel()

    @p.on_request()                       # catch-all
    def _(req):
        return req.get("n", 0) + 1

    assert p._handle_request({"n": 4}) == 5
    # No handler for a keyed event with no catch-all match still routes to the
    # catch-all here; remove it to prove the unhandled path raises.
    q = _panel()
    with pytest.raises(LookupError):
        q._handle_request({"event": "whatever"})


# -- themed=True: follow the canvas theme inside the sandboxed iframe ---------

def test_themed_panel_wires_prop_and_theme_listener():
    p = danvas.Custom(html="<div></div>", name="t", themed=True)
    props = p.register_props()
    assert props["themed"] is True
    # The listener that applies forwarded --pc-* vars lives in the frontend
    # shim now; Python's job is carrying the prop the frontend keys on.
    assert "__danvas_theme" in _frontend_shim()


def test_themed_defaults_off():
    p = danvas.Custom(html="<div></div>", name="t")
    assert p.register_props()["themed"] is False


def test_custom_shim_exposes_chat_parity():
    shim = _frontend_shim()
    assert "chat:{" in shim
    for action in ("'send'", "'setName'", "'history'", "'sub'", "'idsub'"):
        assert f"action:{action}" in shim, f"chat shim missing action {action}"
