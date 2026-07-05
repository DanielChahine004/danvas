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
    # Interactive controls + text/content basics.
    "slider": lambda: danvas.Slider(),
    "label": lambda: danvas.Label(""),
    "button": lambda: danvas.Button(),
    "toggle": lambda: danvas.Toggle(["A", "B"]),
    "text_field": lambda: danvas.TextField(),
    "markdown": lambda: danvas.Markdown(""),
    # Streaming media: React panels whose frames ride the binary envelope. An
    # SDK now drives that envelope (send_video/send_audio + on_binary), so their
    # register shape belongs in the shared asset like any other native panel.
    "video": lambda: danvas.VideoFeed("cam"),
    "audio": lambda: danvas.AudioFeed("mic"),
    # Data/content panels: they render from data an SDK sets (Plotly figures via
    # update, table rows, an image src, a page url, custom html). Host-machinery
    # panels (upload endpoint, download token, file browsing, the inspector's
    # live state) still register from the same shape — their *function* depends
    # on the hub, but any language can author the panel.
    "table": lambda: danvas.Table({"Name": ["Alice", "Bob"], "Score": [92, 85]}),
    "plot": lambda: danvas.Plot(),
    "histogram": lambda: danvas.Histogram(),
    "live_plot": lambda: danvas.LivePlot(),
    "image": lambda: danvas.Image(b""),
    "webview": lambda: danvas.WebView("https://example.com"),
    "custom": lambda: danvas.Custom(html="<b>custom</b>"),
    "download": lambda: danvas.Download(source=b"", filename="file.txt", text="Download"),
    "upload": lambda: danvas.Upload(text="Choose a file"),
    "file_browser": lambda: danvas.FileBrowser(root="."),
    "inspector": lambda: danvas.Inspector(),
    "chat": lambda: danvas.Chat(),
}

# Per-instance fields that must not be baked into a reusable template (a minted
# upload endpoint token, etc.) — blanked so the asset is stable/regenerable.
def _strip_volatile(kind, data):
    if kind == "upload" and isinstance(data.get("url"), str):
        data["url"] = ""
    return data


def _contract(comp):
    """The component's declared contract, finished with the derivable parts.

    Every templated component class declares a ``CONTRACT`` (data field types,
    update keys consumed, input payload shapes emitted, request round-trips,
    binary envelope usage, legacy ``encoded`` fields) — the machine-readable
    half of what an SDK must know to drive the panel; PROTOCOL.md's "component
    contracts" section is the human-readable half. Geometry and the universal
    ``_th`` theme field are derived here rather than repeated on each class.
    """
    declared = getattr(type(comp), "CONTRACT", None)
    if declared is None:
        raise SystemExit(
            f"{type(comp).__name__} has no CONTRACT declaration — every "
            "templated component must declare one (see PROTOCOL.md)")
    contract = dict(declared)
    data = dict(contract.get("data", {}))
    # Universal: every panel accepts the derived accent-theme CSS variables
    # (what a `color=`/`.color()` sets); no class declares it individually.
    data.setdefault("_th", "object -- accent theme CSS variables, derived "
                           "from an accent color; frameColor is its "
                           "top-level register-frame twin")
    contract["data"] = data
    contract.setdefault("encoded", [])
    contract["geometry"] = {
        "w": getattr(comp, "default_w", None),
        "h": getattr(comp, "default_h", None),
        "auto_h": bool(getattr(comp, "_auto_h", False)),
    }
    return contract


def build():
    out = {}
    for kind, make in _BUILDERS.items():
        comp = make()
        props = comp.register_props_for(None, None)
        data = json.loads(props.pop("data")) if isinstance(
            props.get("data"), str) else {}
        data = _strip_volatile(kind, data)
        props.pop("label", None)          # per-instance, not template
        out[kind] = {
            "component": comp.component,
            "props": props,
            "data": data,
            "contract": _contract(comp),
        }
    return {
        "_generated_by": "scripts/gen_component_templates.py",
        "_note": "language-neutral register templates for the built-in panels;"
                 " merge user kwargs over `data`, json-encode into props.data."
                 " Each template's `contract` declares what an SDK sets/sends"
                 " and what the panel emits — see PROTOCOL.md § component"
                 " contracts",
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
