"""Template smoke: mount every shipped panel in a REAL browser and look.

The other suites measure the wire; this one measures the renderer — the last
layer where a contract can silently break (a missing Plotly trace module, a
JSX/sucrase failure, a broken shim). A headless Chromium loads the real
danvasd-served frontend with every template from components.json registered
(sample data per its contract), then asserts:

- every registered panel mounted (the store holds them all);
- zero console/page errors;
- the Plotly panels actually rendered their figures, INCLUDING the histogram's
  heatmap trace (the plotly-basic regression this suite exists for);
- Custom iframes carry the frontend-injected `window.canvas=` shim;
- a `rel`-placed register landed below its anchor, and an owner-driven anchor
  height change re-settled the chain browser-side (PROTOCOL.md § relative
  placement).

Requires: playwright + chromium (pip install playwright; playwright install
chromium) and a built danvasd — both skipped cleanly when absent.
"""

import json
import os
import socket
import subprocess
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from danvas.source import SourceClient  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A 1x1 PNG for the image panel.
_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

_FIG_SCATTER = {"data": [{"x": [0, 1, 2], "y": [1, 3, 2], "type": "scatter",
                          "mode": "lines"}], "layout": {}}
_FIG_HEATMAP = {"data": [{"type": "heatmap", "x": [0, 1], "y": [0.5, 1.5],
                          "z": [[0.2, 0.8], [0.6, 0.4]], "showscale": False}],
                "layout": {}}


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _danvasd():
    exe = os.environ.get("DANVASD")
    if exe and os.path.isfile(exe):
        return exe
    name = "danvasd.exe" if os.name == "nt" else "danvasd"
    for rel in ("broker/target/release", "broker/target/debug"):
        p = os.path.join(_ROOT, rel, name)
        if os.path.isfile(p):
            return p
    return None


