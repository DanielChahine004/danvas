"""Markdown: render Markdown text as a native (React) canvas panel.

Markdown is converted to HTML in Python (using the ``markdown`` or
``markdown-it-py`` package if either is installed — richer output: tables,
fenced code, etc. — otherwise a small built-in converter covering headings,
bold/italic, inline code, fenced code, links, lists, pipe tables and
paragraphs). The HTML is
then mounted as a **native React subtree** rather than inside a sandboxed iframe.

Why native rather than an iframe: an iframe gets *rasterised* and then scaled
when the canvas is zoomed, so dense text looks blurry/pixelated at anything but
100%. A native node re-renders sharp at every zoom level, and — being in the
app's own document — the prose follows the canvas theme directly through the
``--pc-*`` CSS variables (no iframe ``color-scheme`` shim needed).

The converted HTML is injected with ``dangerouslySetInnerHTML``, which does not
execute ``<script>``; the markup is the user's own script-authored content, the
same trust level as the rest of their danvas app.
"""

import html as _html
import re

from . import _theme
from .react import React

# CSS scoped under `.pc-md`, driven by the canvas theme variables so the prose
# tracks dark/light automatically. `max-height:100%` + `overflow:auto` gives a
# scrollbar when a fixed-height panel is shorter than its content; in h="auto"
# mode the host's height is indefinite, so `max-height:100%` resolves to "none"
# and the panel just grows to fit (no scrollbar, no clipping of the measurement).
_MD_CSS = """
.pc-md{width:100%;max-height:100%;overflow:auto;box-sizing:border-box;padding:8px;
 font-family:system-ui,-apple-system,sans-serif;font-size:13px;line-height:1.5;
 color:var(--pc-text)}
.pc-md h1,.pc-md h2,.pc-md h3{margin:.4em 0 .3em;line-height:1.25}
.pc-md h1{font-size:1.5em}.pc-md h2{font-size:1.3em}.pc-md h3{font-size:1.1em}
.pc-md p{margin:.4em 0}.pc-md ul,.pc-md ol{margin:.4em 0;padding-left:1.4em}
.pc-md a{color:var(--pc-accent)}
.pc-md img{display:block;max-width:100%}
.pc-md pre{background:var(--pc-code-bg);border-radius:6px;padding:8px;overflow:auto}
.pc-md code{background:var(--pc-code-bg);border-radius:4px;padding:1px 4px;
 font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.pc-md pre code{background:none;padding:0}
.pc-md table{border-collapse:collapse}
.pc-md th,.pc-md td{border:1px solid var(--pc-border);padding:3px 8px}
"""

# The JSX component: drop the Python-rendered HTML into a native node. The CSS is
# carried in a <style> tag (its braces are escaped to {{ }} so str.format leaves
# them alone; only the {css} placeholder is filled).
from . import _jsx

_MD_SOURCE = _jsx.load("markdown").format(css=_MD_CSS)


class Markdown(React):
    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {"html": "str -- rendered HTML (Python converts markdown; "
                         "other SDKs send HTML directly)"},
        "updates": {"data_patch": "merge changed data fields"},
        "events": [],
    }
    default_w = 380
    default_h = 240

    def __init__(self, text="", name="markdown", color=None, label=None, w=None, h=None):
        self._text = text
        super().__init__(source=_MD_SOURCE, name=name, label=label, w=w, h=h,
                         props={"html": _md_to_html(text or ""),
                                "_th": _theme.derive(color) if color is not None else {}})
        self._init_color(color)

    @property
    def html(self):
        """The Markdown rendered to HTML (what the panel displays)."""
        return self._data.get("html", "")

    @property
    def text(self):
        """The Markdown source; assign to update it live (same as ``update``)."""
        return self._text

    @text.setter
    def text(self, value):
        self.update(value)

    def update(self, text):
        """Replace the rendered Markdown, live."""
        self._text = text
        super().update(html=_md_to_html(text or ""))


def _md_to_html(text):
    """Markdown -> HTML via an installed library, else a small built-in fallback."""
    try:
        import markdown  # type: ignore
        return markdown.markdown(text, extensions=["fenced_code", "tables"])
    except Exception:
        pass
    try:
        from markdown_it import MarkdownIt  # type: ignore
        return MarkdownIt("commonmark", {"html": False}).enable("table").render(text)
    except Exception:
        pass
    return _basic_md(text)


_INLINE = (
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"__([^_]+)__"), r"<strong>\1</strong>"),
    (re.compile(r"\*([^*]+)\*"), r"<em>\1</em>"),
    (re.compile(r"(?<!\w)_([^_]+)_(?!\w)"), r"<em>\1</em>"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2">\1</a>'),
)


def _inline(text):
    """Apply inline Markdown to an already HTML-escaped line."""
    for pattern, repl in _INLINE:
        text = pattern.sub(repl, text)
    return text


def _table_row(line):
    """Split one ``| a | b |`` line into cell strings (or None if not a row)."""
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|") and len(s) > 1):
        return None
    return [c.strip() for c in s[1:-1].split("|")]


_TABLE_RULE = re.compile(r"^:?-{3,}:?$")   # the |---|:--:|---| separator cells


def _table_starts(lines, i):
    """True when ``lines[i]`` is a table header row with a rule row under it.
    A lone ``|…|`` line with no rule is NOT a table — it stays paragraph
    text, and crucially the paragraph gatherer must still consume it (a
    breaker that doesn't start a table would stall the line cursor)."""
    header = _table_row(lines[i])
    rule = _table_row(lines[i + 1]) if i + 1 < len(lines) else None
    return bool(header and rule and len(rule) == len(header)
                and all(_TABLE_RULE.match(c) for c in rule))


def _basic_md(text):
    """A compact Markdown subset: headings, lists, fenced code, pipe tables,
    paragraphs."""
    out = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # Pipe table: a |…| header row followed by a |---|---| rule row.
        if _table_starts(lines, i):
            header = _table_row(line)
            cells = lambda row, tag: "".join(
                f"<{tag}>" + _inline(_html.escape(c)) + f"</{tag}>"
                for c in (row + [""] * len(header))[:len(header)])
            out.append("<table><thead><tr>" + cells(header, "th")
                       + "</tr></thead><tbody>")
            i += 2
            while i < len(lines):
                row = _table_row(lines[i])
                if row is None:
                    break
                out.append("<tr>" + cells(row, "td") + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        # Fenced code block: emit verbatim (escaped), no inline formatting.
        if line.strip().startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(_html.escape(lines[i]))
                i += 1
            i += 1  # skip closing fence
            out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            continue
        # Heading.
        m = re.match(r"(#{1,3})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(_html.escape(m.group(2)))}</h{level}>")
            i += 1
            continue
        # List block (consecutive -/*/digit. lines).
        if re.match(r"\s*([-*]|\d+\.)\s+", line):
            ordered = bool(re.match(r"\s*\d+\.\s+", line))
            items = []
            while i < len(lines) and re.match(r"\s*([-*]|\d+\.)\s+", lines[i]):
                item = re.sub(r"\s*([-*]|\d+\.)\s+", "", lines[i], count=1)
                items.append("<li>" + _inline(_html.escape(item)) + "</li>")
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        # Blank line: paragraph break.
        if not line.strip():
            i += 1
            continue
        # Paragraph: gather until a blank line.
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"(#{1,3})\s+|\s*([-*]|\d+\.)\s+|```", lines[i]
        ) and not _table_starts(lines, i):
            para.append(_inline(_html.escape(lines[i])))
            i += 1
        out.append("<p>" + "<br>".join(para) + "</p>")
    return "\n".join(out)