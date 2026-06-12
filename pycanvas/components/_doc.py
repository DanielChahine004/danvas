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

# A theme-aware variant for prose panels (Markdown) that should blend into the
# canvas instead of reading as a white notebook output. The body is transparent
# so the panel's own background shows through, and `color-scheme: light dark`
# lets the text/link colours follow the embedder's scheme: the frontend sets the
# iframe element's `color-scheme` to match tldraw's dark/light toggle, so the
# `prefers-color-scheme` query below tracks the canvas theme (not the OS).
_THEMED_CSS = (
    ":root{color-scheme:light dark;}"
    "body{margin:0;padding:8px;font-family:system-ui,-apple-system,sans-serif;"
    "font-size:13px;line-height:1.5;color:#0f172a;background:transparent;"
    "box-sizing:border-box;}"
    "img{display:block;max-width:100%;}"
    "a{color:#2563eb;}"
    "code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;}"
    "@media (prefers-color-scheme: dark){"
    "body{color:#e5e7eb;}a{color:#60a5fa;}}"
)


def document(body, css="", theme=False):
    """Wrap an HTML fragment in a minimal, self-contained styled document.

    ``theme=True`` selects a transparent, theme-aware base (for prose panels that
    should blend into the canvas); the default is the white notebook-style page.
    """
    base = _THEMED_CSS if theme else _BASE_CSS
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{base}{css}</style></head><body>{body}</body></html>"
    )