@pytest.fixture(scope="module")
def canvas_url():
    """danvasd + a source with every template registered; yields the URL."""
    binary = _danvasd()
    if binary is None:
        pytest.skip("danvasd not built")
    port = _free_port()
    broker = subprocess.Popen([binary, "--port", str(port)],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        broker.kill()
        raise RuntimeError("danvasd never opened its port")

    src = SourceClient(f"127.0.0.1:{port}", label="smoke")
    src.connect()
    y = [40]

    def place(cid, kind, **data):
        src.register_template(cid, kind, x=40, y=y[0], **data)
        y[0] += 240

    place("slider", "slider", min=0, max=10, value=3)
    place("label", "label", text="hello")
    place("button", "button", text="press")
    place("toggle", "toggle", options=["a", "b"], value="a")
    place("text_field", "text_field", placeholder="type")
    place("markdown", "markdown", html="<p><b>md</b></p>")
    place("video", "video")
    place("audio", "audio")
    place("table", "table", cols=["a", "b"], rows=[[1, 2]],
          numeric=[True, True])
    place("plot", "plot", _fig=_FIG_SCATTER)
    place("histogram", "histogram", _fig=_FIG_HEATMAP)
    place("live_plot", "live_plot")
    src.update("live_plot", plot=_FIG_SCATTER)
    place("image", "image", src=_PNG)
    place("webview", "webview", url="about:blank")
    place("download", "download", text="get")
    place("upload", "upload", text="drop")
    place("file_browser", "file_browser")
    src.update("file_browser", post={"cwd": "/", "atRoot": True,
                                     "selected": None,
                                     "entries": [{"name": "f", "dir": False,
                                                  "size": 1}]})
    place("inspector", "inspector")
    place("chat", "chat")
    # Custom rides props.html (no shim — the frontend must inject it).
    src.register("custom", "Custom",
                 props={"html": "<button>hi</button>", "w": 240, "h": 160},
                 x=40, y=y[0])
    y[0] += 240
    # The rel chain: an anchored label and one placed by the FRONTEND.
    src.register_template("anchor", "label", text="anchor", x=600, y=40)
    src.register_template("dep", "label", text="dep",
                          rel={"kind": "below", "anchor": "anchor", "gap": 16})

    try:
        yield f"http://127.0.0.1:{port}", src
    finally:
        src.close()
        broker.kill()
        try:
            broker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


N_PANELS = 22  # everything registered above (19 templates + custom + anchor + dep)


@pytest.fixture(scope="module")
def page_state(canvas_url):
    """One headless page over the canvas; yields (page, console errors)."""
    url, src = canvas_url
    with playwright_sync.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # browsers not installed
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        errors = []
        page.on("console",
                lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function(
            "() => window.__danvas && [...window.__danvas.store.ids()]"
            ".filter(i => (window.__danvas.store.peek(i)||{}).typeName"
            " === 'panel').length >= %d" % N_PANELS,
            timeout=30_000)
        # Let lazy chunks (plotly) and iframes settle.
        page.wait_for_selector(".js-plotly-plot", timeout=30_000)
        page.wait_for_timeout(1500)
        yield page, errors, src
        browser.close()


def _panels(page):
    return page.evaluate(
        "() => [...window.__danvas.store.ids()]"
        ".map(i => window.__danvas.store.peek(i))"
        ".filter(s => s && s.typeName === 'panel')"
        ".map(s => ({id: s.id, x: s.x, y: s.y, w: s.props.w, h: s.props.h,"
        " label: s.props.label}))")


def test_every_template_mounts(page_state):
    page, _errors, _src = page_state
    assert len(_panels(page)) == N_PANELS


def test_no_console_errors(page_state):
    _page, errors, _src = page_state
    real = [e for e in errors if "favicon" not in e.lower()]
    assert not real, f"console/page errors: {real[:5]}"


def test_plotly_figures_render_including_heatmap(page_state):
    page, _errors, _src = page_state
    types = page.evaluate(
        "() => [...document.querySelectorAll('.js-plotly-plot')]"
        ".flatMap(gd => (gd.data || []).map(t => t.type || 'scatter'))")
    assert "heatmap" in types, (
        f"the histogram's heatmap trace did not render (saw {types}) — is "
        "the bundled Plotly missing the trace module?")
    assert "scatter" in types
    assert len(types) >= 3  # plot + histogram + live_plot all drew


def test_custom_iframe_gets_the_injected_shim(page_state):
    page, _errors, _src = page_state
    docs = page.evaluate(
        "() => [...document.querySelectorAll('iframe[srcdoc]')]"
        ".map(f => f.getAttribute('srcdoc'))")
    assert any("window.canvas=" in (d or "") for d in docs), (
        "no Custom iframe carries the frontend-injected canvas shim")


def test_rel_places_and_cascades_browser_side(page_state):
    page, _errors, src = page_state

    def geom(label):
        for p in _panels(page):
            if p["label"] == label:
                return p
        raise AssertionError(f"panel {label!r} not found")

    a, b = geom("anchor"), geom("dep")
    assert abs(b["x"] - a["x"]) < 0.5
    assert abs(b["y"] - (a["y"] + a["h"] + 16)) < 0.5, (a, b)
    # The production cascade path: the anchor's CONTENT grows, its auto-fit
    # raises the panel height, and the chain re-settles browser-side — the
    # dep tracks anchor.y + anchor.h + gap through the change.
    src.update("anchor", post="a very long line of label text " * 12)
    page.wait_for_function(
        "(oldH) => {"
        " const ps = [...window.__danvas.store.ids()]"
        "   .map(i => window.__danvas.store.peek(i))"
        "   .filter(s => s && s.typeName === 'panel');"
        " const a = ps.find(s => s.props.label === 'anchor');"
        " const b = ps.find(s => s.props.label === 'dep');"
        " return a && b && a.props.h > oldH + 20"
        "   && Math.abs(b.y - (a.y + a.props.h + 16)) < 1;"
        "}",
        arg=a["h"], timeout=15_000)


def test_order_frame_restacks_shapes_and_arrows(page_state):
    # Shapes and arrows live in the browser's ONE z-order (fractional index
    # in the store), and the same `order` frame that restacks panels must
    # restack them -- the seam behind shape.to_front()/arrow.to_back().
    # (Record ids arrive tag-namespaced through the broker, so match by
    # suffix rather than exact id.)
    page, errors, src = page_state
    src._send({"type": "shape", "id": "zs1", "shapeType": "geo",
               "x": 900, "y": 400, "props": {"w": 120, "h": 80,
                                             "geo": "rectangle"}})
    src._send({"type": "shape", "id": "zs2", "shapeType": "geo",
               "x": 940, "y": 430, "props": {"w": 120, "h": 80,
                                             "geo": "rectangle"}})
    src._send({"type": "arrow", "id": "za", "start": "slider",
               "end": "label"})
    find = ("(sfx) => [...window.__danvas.store.ids()]"
            ".findIndex(i => i.endsWith(':' + sfx) || i === 'shape:' + sfx)")
    page.wait_for_function(
        "() => ['zs1','zs2','za'].every(sfx => (%s)(sfx) >= 0)" % find,
        timeout=10_000)

    src._send({"type": "order", "id": "zs1", "op": "front"})
    page.wait_for_function(          # zs1 rose above the later-created two
        "() => { const at = %s;"
        " return at('zs1') > at('zs2') && at('zs1') > at('za'); }" % find,
        timeout=10_000)

    src._send({"type": "order", "id": "za", "op": "back"})
    page.wait_for_function(          # the arrow sank to the very bottom
        "() => (%s)('za') === 0" % find,
        timeout=10_000)


def test_drawings_interleave_with_panels_in_dom(page_state):
    # Drawings/arrows and panels share ONE z-order, and the DOM renders it
    # faithfully: a shape sent to the back sits BELOW every panel div, an
    # arrow left on top sits ABOVE them, and one moved to just above a
    # given panel lands between two panels (user report: an arrow could
    # only be above ALL panels or below ALL of them, so a leader always
    # crossed an open note).
    page, errors, src = page_state
    src._send({"type": "shape", "id": "zi1", "shapeType": "geo",
               "x": 1200, "y": 400, "props": {"w": 60, "h": 40,
                                              "geo": "rectangle"}})
    src._send({"type": "arrow", "id": "zia", "start": "slider",
               "end": "label"})
    find = ("(sfx) => [...window.__danvas.store.ids()]"
            ".findIndex(i => i.endsWith(':' + sfx) || i === 'shape:' + sfx)")
    page.wait_for_function(
        "() => ['zi1','zia'].every(sfx => (%s)(sfx) >= 0)" % find,
        timeout=10_000)
    src._send({"type": "order", "id": "zi1", "op": "back"})
    page.wait_for_function("() => (%s)('zi1') === 0" % find, timeout=10_000)

    # DOM position of a record relative to the panel divs: -1 below all,
    # +1 above all, else the number of panels it sits above.
    dom_rank = """(sfx) => {
      const layer = document.querySelector('[data-pc-camera-layer]');
      const kids = [...layer.children];
      const node = layer.querySelector(
        '[data-pc-drawing-id$=":' + sfx + '"], [data-pc-drawing-id="shape:' + sfx + '"]');
      if (!node) return null;
      const svg = node.closest('svg');
      const at = kids.indexOf(svg);
      const panels = kids.map((k, i) => [k, i]).filter(([k]) => k.hasAttribute('data-pc-panel-id'));
      const above = panels.filter(([, i]) => i < at).length;
      if (above === 0) return -1;
      if (above === panels.length) return panels.length;
      return above;
    }"""
    page.wait_for_function(
        "() => (%s)('zi1') === -1 && (%s)('zia') === "
        "document.querySelectorAll('[data-pc-panel-id]').length"
        % (dom_rank, dom_rank), timeout=10_000)

    # Now step the arrow down: 'backward' moves it one slot in the shared
    # list (past a shape or a panel alike), so after a few steps it is under
    # the topmost panel but still above the rest -- a position the old
    # two-pass renderer could not draw.
    n_panels = page.evaluate(
        "() => document.querySelectorAll('[data-pc-panel-id]').length")
    assert n_panels >= 2, n_panels
    for _ in range(8):
        before = page.evaluate("() => (%s)('zia')" % find)
        src._send({"type": "order", "id": "zia", "op": "backward"})
        page.wait_for_function("() => (%s)('zia') < %d" % (find, before),
                               timeout=10_000)
        rank = page.evaluate("() => (%s)('zia')" % dom_rank)
        if 0 < rank < n_panels:
            break
    else:
        pytest.fail("arrow never landed between two panels")
    assert not errors, errors


def test_auto_flow_lands_below_explicit_panels(page_state):
    # The auto-flow must not bury owner-positioned content: a panel with no
    # x/y flows BELOW the explicitly-placed panels, not into a fixed-origin
    # grid on top of them (user report: four unplaced plots stacked over a
    # hand-placed intro, silently).
    page, errors, src = page_state
    src.register_template("floaty", "label", text="I flow")
    page.wait_for_function(
        "() => [...window.__danvas.store.ids()].some(i =>"
        " (window.__danvas.store.peek(i)||{}).props?.label === 'floaty')",
        timeout=10_000)
    placed = page.evaluate(
        "() => { const ps = [...window.__danvas.store.ids()]"
        ".map(i => window.__danvas.store.peek(i))"
        ".filter(s => s && s.typeName === 'panel');"
        " const f = ps.find(s => s.props.label === 'floaty');"
        " const rest = ps.filter(s => s !== f);"
        " return { fy: f.y, maxBottom: Math.max(...rest.map("
        "   s => s.y + (typeof s.props.h === 'number' ? s.props.h : 96))) }; }")
    assert placed["fy"] >= placed["maxBottom"], placed


def test_unplaced_anchor_keeps_its_below_chain(page_state):
    # An UNPLACED panel with a `below=` chain hanging off it (the page_layout
    # example's title) must stay on top of its chain. The v0.6.7 flow fix
    # counted rel-placed panels as owner-positioned, so the masonry re-pack
    # pushed the flow-placed root beneath its own dependents.
    page, errors, src = page_state
    src.register_template("root", "label", text="I am the root")
    src.register_template("kid", "label", text="I hang below root",
                          rel={"kind": "below", "anchor": "root", "gap": 16})
    get = ("(lbl) => { const s = window.__danvas.store;"
           " const r = [...s.ids()].map(i => s.peek(i))"
           ".find(x => x && x.typeName === 'panel' && x.props.label === lbl);"
           " return r && [r.y, r.props.h]; }")
    page.wait_for_function(
        "() => { const g = %s; return !!g('root') && !!g('kid'); }" % get,
        timeout=10_000)
    time.sleep(1.0)                     # let the masonry re-pack settle
    root, kid = page.evaluate("() => [(%s)('root'), (%s)('kid')]" % (get, get))
    assert kid[0] >= root[0] + root[1], (root, kid)
    assert abs(kid[0] - (root[0] + root[1] + 16)) < 1, (root, kid)


def test_shared_state_written_from_inside_panels(page_state):
    # canvas.setState in a Custom iframe and in a React component lands in the
    # store's props.state (applied locally first, then written through the
    # property plane). Both start from the register frame's state.
    page, errors, src = page_state
    src.register("st_custom", "Custom", props={
        "html": "<p>state</p><script>canvas.onState(function(s){"
                "if(s.seed&&!s.echo){canvas.setState({echo:s.seed*2});}});</script>",
        "w": 200, "h": 100, "state": {"seed": 7}}, x=1400, y=40)
    src.register("st_react", "React", props={
        "source": "function Component({canvas, state}){"
                  "React.useEffect(function(){"
                  "if(state.seed&&!state.echo){canvas.setState({echo:state.seed+1});}"
                  "},[state.seed,state.echo]);return React.createElement('p',null,'r');}",
        "data": "{}", "w": 200, "h": 100, "state": {"seed": 10}}, x=1400, y=180)
    get = ("(sfx) => { const s = window.__danvas.store; const id = [...s.ids()]"
           ".find(i => i.endsWith(':' + sfx)); const r = id && s.peek(id);"
           " return r && r.props && r.props.state; }")
    page.wait_for_function(
        "() => { const g = %s; const c = g('st_custom'), r = g('st_react');"
        " return !!c && c.echo === 14 && !!r && r.echo === 11; }" % get,
        timeout=15_000)
    assert not errors, errors


_LOOK = ("() => { const c = window.__danvas && window.__danvas.camera; if (!c) return;"
         " c.markInitialFitDone(); c.setCamera({x: -1300, y: -250, z: 1}, {force: true}); }")


def _frame_with(page, marker, selector, timeout=15_000):
    """The Custom iframe (Playwright Frame) whose srcdoc carries `marker`
    (the panel's composed id appears in the injected shim), once `selector`
    exists in its document. Keeps the camera on the test panel (an off-screen
    panel is culled — no iframe; the once-per-load auto-fit would zoom out)."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        page.evaluate(_LOOK)
        for f in page.frames:
            if f == page.main_frame:
                continue
            try:
                el = f.frame_element()
                if marker in (el.get_attribute("srcdoc") or ""):
                    f.wait_for_selector(selector, timeout=timeout)
                    return f
            except Exception:
                pass
        time.sleep(0.1)
    dbg = page.evaluate("() => { const s = window.__danvas && window.__danvas.store; if (!s) return 'no store';"
                        " const ids = [...s.ids()]; const cam = s.camera();"
                        " return {n: ids.length, cam, iframes: document.querySelectorAll('iframe').length,"
                        " synced: ids.filter(i => i.endsWith(':synced')).map(i => { const r = s.peek(i); return [r.x, r.y, r.props.w, r.props.h]; })}; }")
    raise AssertionError(f"no iframe carrying {marker!r}; frames: {[f.url for f in page.frames]}; {dbg}")


def test_sync_true_shares_a_plain_pages_controls_between_browsers(page_state):
    # Custom(sync=True): an unedited HTML page's native controls and button
    # clicks converge between two browsers through the panel's state, with
    # the page never mentioning `canvas`. Browser A moves a slider and picks
    # a select; B's copies follow AND B's page-side listeners run (the label
    # it derives from the slider updates). A clicks a toggle button; B's
    # toggles too. Python reads it all as panel.state.
    page, errors, src = page_state
    html = ("<input id=sl type=range min=0 max=100 value=10>"
            "<span id=lbl>10</span>"
            "<select id=mode><option>a</option><option>b</option></select>"
            "<button id=tog>off</button>"
            "<script>"
            "sl.addEventListener('input',function(){lbl.textContent=sl.value});"
            "tog.addEventListener('click',function(){tog.textContent=tog.textContent==='off'?'on':'off'});"
            "</script>")
    src.register("synced", "Custom", props={"html": html, "w": 260, "h": 140,
                                            "sync": True}, x=1400, y=320)

    # The fixture's owner is a raw SourceClient; do what a Canvas does with a
    # viewer's set_props {state}: store it and broadcast the full state.
    seen_frames = []

    def echo_state(m):
        if m.get("type") != "set_props":
            return
        seen_frames.append(m)
        state = (m.get("props") or {}).get("state")
        if m.get("id") == "synced" and isinstance(state, dict):
            src._send({"type": "update", "id": "synced", "payload": {"state": state}})
    src.on_frame(echo_state)
    fa = _frame_with(page, ":synced", "#sl")
    page_b = page.context.browser.new_context().new_page()
    page_b.goto(page.url)
    page_b.wait_for_function("() => !!window.__danvas", timeout=15_000)
    fb = _frame_with(page_b, ":synced", "#sl")
    # A moves the slider (a real input event, as a drag would fire)
    fa.locator("#sl").evaluate(
        "el => { el.value = 73; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    st_js = ("() => { const s = window.__danvas.store; const id = [...s.ids()]"
             ".find(i => i.endsWith(':synced')); return id && s.peek(id).props.state; }")
    try:
        fb.wait_for_function("() => document.getElementById('sl').value === '73'", timeout=15_000)
    except Exception:
        raise AssertionError({"A_store": page.evaluate(st_js), "B_store": page_b.evaluate(st_js),
                              "A_val": fa.locator("#sl").input_value(), "B_val": fb.locator("#sl").input_value(),
                              "A_shim": fa.evaluate("() => [typeof canvas.setState, JSON.stringify(canvas.state)]"),
                              "frames_seen": seen_frames})
    assert fb.locator("#lbl").inner_text() == "73"          # B's own listener ran
    # A picks a select option
    fa.locator("#mode").select_option("b")
    fb.wait_for_function("() => document.getElementById('mode').value === 'b'", timeout=15_000)
    # A clicks the toggle; B's toggle flips too
    fa.locator("#tog").click()
    fb.wait_for_function("() => document.getElementById('tog').textContent === 'on'", timeout=15_000)
    # and the store on B holds it all as the panel's shared state
    st = page_b.evaluate(
        "() => { const s = window.__danvas.store; const id = [...s.ids()]"
        ".find(i => i.endsWith(':synced')); return s.peek(id).props.state; }")
    assert st["sl"] == "73" and st["mode"] == "b" and st["_clicks"] == ["#tog"], st
    page_b.context.close()
    assert not errors, errors
