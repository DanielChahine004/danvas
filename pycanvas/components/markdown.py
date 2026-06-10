"""Markdown: render Markdown text as a panel.

Uses the ``markdown`` or ``markdown-it-py`` package if either is installed
(richer output: tables, fenced code, etc.); otherwise falls back to a small
built-in converter covering headings, bold/italic, inline code, fenced code,
links, lists and paragraphs — enough for notes and labels on the canvas.
"""

import html as _html
import re

from .custom import Custom
from ._doc import document

_MD_CSS = (
    "h1,h2,h3{margin:.4em 0 .3em;line-height:1.25}"
    "h1{font-size:1.5em}h2{font-size:1.3em}h3{font-size:1.1em}"
    "p{margin:.4em 0}ul,ol{margin:.4em 0;padding-left:1.4em}"
    "pre{background:#f1f5f9;border-radius:6px;padding:8px;overflow:auto}"
    "code{background:#f1f5f9;border-radius:4px;padding:1px 4px}"
    "pre code{background:none;padding:0}"
    "table{border-collapse:collapse}th,td{border:1px solid #cbd5e1;padding:3px 8px}"
)


class Markdown(Custom):
    component = "Custom"
    default_w = 380
    default_h = 240

    def __init__(self, text="", name="markdown", label=None, width=380, height=240):
        self._text = text
        super().__init__(html=self._render(text), name=name, label=label,
                         width=width, height=height)

    def update(self, text):
        """Replace the rendered Markdown, live."""
        self._text = text
        super().update(self._render(text))

    def _render(self, text):
        return document(_md_to_html(text or ""), _MD_CSS)


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


def _basic_md(text):
    """A compact Markdown subset: headings, lists, fenced code, paragraphs."""
    out = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
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
        ):
            para.append(_inline(_html.escape(lines[i])))
            i += 1
        out.append("<p>" + "<br>".join(para) + "</p>")
    return "\n".join(out)
