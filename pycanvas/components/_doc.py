"""Minimal styled HTML-document wrapper shared by the display components.

The display panels (Markdown, Image, Table) and the value dispatcher render an
HTML fragment into a sandboxed ``Custom`` iframe. The iframe has its own origin,
so it can't inherit the canvas theme — these panels render on a clean light
document, matching how a notebook renders the same output inline.
"""

_BASE_CSS = (
    "body{margin:0;padding:8px;font-family:system-ui,-apple-system,sans-serif;"
    "font-size:13px;line-height:1.5;color:#0f172a;background:#fff;"
    "box-sizing:border-box;}"
    "img{display:block;max-width:100%;}"
    "a{color:#2563eb;}"
    "code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;}"
)


def document(body, css=""):
    """Wrap an HTML fragment in a minimal, self-contained styled document."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_BASE_CSS}{css}</style></head><body>{body}</body></html>"
    )
