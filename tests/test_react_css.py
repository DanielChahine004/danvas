"""css= on a React panel.

With source= the styles ride as a `css` prop the host renders into a <style>;
with jsx= they're composed into the wrapper (and the css prop stays empty so the
styles aren't injected twice).
"""

import danvas


def test_css_with_source_rides_as_prop():
    p = danvas.React(source="function Component(){return null}",
                       css=".x{color:red}")
    assert p.register_props()["css"] == ".x{color:red}"


def test_css_with_jsx_is_baked_not_propped():
    q = danvas.React(jsx='<div className="x"/>', css=".x{color:blue}")
    assert q.register_props()["css"] == ""          # not double-injected
    assert ".x{color:blue}" in q._source            # composed into the source


def test_no_css_leaves_prop_empty():
    p = danvas.React(source="function Component(){return null}")
    assert p.register_props()["css"] == ""


def test_set_css_sends_live_update():
    sent = []
    p = danvas.React(source="function Component(){return null}")

    class B:
        def broadcast(self, msg, exclude=None):
            sent.append(msg)
    p._bind("p1", B())
    p.set_css(".y{margin:0}")
    assert p._css == ".y{margin:0}"
    assert sent[0]["payload"] == {"css": ".y{margin:0}"}
