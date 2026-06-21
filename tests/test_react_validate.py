"""React.validate(): a fast structural lint for panel source.

Catches a missing `Component` and unbalanced delimiters, skipping strings and
comments so braces in text / CSS template literals don't false-positive.
"""

import danvas


def v(source):
    return danvas.React(source=source).validate()


def test_good_source_passes():
    assert v("function Component({ props }) { return null; }") == []


def test_empty_source():
    assert v("") == ["empty source"]


def test_missing_component_flagged():
    probs = v("function Widget() { return null; }")
    assert any("Component" in p for p in probs)


def test_arrow_component_ok():
    assert v("const Component = () => null;") == []


def test_unbalanced_brace_flagged():
    probs = v("function Component() { return null; ")   # missing }
    assert any("unclosed" in p or "unbalanced" in p for p in probs)


def test_braces_in_css_template_dont_false_positive():
    # A realistic panel: CSS with { } inside a <style> template literal.
    src = (
        "function Component() {"
        "  const css = `.x{color:red} .y{margin:0}`;"
        "  return null;"
        "}"
    )
    assert v(src) == []


def test_braces_in_strings_dont_false_positive():
    assert v('function Component() { const s = "}{"; return null; }') == []


def test_jsx_built_panel_validates_clean():
    panel = danvas.React(jsx='<div className="x">hi</div>', css=".x{color:red}")
    assert panel.validate() == []
