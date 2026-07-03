"""Render the built-in panels to a language-neutral template asset.

The wire protocol treats a register frame's props as opaque — which means a
non-Python SDK can relay panels but couldn't *author* a native-looking slider
without reproducing the React-shaped register the Python components build
(JSX ``source`` + baked ``data`` JSON). This script extracts exactly that,
once, into ``danvas/templates/components.json``: for each built-in, the
``component`` tag, the static props (JSX source, css, sizing), and the
``data`` defaults an SDK merges user kwargs over. Any language renders a
native panel by sending::

    {"type": "register", "id": ..., "name": ...,
     "component": tpl.component,
     "props": {**tpl.props, "data": json({**tpl.data, **user_kwargs}),
               "label": <display name>}}

Run whenever a built-in component's JSX or defaults change:

    python scripts/gen_component_templates.py

``tests/test_component_templates.py`` fails if the committed asset is stale.
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import danvas  # noqa: E402

OUT_PATH = os.path.join(_ROOT, "danvas", "templates", "components.json")

# The authorable core: the interactive controls + the text/content basics.
# (Media/stream panels need host-side machinery an SDK provides itself.)
_BUILDERS = {
    "slider": lambda: danvas.Slider(),
    "label": lambda: danvas.Label(""),
    "button": lambda: danvas.Button(),
    "toggle": lambda: danvas.Toggle(["A", "B"]),
    "text_field": lambda: danvas.TextField(),
    "markdown": lambda: danvas.Markdown(""),
}


def build():
    out = {}
    for kind, make in _BUILDERS.items():
        comp = make()
        props = comp.register_props_for(None, None)
        data = json.loads(props.pop("data")) if isinstance(
            props.get("data"), str) else {}
        props.pop("label", None)          # per-instance, not template
        out[kind] = {
            "component": comp.component,
            "props": props,
            "data": data,
        }
    return {
        "_generated_by": "scripts/gen_component_templates.py",
        "_note": "language-neutral register templates for the built-in panels;"
                 " merge user kwargs over `data`, json-encode into props.data",
        "templates": out,
    }


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(build(), f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
