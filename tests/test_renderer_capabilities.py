"""The renderer's capability set is part of the contract — test it statically.

The frontend bundles a *partial* Plotly build; a trace type a component emits
but the bundle lacks degrades silently to a meaningless scatter render (the
Histogram/plotly-basic incident). This pins the two sides together without a
browser: every trace type any shipped component (Python or Rust) emits must be
in the bundled dist's trace set, and the built dist must actually contain the
bundle ReactHost imports.
"""

import glob
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMPONENTS = os.path.join(_ROOT, "danvas", "components")
_RUST_HELPERS = os.path.join(_ROOT, "danvas-rust", "src", "helpers.rs")
_REACT_HOST = os.path.join(_ROOT, "danvas", "frontend", "src", "react",
                           "ReactHost.tsx")
_DIST_ASSETS = os.path.join(_ROOT, "danvas", "frontend", "dist", "assets")

# The trace modules each official plotly.js partial bundle registers
# (https://github.com/plotly/plotly.js#partial-bundles). Extend when the
# frontend moves to a bigger bundle.
_BUNDLE_TRACES = {
    "plotly.js-basic-dist-min": {"scatter", "bar", "pie"},
    "plotly.js-cartesian-dist-min": {
        "scatter", "bar", "box", "heatmap", "histogram", "histogram2d",
        "histogram2dcontour", "image", "pie", "contour", "scatterternary",
        "violin",
    },
}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _bundle_name():
    m = re.search(r"import\('(plotly[^']+)'\)", _read(_REACT_HOST))
    assert m, "ReactHost.tsx no longer declares its local plotly bundle"
    return m.group(1)


# The full Plotly trace-name universe (all bundles), used to separate real
# trace emissions from unrelated "type": "..." strings in the scanned code.
_ALL_PLOTLY_TRACES = {
    "scatter", "scattergl", "bar", "pie", "box", "heatmap", "heatmapgl",
    "histogram", "histogram2d", "histogram2dcontour", "image", "contour",
    "scatterternary", "violin", "funnel", "funnelarea", "waterfall",
    "indicator", "table", "sunburst", "treemap", "icicle", "sankey",
    "scatter3d", "surface", "mesh3d", "cone", "streamtube", "volume",
    "isosurface", "scattergeo", "choropleth", "scattermapbox",
    "choroplethmapbox", "densitymapbox", "scatterpolar", "scatterpolargl",
    "barpolar", "parcoords", "parcats", "carpet", "scattercarpet",
    "contourcarpet", "ohlc", "candlestick", "splom", "pointcloud",
}


def _emitted_trace_types():
    """Every Plotly trace type the shipped component code constructs."""
    found = set()
    for path in glob.glob(os.path.join(_COMPONENTS, "*.py")):
        src = _read(path)
        # go.Scatter(...) / go.Heatmap(...) — the plotly-object builders.
        found |= {m.lower() for m in re.findall(r"go\.([A-Z]\w+)\(", src)}
        found |= set(re.findall(r'"type":\s*"(\w+)"', src))
    found |= set(re.findall(r'"type":\s*"(\w+)"', _read(_RUST_HELPERS)))
    # Only real trace names count — the scan also catches unrelated
    # "type": "str"-style strings (inspector rows, contracts, …).
    return found & _ALL_PLOTLY_TRACES


def test_bundle_covers_every_emitted_trace_type():
    bundle = _bundle_name()
    assert bundle in _BUNDLE_TRACES, (
        f"unknown plotly bundle {bundle!r} — add its trace set to "
        "_BUNDLE_TRACES (from the plotly.js partial-bundles table)")
    available = _BUNDLE_TRACES[bundle]
    required = _emitted_trace_types()
    missing = required - available
    assert not missing, (
        f"components emit Plotly trace types {sorted(missing)} that the "
        f"bundled {bundle} does not register — the figure would silently "
        "degrade in the browser. Move to a bigger partial bundle "
        "(ReactHost.tsx LOCAL_MODULES) and rebuild dist.")
    # Sanity: the guard only means something if components emit anything.
    assert "scatter" in required and "heatmap" in required


def test_dist_ships_the_imported_bundle():
    bundle = _bundle_name()
    # vite names the chunk after the entry: plotly-cartesian.min-<hash>.js
    stem = bundle.replace("plotly.js-", "plotly-").replace("-dist-min", "")
    hits = glob.glob(os.path.join(_DIST_ASSETS, f"{stem}.min-*.js"))
    assert hits, (
        f"dist/assets has no chunk for {bundle} — rebuild the frontend "
        "(npm run build) and re-embed it in danvasd")
