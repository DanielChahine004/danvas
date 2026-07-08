"""Model3D: a prebuilt CAD/3D viewer panel — hand it GLB bytes, done.

The viewer inside is `xeokit <https://xeokit.io>`_ (the established
BIM/engineering web viewer, loaded from its CDN), pre-wired with the tools a
part viewer needs: orbit/pan with fine-grained proportional zoom, snap-to-edge
**distance** and **angle** measurements, a draggable **section plane**,
**X-ray** and **edges** display toggles, a **NavCube** orientation gizmo,
fit/clear — the distilled version of the panel every CAD script used to
hand-write.

Hovering the model shows what's under the cursor — the entity (glTF node)
name and the vertex-snapped coordinate; on a point cloud, the point's index
too (``cloud #4172``). A click sends the same info to Python::

    @viewer.on_pick
    def picked(msg):
        print(msg["id"], msg["point"], msg["pos"])

Not just solids: glTF carries POINTS and LINES primitives as well as
triangles, so point clouds, polylines, and sampled curves render in the
same space — a ``trimesh.Scene`` holding meshes, ``PointCloud``\\ s, and
``Path3D``\\ s exports to one GLB. Per-part materials come through; a
``COLOR_0`` vertex attribute (xeokit's loader ignores it) is applied as an
entity tint — the node's *average* vertex color, flat, not a gradient.
The **Items** toolbar button lists the model's top-level glTF
nodes with show/hide checkboxes — group geometries under parent nodes to
shape that list.

The input is **glTF-Binary (GLB)** — the industry-standard 3D binary, which
every CAD/mesh stack exports::

    viewer = canvas.model3d("part")

    @canvas.on_edit
    def rebuild():
        export_gltf(make_part(), "part.glb", binary=True)   # build123d
        viewer.update(open("part.glb", "rb").read())

``update`` accepts GLB ``bytes``, a filesystem path to a ``.glb``/``.gltf``,
or any object exposing a glTF exporter danvas recognises (a trimesh
``Trimesh``/``Scene`` via ``export(file_type="glb")``), plus overlay
keywords composed into the same frame::

    viewer.update(part_glb, points=pts, curve=helix,
                  mesh_color=(110, 150, 220, 255))

The scene is made of **layers** — named, independently replaceable slices,
each with its own Items row. ``update`` addresses the default ``"model"``
layer; ``viewer.layer(name)`` returns a handle for the rest::

    viewer.layer("part").update("part.glb", mesh_color=STEEL)
    viewer.layer("cloud").points(pts)        # part stays put
    viewer.layer("path").curve(helix)        # animate just this, cheaply
    viewer.layer("cloud").visible = False
    viewer.layer("path").clear()

Pushing a layer replaces only that layer; the camera frames the first load
and holds your viewpoint afterwards.

Polyglot: the panel ships as the ``model3d`` template, and the bytes ride the
CUSTOM binary envelope — any SDK renders a part by sending one register frame
and pushing GLB bytes (Rust: ``send_media(3, id, glb)``).

Section-cap note: a filled cut face needs a closed solid mesh. GLB exported
from CAD kernels is usually one open surface per face, so the section slices
without a cap — the standard trade for taking the standard format.
"""

import json
import os
import struct

from .custom import Custom

# Default overlay colors (RGBA 0-255).
POINT_COLOR = (255, 40, 40, 255)    # red
LINE_COLOR = (0, 200, 80, 255)      # green
CURVE_COLOR = (255, 170, 0, 255)    # orange
MESH_COLOR = (150, 160, 175, 255)   # neutral grey-blue (voxels/isosurfaces)

# Built-in colormap (viridis stops) for `color_by=` / isosurface levels; a
# `cmap` argument accepts a list of RGB(A) 0-255 stops to lerp, or a
# callable t∈[0,1] -> RGBA 0-255.
_VIRIDIS = ((68, 1, 84), (71, 44, 122), (59, 81, 139), (44, 113, 142),
            (33, 144, 141), (39, 173, 129), (92, 200, 99), (170, 220, 50),
            (253, 231, 37))


def _rgba(color, default=None):
    """RGBA 0-255 from an (r,g,b[,a]) tuple or hex string — the same forms
    ``color=`` accepts across the package: ``'#38f'``, ``'#3b82f6'``, plus
    ``'#rgba'``/``'#rrggbbaa'`` for translucency — or ``default``."""
    if color is None:
        return default
    if isinstance(color, (tuple, list)):
        c = tuple(int(x) for x in color)
        return c if len(c) == 4 else (*c[:3], 255)
    s = str(color).strip().lstrip("#")
    if len(s) in (3, 4):
        s = "".join(ch * 2 for ch in s)
    v = [int(s[i:i + 2], 16) for i in range(0, len(s), 2)]
    return (*v, 255)[:4] if len(v) == 3 else tuple(v[:4])


def _cmap_rgba(t, cmap=None):
    if callable(cmap):
        return _rgba(cmap(float(t)))
    stops = ([_rgba(s) for s in cmap] if cmap is not None
             else [_rgba(s) for s in _VIRIDIS])
    t = min(max(float(t), 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    a, b = stops[i], stops[i + 1]
    return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(4))


def _bucketize(values, n):
    """Bucket indices (per value) + normalized bucket centers, for
    `color_by=`: values -> n color bands."""
    import numpy as np
    v = np.asarray(values, float).ravel()
    lo, hi = float(np.nanmin(v)), float(np.nanmax(v))
    t = np.zeros_like(v) if hi <= lo else (v - lo) / (hi - lo)
    idx = np.minimum((t * n).astype(int), n - 1)
    return idx, [(b + 0.5) / n for b in range(n)]

# Layer frame: b"DVL1" + u16le(name length) + UTF-8 layer name + GLB bytes.
# A payload that starts with the GLB magic instead is the legacy form and
# addresses the "model" layer.
_LAYER_MAGIC = b"DVL1"


def _layer_frame(name, glb):
    nb = name.encode("utf-8")
    return _LAYER_MAGIC + struct.pack("<H", len(nb)) + nb + glb


def compose_glb(source=None, *, points=None, lines=None, curve=None,
                point_color=None, line_color=None, curve_color=None,
                mesh_color=None, _nodes=None):
    """Build one GLB from a base model and/or overlays.

    ``source`` is GLB ``bytes``, a ``.glb``/``.gltf`` path, or a trimesh-like
    object with ``export(file_type='glb')`` — or ``None`` for an overlay-only
    GLB. Overlays (all optional, numpy-shaped, coordinates in the model's
    world frame):

    - ``points``: (N, 3) → a point cloud (node ``points``).
    - ``lines``: (M, 2, 3) → M independent segments (node ``lines``).
    - ``curve``: (K, 3) samples along a function; consecutive samples are
      joined into a connected polyline (node ``curve``).

    Colors take the package's usual forms — an (r,g,b[,a]) 0-255 tuple or a
    hex string (``'#38f'``, ``'#3b82f6'``, ``'#3b82f680'`` for alpha);
    ``mesh_color`` becomes a glTF material on every source face that has
    none. Everything is appended straight into the GLB's binary chunk with
    numpy — millions of points are fine.

    Unnamed source nodes are named (``model``/``model_N``) so the viewer's
    Items panel and hover labels can identify them.
    """
    import numpy as np

    pts = (np.zeros((0, 3)) if points is None
           else np.asarray(points, float).reshape(-1, 3))
    seg_pts = (np.zeros((0, 3)) if lines is None
               else np.asarray(lines, float).reshape(-1, 3))
    cur = (np.zeros((0, 3)) if curve is None
           else np.asarray(curve, float).reshape(-1, 3))
    cur_pts = (np.zeros((0, 3)) if len(cur) < 2
               else np.stack([cur[:-1], cur[1:]], axis=1).reshape(-1, 3))
    if (source is None and _nodes is None
            and not (len(pts) or len(seg_pts) or len(cur_pts))):
        raise ValueError("compose_glb needs a source model and/or overlays")

    if source is not None:
        glb = _coerce_glb(source)
        if glb[:4] != b"glTF":
            raise ValueError("source is not glTF-Binary (GLB)")
        jlen, = struct.unpack_from("<I", glb, 12)
        doc = json.loads(glb[20:20 + jlen])
        boff = 20 + jlen
        blen = (struct.unpack_from("<I", glb, boff)[0]
                if boff + 8 <= len(glb) else 0)
        bin0 = glb[boff + 8: boff + 8 + blen]
    else:
        doc = {"asset": {"version": "2.0"}}
        bin0 = b""
    doc.setdefault("scenes", [{"nodes": []}])
    doc.setdefault("scene", 0)
    for key in ("nodes", "meshes", "accessors", "bufferViews"):
        doc.setdefault(key, [])
    doc.setdefault("buffers", [{"byteLength": 0}])
    scene_nodes = doc["scenes"][doc["scene"]].setdefault("nodes", [])

    chunks = [bin0, b"\0" * ((-len(bin0)) % 4)]
    off = sum(len(c) for c in chunks)

    if doc["nodes"]:
        root = scene_nodes[0] if scene_nodes else 0
        for i, nd in enumerate(doc["nodes"]):
            nd.setdefault("name", "model" if i == root else f"model_{i}")

    if mesh_color is not None and doc["meshes"]:
        # A real glTF material — the standard way to color a mesh, honored
        # by any glTF viewer, not just this panel.
        doc.setdefault("materials", []).append({"pbrMetallicRoughness": {
            "baseColorFactor": [c / 255 for c in _rgba(mesh_color)],
            "metallicFactor": 0.1, "roughnessFactor": 0.6}})
        for mesh in doc["meshes"]:
            for prim in mesh.get("primitives", []):
                prim.setdefault("material", len(doc["materials"]) - 1)

    def add_accessor(arr, ctype, atype, normalized=False, minmax=False):
        nonlocal off
        b = arr.tobytes()
        doc["bufferViews"].append(
            {"buffer": 0, "byteOffset": off, "byteLength": len(b)})
        acc = {"bufferView": len(doc["bufferViews"]) - 1,
               "componentType": ctype, "count": len(arr), "type": atype}
        if normalized:
            acc["normalized"] = True
        if minmax:
            acc["min"] = arr.min(axis=0).tolist()
            acc["max"] = arr.max(axis=0).tolist()
        doc["accessors"].append(acc)
        chunks.extend([b, b"\0" * ((-len(b)) % 4)])
        off += len(b) + ((-len(b)) % 4)
        return len(doc["accessors"]) - 1

    overlays = (("points", pts, _rgba(point_color, POINT_COLOR), 0),
                ("lines", seg_pts, _rgba(line_color, LINE_COLOR), 1),
                ("curve", cur_pts, _rgba(curve_color, CURVE_COLOR), 1))
    for name, verts, rgba, mode in overlays:
        if len(verts) == 0:
            continue
        v = np.ascontiguousarray(verts, dtype=np.float32)
        c = np.ascontiguousarray(np.broadcast_to(
            np.asarray(rgba, np.uint8), (len(v), 4)))
        prim = {"attributes": {
                    "POSITION": add_accessor(v, 5126, "VEC3", minmax=True),
                    "COLOR_0": add_accessor(c, 5121, "VEC4", normalized=True)},
                "mode": mode}
        doc["meshes"].append({"primitives": [prim]})
        doc["nodes"].append({"name": name, "mesh": len(doc["meshes"]) - 1})
        scene_nodes.append(len(doc["nodes"]) - 1)

    # Generic node specs (the voxel/isosurface/color_by machinery): each is
    # {name, mode, verts, rgba, indices?, normals?}. Node names may carry a
    # "base/sub" suffix — the viewer folds those into one Items row.
    for spec in (_nodes or []):
        v = np.ascontiguousarray(
            np.asarray(spec["verts"], np.float32).reshape(-1, 3))
        if len(v) == 0:
            continue
        rgba = _rgba(spec.get("rgba"), MESH_COLOR)
        c = np.ascontiguousarray(np.broadcast_to(
            np.asarray(rgba, np.uint8), (len(v), 4)))
        attrs = {"POSITION": add_accessor(v, 5126, "VEC3", minmax=True),
                 "COLOR_0": add_accessor(c, 5121, "VEC4", normalized=True)}
        if spec.get("normals") is not None:
            attrs["NORMAL"] = add_accessor(np.ascontiguousarray(
                np.asarray(spec["normals"], np.float32).reshape(-1, 3)),
                5126, "VEC3")
        prim = {"attributes": attrs, "mode": spec.get("mode", 4)}
        if spec.get("indices") is not None:
            prim["indices"] = add_accessor(np.ascontiguousarray(
                np.asarray(spec["indices"], np.uint32).ravel()),
                5125, "SCALAR")
        doc["meshes"].append({"primitives": [prim]})
        doc["nodes"].append({"name": spec["name"],
                             "mesh": len(doc["meshes"]) - 1})
        scene_nodes.append(len(doc["nodes"]) - 1)

    doc["buffers"][0]["byteLength"] = off
    jb = json.dumps(doc, separators=(",", ":")).encode()
    jb += b" " * ((-len(jb)) % 4)
    body = b"".join(chunks)
    return b"".join([
        struct.pack("<III", 0x46546C67, 2, 28 + len(jb) + len(body)),
        struct.pack("<II", len(jb), 0x4E4F534A), jb,          # "JSON"
        struct.pack("<II", len(body), 0x004E4942), body])     # "BIN"


def _coerce_glb(source):
    """GLB bytes from bytes, a .glb/.gltf path, or a trimesh-like object."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    if isinstance(source, (str, os.PathLike)) and os.path.isfile(source):
        with open(source, "rb") as f:
            return f.read()
    if hasattr(source, "export"):
        return source.export(file_type="glb")
    raise TypeError(
        "expected GLB bytes, a .glb/.gltf path, or a trimesh-like object "
        "with export(file_type='glb') — for build123d, "
        "export_gltf(part, path, binary=True) first")


def _face_quads(cells, axis, sign, P, O, base):
    """Quads for one face direction of the given cells: verts, per-face
    normals, and triangle indices starting at vertex ``base``."""
    import numpy as np
    u, w = [x for x in range(3) if x != axis]
    corners = np.zeros((4, 3))
    corners[:, axis] = (sign + 1) // 2
    # wind so the face's normal points along `sign` on `axis`
    order = ((0, 0), (0, 1), (1, 1), (1, 0))
    for ci, (du, dw) in enumerate(order if sign > 0 else order[::-1]):
        corners[ci, u] += du
        corners[ci, w] += dw
    v = ((cells[:, None, :] + corners[None, :, :]) * P + O).reshape(-1, 3)
    nrm = np.zeros(3)
    nrm[axis] = sign
    n = np.tile(nrm, (len(cells) * 4, 1))
    quad = np.arange(len(cells))[:, None] * 4 + base
    idx = (quad + np.array([0, 1, 2, 0, 2, 3])).ravel()
    return v, n, idx


def _box_surface(grid, pitch, origin):
    """Exposed-face box mesh of an occupancy grid (pure numpy).

    One quad per face where a filled cell meets empty space (or the grid
    border) — the classic voxel mesher: interior faces never exist, per-face
    normals keep the cubes crisp. Cell (i,j,k) spans
    ``origin + [i,i+1]*pitch`` per axis. Returns (verts, normals, indices).
    """
    import numpy as np
    g = np.asarray(grid).astype(bool)
    P = np.asarray(pitch, float) * np.ones(3)
    O = np.asarray(origin, float)
    gp = np.pad(g, 1, constant_values=False)
    core = (slice(1, -1),) * 3
    all_v, all_n, all_i, base = [], [], [], 0
    for axis in range(3):
        for sign in (1, -1):
            sl = list(core)
            sl[axis] = (slice(2, None) if sign > 0 else slice(0, -2))
            cells = np.argwhere(g & ~gp[tuple(sl)])
            if not len(cells):
                continue
            v, n, i = _face_quads(cells, axis, sign, P, O, base)
            all_v.append(v)
            all_n.append(n)
            all_i.append(i)
            base += len(v)
    if not all_v:
        return (np.zeros((0, 3)),) * 2 + (np.zeros(0, np.uint32),)
    return (np.concatenate(all_v), np.concatenate(all_n),
            np.concatenate(all_i))


def _rank_surfaces(rank, pitch, origin):
    """Per-rank walls of a ranked voxel grid: ``{rank: (verts, normals,
    indices)}``, with each boundary face owned by the HIGHER rank only
    (``rank`` -1 = empty). One wall per boundary — two adjacent opacity
    bands never emit coincident quads, which would z-fight and
    double-blend into a murky wall."""
    import numpy as np
    r = np.asarray(rank)
    P = np.asarray(pitch, float) * np.ones(3)
    O = np.asarray(origin, float)
    rp = np.pad(r, 1, constant_values=-1)
    core = (slice(1, -1),) * 3
    acc = {}   # band -> [verts...], [normals...], [indices...], base
    for axis in range(3):
        for sign in (1, -1):
            sl = list(core)
            sl[axis] = (slice(2, None) if sign > 0 else slice(0, -2))
            nbr = rp[tuple(sl)]
            cells = np.argwhere((r >= 0) & (nbr < r))
            if not len(cells):
                continue
            bands = r[tuple(cells.T)]
            for b in np.unique(bands):
                a = acc.setdefault(int(b), ([], [], [], [0]))
                v, n, i = _face_quads(cells[bands == b], axis, sign,
                                      P, O, a[3][0])
                a[0].append(v)
                a[1].append(n)
                a[2].append(i)
                a[3][0] += len(v)
    return {b: (np.concatenate(v), np.concatenate(n), np.concatenate(i))
            for b, (v, n, i, _) in acc.items()}


def _surface_net(field, level, spacing, origin):
    """Isosurface of a 3D scalar field at ``level`` (pure numpy).

    Surface nets: one vertex per grid cell the surface crosses (placed at
    the mean of its edge crossings), one quad per crossing grid edge —
    no marching-cubes table, no scikit-image dependency. Normals come from
    the field gradient, so shading is smooth. Sample (i,j,k) sits at
    ``origin + (i,j,k)*spacing``. Returns (verts, normals, indices).
    """
    import numpy as np
    F = np.asarray(field, float)
    S = np.asarray(spacing, float) * np.ones(3)
    O = np.asarray(origin, float)
    inside = F > level
    cshape = tuple(s - 1 for s in F.shape)
    agg_all = np.ones(cshape, bool)
    agg_any = np.zeros(cshape, bool)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                blk = inside[dx:cshape[0] + dx, dy:cshape[1] + dy,
                             dz:cshape[2] + dz]
                agg_all &= blk
                agg_any |= blk
    mixed = agg_any & ~agg_all
    cells = np.argwhere(mixed)
    if not len(cells):
        return (np.zeros((0, 3)),) * 2 + (np.zeros(0, np.uint32),)
    cell_id = np.full(cshape, -1, np.int64)
    cell_id[tuple(cells.T)] = np.arange(len(cells))
    acc = np.zeros((len(cells), 3))
    cnt = np.zeros(len(cells))
    quads = []
    for axis in range(3):
        sl0 = [slice(None)] * 3
        sl1 = [slice(None)] * 3
        sl0[axis] = slice(0, -1)
        sl1[axis] = slice(1, None)
        f0 = F[tuple(sl0)]
        f1 = F[tuple(sl1)]
        cross = (f0 > level) != (f1 > level)
        e = np.argwhere(cross)
        if not len(e):
            continue
        t = (level - f0[cross]) / (f1[cross] - f0[cross])
        pos = e.astype(float)
        pos[:, axis] += t
        u, w = [x for x in range(3) if x != axis]
        quad_ids = []
        for du, dw in ((0, 0), (0, 1), (1, 1), (1, 0)):
            cc = e.copy()
            cc[:, u] -= du
            cc[:, w] -= dw
            valid = ((cc >= 0).all(1)
                     & (cc[:, 0] < cshape[0]) & (cc[:, 1] < cshape[1])
                     & (cc[:, 2] < cshape[2]))
            ids = np.full(len(e), -1, np.int64)
            ids[valid] = cell_id[tuple(cc[valid].T)]
            ok = ids >= 0   # a cell touching a crossing edge is mixed
            np.add.at(acc, ids[ok], pos[ok])
            np.add.at(cnt, ids[ok], 1)
            quad_ids.append(ids)
        q = np.stack(quad_ids, 1)
        q = q[(q >= 0).all(1)]
        flip = (f0[cross] > level)[(np.stack(quad_ids, 1) >= 0).all(1)]
        q[flip] = q[flip, ::-1]
        quads.append(q)
    quads = (np.concatenate(quads) if quads
             else np.zeros((0, 4), np.int64))
    vpos = acc / np.maximum(cnt, 1)[:, None]
    verts = vpos * S + O
    idx = quads[:, [0, 1, 2, 0, 2, 3]].ravel().astype(np.uint32)
    gx, gy, gz = np.gradient(F)
    vi = np.clip(np.round(vpos).astype(int), 0,
                 np.asarray(F.shape) - 1)
    nrm = -np.stack([gx[tuple(vi.T)], gy[tuple(vi.T)],
                     gz[tuple(vi.T)]], 1)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    return verts, nrm, idx

_VIEWER_HTML = """
<style>
    html, body { margin: 0; height: 100%; background: #1a1a1b; overflow: hidden;
                 font-family: monospace; }
    #xk { width: 100vw; height: 100vh; cursor: grab; }
    #xk:active { cursor: grabbing; }
    #vol { position: absolute; inset: 0; width: 100vw; height: 100vh;
           pointer-events: none; }
    #wl { position: absolute; bottom: 8px; left: 10px; display: none;
          color: #888; font-size: 11px; pointer-events: none;
          background: rgba(0,0,0,0.8); padding: 4px 8px; border-radius: 4px; }
    #axes { position: absolute; left: 10px; bottom: 34px;
            pointer-events: none; }
    #nav { position: absolute; right: 8px; bottom: 8px; width: 110px;
           height: 110px; z-index: 2; }
    #status { position: absolute; top: 10px; left: 10px; color: #888;
              font-size: 11px; pointer-events: none;
              background: rgba(0,0,0,0.8); padding: 4px 8px; border-radius: 4px; }
    #toolbar { position: absolute; top: 10px; right: 10px; display: flex; gap: 6px; }
    #toolbar button { font-family: monospace; font-size: 11px; color: #ddd;
                      background: #2a2a2c; border: 1px solid #444;
                      border-radius: 4px; padding: 6px 10px; cursor: pointer; }
    #toolbar button.on { background: #3b6ea5; border-color: #5a8fc7; color: #fff; }
    #toolbar button:hover { border-color: #777; }
    .viewer-ruler-label { font-family: monospace !important; }
    #tip { position: absolute; display: none; z-index: 3; color: #ddd;
           font-size: 11px; pointer-events: none; white-space: pre;
           background: rgba(0,0,0,0.85); padding: 4px 8px; border-radius: 4px; }
    #items { position: absolute; display: none; z-index: 2; top: 44px;
             right: 10px; max-height: 60vh; overflow-y: auto; color: #ddd;
             font-size: 11px; background: rgba(20,20,22,0.95);
             border: 1px solid #444; border-radius: 4px; padding: 6px 10px; }
    #items label { display: block; padding: 3px 2px; cursor: pointer;
                   white-space: nowrap; }
    #items input { vertical-align: middle; margin-right: 6px; }
</style>

<canvas id="xk"></canvas>
<canvas id="vol"></canvas>
<canvas id="nav"></canvas>
<canvas id="axes" width="88" height="88"></canvas>
<div id="status">WAITING FOR MODEL…</div>
<div id="wl">W 1.00 / L 0.50 — right-drag to window</div>
<div id="tip"></div>
<div id="toolbar">
    <button id="btnMeasure">Measure</button>
    <button id="btnAngle">Angle</button>
    <button id="btnSection">Section</button>
    <button id="btnXray">X-ray</button>
    <button id="btnEdges" class="on">Edges</button>
    <button id="btnOrtho">Ortho</button>
    <button id="btnVolMode" style="display:none">MIP</button>
    <button id="btnItems">Items</button>
    <button id="btnReset">Fit</button>
    <button id="btnClear">Clear</button>
</div>
<div id="items"></div>

<script type="module">
    import {
        Viewer, GLTFLoaderPlugin, SectionPlanesPlugin, NavCubePlugin,
        DistanceMeasurementsPlugin, DistanceMeasurementsMouseControl,
        AngleMeasurementsPlugin, AngleMeasurementsMouseControl,
        PointerLens, math
    } from "https://cdn.jsdelivr.net/npm/@xeokit/xeokit-sdk@2/dist/xeokit-sdk.es.min.js";

    const status = document.getElementById('status');
    // Middle-drag pans (xeokit binds MOUSE_MIDDLE_BUTTON to pan); stop the
    // browser's middle-click autoscroll from stealing the gesture first.
    document.getElementById('xk').addEventListener(
        'mousedown', (e) => { if (e.button === 1) e.preventDefault(); });
    document.getElementById('xk').addEventListener(
        'auxclick', (e) => { if (e.button === 1) e.preventDefault(); });
    const viewer = new Viewer({ canvasId: "xk", transparent: true,
                                readableGeometryEnabled: true });
    viewer.scene.pointsMaterial.pointSize = 5;   // clouds read as dots, not dust
    viewer.scene.pointsMaterial.roundPoints = true;
    // glTF world units are meters by spec (a mm-modeled CAD part arrives
    // as 0.02-unit geometry) — read measurements out in mm. The label
    // format is (length * scale).toFixed(2) + unit, so "20.00mm".
    viewer.scene.metrics.units = "millimeters";
    viewer.scene.metrics.scale = 1000;
    viewer.camera.eye = [60, 60, 60];
    viewer.camera.look = [0, 0, 0];
    viewer.camera.up = [0, 1, 0];
    // near/far are set per-load, scaled to the model (a mm-modeled part
    // arrives in meters — a 20mm box is 0.02 units, inside any fixed near).
    // followPointer is OFF: its pivot machinery drifts camera.look during a
    // left-drag even with the rotate keymap unbound — that drift is an
    // off-centre orbit. The two things it provided are reimplemented below:
    // orbit (about the model centre, deliberately) and wheel zoom-to-cursor.
    viewer.cameraControl.followPointer = false;
    viewer.cameraControl.mouseWheelDollyRate = 0;   // replaced below
    {   // Middle-drag pans. Not via xeokit's keymap — its mousemove handler
        // doesn't recognise a held middle button (real mousemove events carry
        // button=0), so drive the camera directly: screen-space drag → a
        // camera-space pan sized so the content follows the cursor.
        const cvs = document.getElementById('xk');
        let panning = false, lx = 0, ly = 0;
        cvs.addEventListener('mousedown', (e) => {
            if (e.button === 1) {
                panning = true; lx = e.clientX; ly = e.clientY;
                e.preventDefault();
            }
        });
        document.addEventListener('mousemove', (e) => {
            if (!panning) return;
            const dx = e.clientX - lx, dy = e.clientY - ly;
            lx = e.clientX; ly = e.clientY;
            const camera = viewer.camera;
            const dist = math.lenVec3(
                math.subVec3(camera.look, camera.eye, []));
            const worldPerPx = (camera.projection === "ortho"
                ? camera.ortho.scale
                : 2 * dist * Math.tan(
                      (camera.perspective.fov / 2) * Math.PI / 180))
                / (cvs.clientHeight || 1);
            camera.pan([dx * worldPerPx, dy * worldPerPx, 0]);
        });
        document.addEventListener('mouseup', (e) => {
            if (e.button === 1) panning = false;
        });
    }
    {   // Left-drag orbits about the model CENTRE (camera.look — placed there
        // by the first-load jump and Fit, and carried along by the pan), not
        // the surface point under the cursor: xeokit's own rotate is unbound
        // and the camera is driven directly. Wheel dolly keeps followPointer
        // (zoom toward the cursor) — that pairing is why this isn't done by
        // just flipping followPointer off.
        const cc = viewer.cameraControl;
        const km = cc.keyMap;
        km[cc.MOUSE_ROTATE] = [];
        cc.keyMap = km;
        const cvs = document.getElementById('xk');
        let orbiting = false, ox = 0, oy = 0;
        cvs.addEventListener('mousedown', (e) => {
            // pointerEnabled goes false while a gizmo (the section plane's
            // arrows) owns the pointer — don't orbit under its drags.
            if (e.button === 0 && viewer.cameraControl.pointerEnabled) {
                orbiting = true; ox = e.clientX; oy = e.clientY;
            }
        });
        document.addEventListener('mousemove', (e) => {
            if (!orbiting) return;
            const dx = e.clientX - ox, dy = e.clientY - oy;
            ox = e.clientX; oy = e.clientY;
            viewer.camera.orbitYaw(-dx * 0.4);
            viewer.camera.orbitPitch(dy * 0.4);
        });
        document.addEventListener('mouseup', (e) => {
            if (e.button === 0) orbiting = false;
        });
    }
    {   // Wheel dollies toward the cursor: scale eye AND look toward the 3D
        // point under the pointer (model surface if hit, else the current
        // look), so the spot you point at stays put while the view closes in
        // — and the orbit pivot converges on what you're inspecting.
        const cvs = document.getElementById('xk');
        cvs.addEventListener('wheel', (e) => {
            e.preventDefault();
            const notches = Math.max(1, Math.abs(e.deltaY) / 100);
            const s = Math.pow(0.9, -Math.sign(e.deltaY) * notches);
            const camera = viewer.camera;
            let P = camera.look;
            const hit = viewer.scene.pick({
                canvasPos: [e.offsetX, e.offsetY], pickSurface: true });
            if (hit && hit.worldPos) P = hit.worldPos;
            camera.eye = [P[0] + (camera.eye[0] - P[0]) * s,
                          P[1] + (camera.eye[1] - P[1]) * s,
                          P[2] + (camera.eye[2] - P[2]) * s];
            camera.look = [P[0] + (camera.look[0] - P[0]) * s,
                           P[1] + (camera.look[1] - P[1]) * s,
                           P[2] + (camera.look[2] - P[2]) * s];
        }, { passive: false });
    }

    // The sandboxed iframe (opaque origin) can't XHR-fetch blob: URLs, so
    // hand the loader its bytes directly instead of a src it would fetch.
    // Keyed by src: two layers loading at once must not cross wires.
    const pendingBufs = new Map();
    const serveBuf = (src, ok) => {
        ok(pendingBufs.get(src));
        pendingBufs.delete(src);
    };
    const loader        = new GLTFLoaderPlugin(viewer, {
        dataSource: {   // the loader picks the getter by src extension
            getGLB:  (src, ok, err) => serveBuf(src, ok),
            getGLTF: (src, ok, err) => serveBuf(src, ok),
        }
    });
    const sectionPlanes = new SectionPlanesPlugin(viewer, { overviewVisible: false });
    const distance      = new DistanceMeasurementsPlugin(viewer);
    const measureCtrl   = new DistanceMeasurementsMouseControl(distance, {
        pointerLens: new PointerLens(viewer)
    });
    measureCtrl.snapping = true;
    const angle         = new AngleMeasurementsPlugin(viewer);
    const angleCtrl     = new AngleMeasurementsMouseControl(angle, {
        pointerLens: new PointerLens(viewer)
    });
    angleCtrl.snapping = true;
    const navCube = new NavCubePlugin(viewer, { canvasId: "nav", visible: true,
                                                color: "#2a2a2c", textColor: "#ddd" });

    {   // XYZ axes gizmo (bottom-left): the world axes projected with the
        // live camera — X red / Y green / Z blue (glTF world: Y up,
        // meters); an axis pointing into the screen draws dimmed.
        const axc = document.getElementById('axes');
        const ctx = axc.getContext('2d');
        function drawAxes() {
            const cam = viewer.camera;
            const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
            const crs = (a, b) => [a[1] * b[2] - a[2] * b[1],
                                   a[2] * b[0] - a[0] * b[2],
                                   a[0] * b[1] - a[1] * b[0]];
            const nv = (a) => {
                const l = Math.hypot(a[0], a[1], a[2]) || 1;
                return [a[0] / l, a[1] / l, a[2] / l];
            };
            const fwd = nv(sub(cam.look, cam.eye));
            const right = nv(crs(fwd, cam.up));
            const up = crs(right, fwd);
            ctx.clearRect(0, 0, 88, 88);
            const cx = 44, cy = 44, L = 32;
            const proj = [[[1, 0, 0], "#e74c3c", "X"],
                          [[0, 1, 0], "#2ecc71", "Y"],
                          [[0, 0, 1], "#3b82f6", "Z"]].map(([a, col, lab]) => ({
                x: right[0] * a[0] + right[1] * a[1] + right[2] * a[2],
                y: up[0] * a[0] + up[1] * a[1] + up[2] * a[2],
                z: fwd[0] * a[0] + fwd[1] * a[1] + fwd[2] * a[2],
                col, lab }));
            proj.sort((p, q) => q.z - p.z);   // into-screen axes drawn first
            ctx.font = "bold 11px monospace";
            for (const p of proj) {
                const ex = cx + p.x * L, ey = cy - p.y * L;
                ctx.globalAlpha = p.z > 0 ? 0.4 : 1.0;
                ctx.strokeStyle = ctx.fillStyle = p.col;
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.lineTo(ex, ey);
                ctx.stroke();
                ctx.fillText(p.lab, ex + (p.x >= 0 ? 3 : -11), ey + 4);
            }
            ctx.globalAlpha = 1;
        }
        viewer.camera.on("matrix", drawAxes);
        drawAxes();
    }

    // The scene is LAYERS: named, independently replaceable models. A push
    // addresses one layer and leaves the rest standing — that's what makes
    // per-frame animation of one overlay (or a part rebuild that leaves a
    // million-point cloud alone) cheap.
    const layers = new Map();   // name -> {model, groups, colors, counts, seq}
    const layerHidden = {};     // layer name -> hidden via a Python control
    let loadedAny = false;      // the first successful load frames the camera
    let frameSeq = 0;           // unique model ids / dataSource keys
    let measuring = false;
    let angling = false;
    let sectioning = false;
    let xrayed = false;
    let edgesOn = true;
    let orthoMode = false;
    const vtxCache = {};   // entity id -> Float64Array xyz, point clouds only

    function anyLoaded() {
        for (const L of layers.values()) if (L.model) return true;
        return false;
    }

    // ---- fused volume overlay -------------------------------------------
    // Volume layers (DVV2 frames: PET/CT recons, density fields) render on
    // a transparent WebGL2 canvas ray-marched with the SAME camera as the
    // geometry, every frame. Compositing is overlay-onto-geometry: where
    // the volume is dim it's transparent and models show through; there is
    // no depth interleaving between fog and meshes (xeokit's depth buffer
    // isn't shareable across contexts).
    const volumes = new Map();   // layer name -> {tex, dims, aabb, steps}
    let volMode = 0;             // 0 = MIP, 1 = fog (shaded compositing)
    let volWin = 1.0, volLevel = 0.5;
    let volNeedsDraw = false;
    const volDirty = () => { volNeedsDraw = true; };

    const volGL = (() => {
        const c = document.getElementById('vol');
        const gl = c.getContext('webgl2', { alpha: true, antialias: false,
                                            premultipliedAlpha: false });
        if (!gl) return null;
        const VS = `#version 300 es
        out vec2 vUV;
        void main() {
            vUV = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
            gl_Position = vec4(vUV * 2.0 - 1.0, 0.0, 1.0);
        }`;
        const FS = `#version 300 es
        precision highp float; precision highp sampler3D;
        uniform sampler3D uVol;
        uniform vec3 uCamPos, uRight, uUp, uFwd, uBoxMin, uBoxMax;
        uniform float uTanFov, uAspect, uWindow, uLevel;
        uniform int uMode, uSteps, uCmapId, uClipOn;
        uniform vec3 uClipPos, uClipDir;
        in vec2 vUV;
        out vec4 outColor;
        float vol(vec3 p) {
            return texture(uVol, (p - uBoxMin) / (uBoxMax - uBoxMin)).r;
        }
        bool clipped(vec3 p) {   // same side convention as xeokit's planes:
            // geometry survives on the side the plane's arrow points at
            return uClipOn == 1 && dot(p - uClipPos, uClipDir) < 0.0;
        }
        float wl(float v) {
            return clamp((v - (uLevel - uWindow * 0.5)) / uWindow, 0.0, 1.0);
        }
        vec3 cmap(float v) {
            if (uCmapId == 0) return vec3(v);          // gray (napari-like)
            if (uCmapId == 2) {                        // viridis approx
                vec3 a = mix(vec3(0.267, 0.005, 0.329),
                             vec3(0.128, 0.567, 0.551), clamp(v * 2.0, 0.0, 1.0));
                return mix(a, vec3(0.993, 0.906, 0.144),
                           clamp(v * 2.0 - 1.0, 0.0, 1.0));
            }
            // hot: black -> red -> yellow -> white
            return clamp(vec3(v * 3.0, v * 3.0 - 1.0, v * 3.0 - 2.0),
                         0.0, 1.0);
        }
        void main() {
            vec2 ndc = vUV * 2.0 - 1.0;
            vec3 dir = normalize(uFwd + uRight * ndc.x * uTanFov * uAspect
                                      + uUp * ndc.y * uTanFov);
            vec3 inv = 1.0 / dir;
            vec3 ta = (uBoxMin - uCamPos) * inv;
            vec3 tb = (uBoxMax - uCamPos) * inv;
            vec3 tmin = min(ta, tb), tmax = max(ta, tb);
            float t0 = max(max(tmin.x, tmin.y), max(tmin.z, 0.0));
            float t1 = min(min(tmax.x, tmax.y), tmax.z);
            if (t1 <= t0) { outColor = vec4(0.0); return; }
            float dt = (t1 - t0) / float(uSteps);
            if (uMode == 0) {                     // MIP
                float m = 0.0;
                for (int i = 0; i < 2048; i++) {
                    if (i >= uSteps) break;
                    vec3 p = uCamPos + dir * (t0 + (float(i) + 0.5) * dt);
                    if (clipped(p)) continue;
                    m = max(m, vol(p));
                }
                float v = wl(m);
                outColor = vec4(cmap(v), v * 0.92);
                return;
            }
            // fog: exponential extinction + gradient-lit diffuse
            vec3 vox = (uBoxMax - uBoxMin)
                     / vec3(textureSize(uVol, 0));
            vec3 acc = vec3(0.0);
            float T = 1.0;
            vec3 L = normalize(vec3(0.5, 0.8, 0.6));
            float density = 400.0 / length(uBoxMax - uBoxMin);
            for (int i = 0; i < 2048; i++) {
                if (i >= uSteps) break;
                vec3 p = uCamPos + dir * (t0 + (float(i) + 0.5) * dt);
                if (clipped(p)) continue;
                float v = wl(vol(p));
                if (v < 0.004) continue;
                float a = 1.0 - exp(-v * v * density * dt);
                vec3 g = vec3(
                    wl(vol(p + vec3(vox.x, 0, 0))) - wl(vol(p - vec3(vox.x, 0, 0))),
                    wl(vol(p + vec3(0, vox.y, 0))) - wl(vol(p - vec3(0, vox.y, 0))),
                    wl(vol(p + vec3(0, 0, vox.z))) - wl(vol(p - vec3(0, 0, vox.z))));
                float gm = length(g);
                float diff = gm > 1e-4
                    ? 0.35 + 0.65 * max(dot(-g / gm, L), 0.0) : 1.0;
                acc += T * a * cmap(v) * diff;
                T *= 1.0 - a;
                if (T < 0.01) break;
            }
            outColor = vec4(acc, 1.0 - T);
        }`;
        function sh(type, src) {
            const s = gl.createShader(type);
            gl.shaderSource(s, src);
            gl.compileShader(s);
            if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
                throw new Error(gl.getShaderInfoLog(s));
            return s;
        }
        const prog = gl.createProgram();
        gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS));
        gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS));
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
            throw new Error(gl.getProgramInfoLog(prog));
        const U = {};
        for (const n of ["uVol", "uCamPos", "uRight", "uUp", "uFwd",
                         "uBoxMin", "uBoxMax", "uTanFov", "uAspect",
                         "uWindow", "uLevel", "uMode", "uSteps", "uCmapId",
                         "uClipOn", "uClipPos", "uClipDir"])
            U[n] = gl.getUniformLocation(prog, n);
        return { gl, c, prog, U };
    })();

    function drawVolumes() {
        const { gl, c, prog, U } = volGL;
        const w = c.clientWidth, h = c.clientHeight;
        if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
        gl.viewport(0, 0, c.width, c.height);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        const vis = [...volumes.entries()].filter(
            ([n]) => !(hiddenGroups[n + "/volume"] || layerHidden[n]));
        if (!vis.length) return;
        const cam = viewer.camera;
        const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
        const crs = (a, b) => [a[1] * b[2] - a[2] * b[1],
                               a[2] * b[0] - a[0] * b[2],
                               a[0] * b[1] - a[1] * b[0]];
        const nrm = (a) => {
            const l = Math.hypot(a[0], a[1], a[2]) || 1;
            return [a[0] / l, a[1] / l, a[2] / l];
        };
        const fwd = nrm(sub(cam.look, cam.eye));
        const right = nrm(crs(fwd, cam.up));
        const up2 = crs(right, fwd);
        gl.useProgram(prog);
        gl.enable(gl.BLEND);
        gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA,
                             gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        gl.uniform3fv(U.uCamPos, cam.eye);
        gl.uniform3fv(U.uRight, right);
        gl.uniform3fv(U.uUp, up2);
        gl.uniform3fv(U.uFwd, fwd);
        gl.uniform1f(U.uTanFov,
            Math.tan(cam.perspective.fov / 2 * Math.PI / 180));
        gl.uniform1f(U.uAspect, c.width / Math.max(c.height, 1));
        gl.uniform1f(U.uWindow, volWin);
        gl.uniform1f(U.uLevel, volLevel);
        gl.uniform1i(U.uMode, volMode);
        gl.uniform1i(U.uVol, 0);
        // the panel's Section plane cuts the volume too (first active one)
        let clip = null;
        for (const id in viewer.scene.sectionPlanes) {
            const sp = viewer.scene.sectionPlanes[id];
            if (sp.active !== false) { clip = sp; break; }
        }
        gl.uniform1i(U.uClipOn, clip ? 1 : 0);
        if (clip) {
            gl.uniform3fv(U.uClipPos, clip.pos);
            gl.uniform3fv(U.uClipDir, clip.dir);
        }
        gl.activeTexture(gl.TEXTURE0);
        for (const [, V] of vis) {
            gl.bindTexture(gl.TEXTURE_3D, V.tex);
            gl.uniform3fv(U.uBoxMin, V.aabb.slice(0, 3));
            gl.uniform3fv(U.uBoxMax, V.aabb.slice(3, 6));
            gl.uniform1i(U.uSteps, V.steps);
            gl.uniform1i(U.uCmapId, V.cmap || 0);
            gl.drawArrays(gl.TRIANGLES, 0, 3);
        }
        gl.disable(gl.BLEND);
    }
    (function volLoop() {
        if (volGL && volNeedsDraw) { volNeedsDraw = false; drawVolumes(); }
        requestAnimationFrame(volLoop);
    })();
    if (volGL) {
        viewer.camera.on("matrix", volDirty);
        new ResizeObserver(volDirty).observe(volGL.c);
        // re-march when a section plane appears, moves, or goes away
        viewer.scene.on("sectionPlaneCreated", (sp) => {
            volDirty();
            sp.on("pos", volDirty);
            sp.on("dir", volDirty);
            sp.on("active", volDirty);
        });
        viewer.scene.on("sectionPlaneDestroyed", volDirty);
    }

    function loadVolume(lname, buf) {
        // "DVV2" + u32le nx,ny,nz + f32le spacing xyz + f32le origin xyz
        // + uint8 voxels (x-fastest), world units = the model's (meters).
        if (!volGL) { status.innerText = "VOLUME NEEDS WEBGL2"; return; }
        const dv = new DataView(buf);
        const nx = dv.getUint32(4, true), ny = dv.getUint32(8, true),
              nz = dv.getUint32(12, true);
        const s = [dv.getFloat32(16, true), dv.getFloat32(20, true),
                   dv.getFloat32(24, true)];
        const o = [dv.getFloat32(28, true), dv.getFloat32(32, true),
                   dv.getFloat32(36, true)];
        const cmapId = dv.getUint8(40);   // 0 gray, 1 hot, 2 viridis
        const vox = new Uint8Array(buf, 44, nx * ny * nz);
        const gl = volGL.gl;
        let V = volumes.get(lname);
        if (!V) {
            V = { tex: gl.createTexture() };
            volumes.set(lname, V);
        }
        const L = layers.get(lname);   // a layer is one thing at a time
        if (L && L.model) {
            dropVtxCache(L.model);
            L.model.destroy();
            layers.delete(lname);
        }
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_3D, V.tex);
        gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
        gl.texImage3D(gl.TEXTURE_3D, 0, gl.R8, nx, ny, nz, 0, gl.RED,
                      gl.UNSIGNED_BYTE, vox);
        for (const [p, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR],
                              [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
                              [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE],
                              [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE],
                              [gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE]])
            gl.texParameteri(gl.TEXTURE_3D, p, v);
        V.dims = [nx, ny, nz];
        V.aabb = [o[0], o[1], o[2],
                  o[0] + nx * s[0], o[1] + ny * s[1], o[2] + nz * s[2]];
        V.steps = Math.min(2 * Math.max(nx, ny, nz), 512);
        V.cmap = cmapId;
        document.getElementById('btnVolMode').style.display = '';
        document.getElementById('wl').style.display = 'block';
        status.innerText = "RENDER COMPLETE";
        if (!loadedAny) {
            loadedAny = true;
            viewer.cameraFlight.jumpTo({ aabb: V.aabb });
        }
        refreshClip();
        buildItems();
        volDirty();
    }

    function volChrome() {
        // hide the volume toolbar bits again when the last volume goes
        if (!volumes.size) {
            document.getElementById('btnVolMode').style.display = 'none';
            document.getElementById('wl').style.display = 'none';
        }
    }

    function unionAabb() {
        let a = viewer.scene.objectIds.length
            ? Array.from(viewer.scene.aabb) : null;
        for (const V of volumes.values()) {
            if (!a) a = V.aabb.slice();
            else for (let i = 0; i < 3; i++) {
                a[i] = Math.min(a[i], V.aabb[i]);
                a[i + 3] = Math.max(a[i + 3], V.aabb[i + 3]);
            }
        }
        return a;
    }

    function applyLook() {
        // Per-entity display state resets with each load — reapply the toggles.
        const scene = viewer.scene;
        scene.setObjectsXRayed(scene.objectIds, xrayed);
        for (const id of scene.objectIds) {
            const o = scene.objects[id];
            if (o) o.edges = edgesOn;
        }
    }

    let clipped = null;   // {D, diag} the current ortho bracket was built for
    function refreshClip() {
        // Clip planes sized to the situation. Perspective: model-scaled (a mm
        // part in meters sits inside any fixed near). Telephoto ortho: tight
        // around the pulled-back camera distance — a model-scaled near plane
        // 32x away compresses the model into a coarse depth slice and the
        // edge overlay z-fights the faces (edges drop out).
        const uab = unionAabb();
        if (!uab) return;
        const diag = Math.max(math.getAABB3Diag(uab), 1e-6);
        const camera = viewer.camera;
        if (orthoMode) {
            const D = math.lenVec3(math.subVec3(camera.look, camera.eye, []));
            if (clipped && clipped.diag === diag
                    && Math.abs(D - clipped.D) < diag * 1e-4) return;
            clipped = { D, diag };
            camera.perspective.near = Math.max(D - diag * 2, D * 0.2);
            camera.perspective.far  = D + diag * 4;
        } else {
            clipped = null;
            camera.perspective.near = diag / 1000;
            camera.perspective.far  = diag * 1000;
        }
    }

    // The bracket rides the camera CONTINUOUSLY (any dolly, flight, or
    // NavCube snap changes the distance it was built around). Widening the
    // clips for a flight instead is not an option: the wide range at
    // telephoto distance is exactly the coarse depth that drops the edges,
    // so the whole ride would flicker. The change-guard above keeps the
    // per-frame cost at a subtraction while the distance holds (orbit, pan).
    viewer.camera.on("matrix", () => { if (orthoMode) refreshClip(); });

    {   // Hover inspect + click pick. Hovering reads what's under the cursor
        // — the entity (glTF node) name and the vertex-snapped coordinate,
        // plus the point's index on a point cloud ("#4172"). A click (a
        // press that doesn't turn into an orbit drag) sends the same info
        // to Python as an {event:'pick'} message.
        const cvs = document.getElementById('xk');
        const tip = document.getElementById('tip');
        const fmt = (v) => Number(v.toPrecision(6));

        function isPointCloud(ent) {
            const m = ent.meshes && ent.meshes[0];
            return !!(m && m.layer && m.layer.primitive === "points");
        }
        function pointIndex(ent, p) {
            // Nearest cached vertex to the snapped position — exact on a
            // snap hit. readableGeometryEnabled keeps positions CPU-side;
            // cache them per entity (cleared on each model load).
            let c = vtxCache[ent.id];
            if (!c) {
                if (typeof ent.getEachVertex !== "function") return null;
                const arr = [];
                ent.getEachVertex((v) => arr.push(v[0], v[1], v[2]));
                c = vtxCache[ent.id] = new Float64Array(arr);
            }
            let best = null, bd = Infinity;
            for (let i = 0; i < c.length; i += 3) {
                const dx = c[i] - p[0], dy = c[i+1] - p[1], dz = c[i+2] - p[2];
                const d = dx*dx + dy*dy + dz*dz;
                if (d < bd) { bd = d; best = i / 3; }
            }
            return best;
        }
        function inspect(canvasPos) {
            const r = viewer.scene.pick({ canvasPos, snapToVertex: true,
                                          snapRadius: 30 });
            const p = r && (r.snappedWorldPos || r.worldPos);
            if (!p) return null;
            const ent = r.entity || null;
            // Globalized entity ids are "<modelId>#<node>", model ids are
            // "m<seq>:<layer>" — split back into (layer, node) for display
            // and for the pick payload.
            let node = null, lname = null;
            if (ent) {
                const hash = ent.id.indexOf("#");
                node = hash >= 0 ? ent.id.slice(hash + 1) : ent.id;
                const mid = hash >= 0 ? ent.id.slice(0, hash) : "";
                lname = mid.slice(mid.indexOf(":") + 1) || null;
            }
            const info = { id: node, layer: lname, point: null,
                           pos: [fmt(p[0]), fmt(p[1]), fmt(p[2])] };
            if (ent && isPointCloud(ent)) info.point = pointIndex(ent, p);
            return info;
        }
        function label(info) {
            // A "base/xx" node is a color_by bucket: show the base name and
            // hide the bucket-local index (Python's pick event translates
            // it to the caller's index; the raw one would just mislead).
            const bucketed = info.id !== null && info.id.includes("/");
            const base = bucketed ? info.id.split("/")[0] : info.id;
            const name = base !== null
                ? (layers.size > 1 && info.layer && info.layer !== base
                       ? info.layer + ":" + base : base)
                : "";
            const head = name + (info.point !== null && !bucketed
                                     ? " #" + info.point : "");
            const xyz = "(" + info.pos.join(", ") + ")";
            return head ? head + "\\n" + xyz : xyz;
        }

        let lastHover = 0;
        cvs.addEventListener('mousemove', (e) => {
            // Quiet during drags and while a tool owns the pointer (the
            // measure/angle controls run their own lens; the section gizmo
            // disables cameraControl.pointerEnabled).
            if (e.buttons || measuring || angling
                    || !viewer.cameraControl.pointerEnabled || !anyLoaded()) {
                tip.style.display = 'none';
                return;
            }
            const now = performance.now();
            if (now - lastHover < 40) return;   // ~25Hz is plenty
            lastHover = now;
            const info = inspect([e.offsetX, e.offsetY]);
            if (!info) { tip.style.display = 'none'; return; }
            tip.innerText = label(info);
            tip.style.left = (e.offsetX + 14) + 'px';
            tip.style.top = (e.offsetY + 14) + 'px';
            tip.style.display = 'block';
        });
        cvs.addEventListener('mouseleave', () => {
            tip.style.display = 'none';
        });

        let downX = 0, downY = 0, armed = false;
        cvs.addEventListener('mousedown', (e) => {
            armed = e.button === 0 && !measuring && !angling
                    && viewer.cameraControl.pointerEnabled;
            downX = e.clientX; downY = e.clientY;
        });
        cvs.addEventListener('mouseup', (e) => {
            if (!armed || e.button !== 0 || !anyLoaded()) return;
            armed = false;
            if (Math.abs(e.clientX - downX) > 4
                    || Math.abs(e.clientY - downY) > 4) return;   // a drag
            const info = inspect([e.offsetX, e.offsetY]);
            if (info) canvas.send({ event: 'pick', ...info });
        });
    }

    function prepGLB(buf) {
        // Per-layer GLB analysis + fixes between the wire and xeokit's
        // loader; returns {buf, groups, colors, counts}:
        //  * colors: the glTF parser never reads the COLOR_0 vertex
        //    attribute (what trimesh & friends write for point clouds and
        //    line sets), and its points layer ignores material color too —
        //    so record each named node's average vertex color and tint the
        //    loaded entity (colorize) instead. Flat per-node, no gradient.
        //  * indexless LINES get sequential indices appended to the BIN
        //    chunk: the loader requires indices for "lines" and drops the
        //    whole primitive otherwise (trimesh exports lines indexless).
        //  * groups: where the node tree first branches — the Items
        //    panel's show/hide rows. counts: points/segments per node.
        try {
            const dv = new DataView(buf);
            if (dv.getUint32(0, true) !== 0x46546C67) {   // ≠"glTF"
                return { buf, groups: [], colors: {}, counts: {} };
            }
            const jlen = dv.getUint32(12, true);
            const json = JSON.parse(new TextDecoder().decode(
                new Uint8Array(buf, 20, jlen)));
            const binOff = 20 + jlen;   // chunk-1 header, if present
            const binLen = binOff + 8 <= buf.byteLength
                ? dv.getUint32(binOff, true) : 0;
            const bin = binLen ? new Uint8Array(buf, binOff + 8, binLen) : null;

            const nodes = json.nodes || [];
            const scn = (json.scenes || [])[json.scene || 0] || { nodes: [] };
            let tops = scn.nodes || [];
            while (tops.length === 1
                   && (nodes[tops[0]].children || []).length)
                tops = nodes[tops[0]].children;
            // Sibling nodes named "base/xx" (color_by buckets, isosurface
            // levels) fold into one group "base" — one Items row.
            const folded = new Map();
            for (const i of tops) {
                const names = [];
                (function walk(k) {
                    if (nodes[k].name) names.push(nodes[k].name);
                    (nodes[k].children || []).forEach(walk);
                })(i);
                const raw = nodes[i].name || "node " + i;
                const base = raw.split("/")[0];
                if (!folded.has(base))
                    folded.set(base, { name: base, nodes: [] });
                folded.get(base).nodes.push(...names);
            }
            const groups = [...folded.values()];

            function avgColor(accIdx) {
                const acc = json.accessors[accIdx];
                const bv = json.bufferViews[acc.bufferView];
                if (!bin || bv.buffer !== 0 || acc.count === 0) return null;
                const ncomp = acc.type === "VEC4" ? 4 : 3;
                const readers = {
                    5121: [1, (d, o) => d.getUint8(o) / 255],
                    5123: [2, (d, o) => d.getUint16(o, true) / 65535],
                    5126: [4, (d, o) => d.getFloat32(o, true)],
                };
                const r = readers[acc.componentType];
                if (!r) return null;
                const [size, get] = r;
                const d = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
                const stride = bv.byteStride || ncomp * size;
                const base = (bv.byteOffset || 0) + (acc.byteOffset || 0);
                const sum = [0, 0, 0, 0];
                for (let i = 0; i < acc.count; i++) {
                    const o = base + i * stride;
                    for (let c = 0; c < ncomp; c++)
                        sum[c] += get(d, o + c * size);
                }
                return [sum[0] / acc.count, sum[1] / acc.count,
                        sum[2] / acc.count,
                        ncomp === 4 ? sum[3] / acc.count : 1];
            }

            const colors = {};
            const counts = {};
            for (const nd of nodes) {
                if (!nd.name || nd.mesh === undefined) continue;
                let count = 0;
                for (const prim of (json.meshes[nd.mesh] || {}).primitives
                                   || []) {
                    if (!prim.attributes) continue;
                    const pc = prim.attributes.POSITION !== undefined
                        ? json.accessors[prim.attributes.POSITION].count : 0;
                    if (prim.mode === 0) count += pc;                // points
                    else if (prim.mode === 1)                       // lines
                        count += Math.floor((prim.indices !== undefined
                            ? json.accessors[prim.indices].count : pc) / 2);
                    if (bin && colors[nd.name] === undefined
                            && prim.attributes.COLOR_0 !== undefined) {
                        const rgba = avgColor(prim.attributes.COLOR_0);
                        if (rgba) colors[nd.name] = rgba;
                    }
                }
                if (count) counts[nd.name] = count;
            }

            let changed = false;
            const binPad = (4 - (binLen % 4)) % 4;
            const appendix = [];        // extra BIN bytes (line indices)
            let apLen = 0;
            for (const mesh of json.meshes || []) {
                for (const prim of mesh.primitives || []) {
                    if (!prim.attributes || !bin) continue;
                    if (prim.mode === 1 && prim.indices === undefined
                            && prim.attributes.POSITION !== undefined) {
                        const count =
                            json.accessors[prim.attributes.POSITION].count;
                        const wide = count > 65535;
                        const idx = wide ? new Uint32Array(count)
                                         : new Uint16Array(count);
                        for (let i = 0; i < count; i++) idx[i] = i;
                        let bytes = new Uint8Array(
                            idx.buffer, 0, idx.byteLength);
                        const pad = (4 - (bytes.length % 4)) % 4;
                        json.bufferViews = json.bufferViews || [];
                        json.bufferViews.push({
                            buffer: 0, byteOffset: binLen + binPad + apLen,
                            byteLength: bytes.length });
                        json.accessors.push({
                            bufferView: json.bufferViews.length - 1,
                            componentType: wide ? 5125 : 5123,
                            count, type: "SCALAR" });
                        prim.indices = json.accessors.length - 1;
                        appendix.push(bytes);
                        if (pad) appendix.push(new Uint8Array(pad));
                        apLen += bytes.length + pad;
                        changed = true;
                    }
                }
            }
            if (!changed) return { buf, groups, colors, counts };
            if (json.buffers && json.buffers[0])
                json.buffers[0].byteLength = binLen + binPad + apLen;

            let jbytes = new TextEncoder().encode(JSON.stringify(json));
            const jpad = (4 - (jbytes.length % 4)) % 4;
            if (jpad) {  // JSON chunks pad with spaces
                const p = new Uint8Array(jbytes.length + jpad).fill(0x20);
                p.set(jbytes);
                jbytes = p;
            }
            const newBinLen = binLen + binPad + apLen;
            const out = new Uint8Array(20 + jbytes.length + 8 + newBinLen);
            const odv = new DataView(out.buffer);
            odv.setUint32(0, 0x46546C67, true);
            odv.setUint32(4, 2, true);
            odv.setUint32(8, out.length, true);
            odv.setUint32(12, jbytes.length, true);
            odv.setUint32(16, 0x4E4F534A, true);        // "JSON"
            out.set(jbytes, 20);
            let off = 20 + jbytes.length;
            odv.setUint32(off, newBinLen, true);
            odv.setUint32(off + 4, 0x004E4942, true);   // "BIN\\0"
            off += 8;
            if (bin) { out.set(bin, off); off += binLen + binPad; }
            for (const a of appendix) { out.set(a, off); off += a.length; }
            return { buf: out.buffer, groups, colors, counts };
        } catch (e) {
            console.warn("model3d: GLB prep skipped:", e);
            return { buf, groups: [], colors: {}, counts: {} };
        }
    }

    function dropVtxCache(model) {
        const pre = model.id + "#";
        for (const k in vtxCache) if (k.startsWith(pre)) delete vtxCache[k];
    }

    function loadLayer(lname, glbBuf) {
        const u8h = new Uint8Array(glbBuf, 0, 4);
        if (u8h[0] === 0x44 && u8h[1] === 0x56 && u8h[2] === 0x56
                && u8h[3] === 0x32) {              // "DVV2": a volume layer
            loadVolume(lname, glbBuf);
            return;
        }
        if (volumes.has(lname)) {                  // GLB replaces a volume
            volumes.delete(lname);
            volChrome();
            volDirty();
        }
        let L = layers.get(lname);
        if (!L) {
            L = { model: null, groups: [], colors: {}, counts: {}, seq: 0 };
            layers.set(lname, L);
        }
        const mySeq = ++L.seq;
        // Measurements/section anchor to entities the reload may destroy.
        distance.clear();
        angle.clear();
        if (sectioning) { sectionPlanes.clear(); sectioning = false; sync(); }
        if (L.model) { dropVtxCache(L.model); L.model.destroy(); L.model = null; }
        document.getElementById('tip').style.display = 'none';
        const prep = prepGLB(glbBuf);
        L.groups = prep.groups;
        L.colors = prep.colors;
        L.counts = prep.counts;
        const src = "m" + (++frameSeq) + ".glb";
        pendingBufs.set(src, prep.buf);
        status.innerText = "LOADING…";
        // globalizeObjectIds: entity ids become "<modelId>#<node>" so the
        // same node name ("points") can live on two layers at once.
        const m = loader.load({ id: "m" + frameSeq + ":" + lname, src,
                                edges: true, globalizeObjectIds: true });
        m.on("loaded", () => {
            if (mySeq !== L.seq) { m.destroy(); return; }  // a newer push won
            L.model = m;
            refreshClip();
            applyLook();
            for (const [n, c] of Object.entries(L.colors)) {
                const o = viewer.scene.objects[m.id + "#" + n];
                if (!o) continue;
                o.colorize = [c[0], c[1], c[2]];
                if (c[3] < 0.999) o.opacity = c[3];
            }
            buildItems();
            status.innerText = "RENDER COMPLETE";
            if (!loadedAny) { loadedAny = true; viewer.cameraFlight.jumpTo(m); }
        });
        m.on("error", (e) => { status.innerText = "LOAD ERROR: " + e; });
    }

    canvas.onPush((data) => {
        // Binary: a layer frame ("DVL1" + u16le name length + name + GLB)
        // or a bare GLB, which addresses the "model" layer. JSON: controls.
        if (data instanceof ArrayBuffer) {
            const u8 = new Uint8Array(data);
            if (u8.length >= 6 && u8[0] === 0x44 && u8[1] === 0x56
                    && u8[2] === 0x4C && u8[3] === 0x31) {      // "DVL1"
                const nlen = u8[4] | (u8[5] << 8);
                const lname = new TextDecoder().decode(
                    u8.subarray(6, 6 + nlen));
                loadLayer(lname, data.slice(6 + nlen));
            } else {
                loadLayer("model", data);
            }
            return;
        }
        if (!data || typeof data !== "object") return;
        if (data.cmd === "visible") {
            layerHidden[data.layer] = !data.on;
            buildItems();
            volDirty();
        } else if (data.cmd === "clear") {
            for (const [lname, L] of [...layers]) {
                if (data.layer != null && lname !== data.layer) continue;
                if (L.model) { dropVtxCache(L.model); L.model.destroy(); }
                layers.delete(lname);
            }
            for (const lname of [...volumes.keys()]) {
                if (data.layer != null && lname !== data.layer) continue;
                volumes.delete(lname);
            }
            volChrome();
            volDirty();
            buildItems();
        }
    });

    const hiddenGroups = {};   // "layer/branch" -> true; survives reloads

    function buildItems() {
        // The Items panel: one show/hide row per layer — or per top-level
        // branch of a layer's node tree when it splits (a single-layer
        // composed GLB reads as before: part / points / lines rows).
        // Checkbox state lives in hiddenGroups (keyed "layer/branch") and
        // Python-set layer visibility in layerHidden; both are re-applied
        // here after every load, so hidden things STAY hidden across
        // pushes. Entities under unnamed nodes land in "(other)".
        const panel = document.getElementById('items');
        panel.innerHTML = '';
        const scene = viewer.scene;
        const rows = [];
        for (const [lname, L] of layers) {
            if (!L.model) continue;
            const mid = L.model.id;
            const claimed = new Set();
            const lrows = [];
            for (const g of L.groups) {
                const ids = g.nodes.map((n) => mid + "#" + n)
                    .filter((i) => scene.objects[i]);
                ids.forEach((i) => claimed.add(i));
                if (ids.length)
                    lrows.push({ branch: g.name, nodes: g.nodes, ids });
            }
            const rest = scene.objectIds.filter(
                (i) => i.startsWith(mid + "#") && !claimed.has(i));
            if (rest.length)
                lrows.push({ branch: lrows.length ? "(other)" : lname,
                             nodes: [], ids: rest });
            for (const r of lrows) {
                const label = lrows.length === 1 ? lname
                    : (layers.size === 1 ? r.branch
                                         : lname + ":" + r.branch);
                rows.push({ key: lname + "/" + r.branch, label, lname,
                            nodes: r.nodes, ids: r.ids, counts: L.counts });
            }
        }
        for (const [lname, V] of volumes) {   // volume layers get rows too
            rows.push({ key: lname + "/volume",
                        label: lname + " (" + V.dims.join("×") + ")",
                        lname, nodes: [], ids: null, counts: {} });
        }
        for (const g of rows) {
            const lab = document.createElement('label');
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            const hidden = !!(hiddenGroups[g.key] || layerHidden[g.lname]);
            cb.checked = !hidden;
            if (g.ids) scene.setObjectsVisible(g.ids, !hidden);
            cb.onchange = () => {
                hiddenGroups[g.key] = !cb.checked;
                if (g.ids)
                    scene.setObjectsVisible(g.ids,
                        cb.checked && !layerHidden[g.lname]);
                else volDirty();
            };
            lab.appendChild(cb);
            // The count: points/segments where the row carries any, else
            // how many entities the checkbox toggles.
            let prims = 0;
            for (const n of g.nodes) prims += g.counts[n] || 0;
            const suffix = prims ? " (" + prims + ")"
                : (g.ids && g.ids.length > 1)
                    ? " (" + g.ids.length + ")" : "";
            lab.appendChild(document.createTextNode(g.label + suffix));
            panel.appendChild(lab);
        }
    }

    const bM = document.getElementById('btnMeasure');
    const bA = document.getElementById('btnAngle');
    const bS = document.getElementById('btnSection');
    const bX = document.getElementById('btnXray');
    const bE = document.getElementById('btnEdges');
    const bO = document.getElementById('btnOrtho');
    function sync() {
        bM.classList.toggle('on', measuring);
        bA.classList.toggle('on', angling);
        bS.classList.toggle('on', sectioning);
        bX.classList.toggle('on', xrayed);
        bE.classList.toggle('on', edgesOn);
        bO.classList.toggle('on', orthoMode);
    }
    bM.onclick = () => {
        measuring = !measuring;
        if (measuring && angling) { angling = false; angleCtrl.deactivate(); }
        measuring ? measureCtrl.activate() : measureCtrl.deactivate();
        sync();
    };
    bA.onclick = () => {
        angling = !angling;
        if (angling && measuring) { measuring = false; measureCtrl.deactivate(); }
        angling ? angleCtrl.activate() : angleCtrl.deactivate();
        sync();
    };
    bS.onclick = () => {
        const uab = unionAabb();   // volumes count: slice a PET cloud too
        if (!uab) return;
        sectioning = !sectioning;
        if (sectioning) {
            const c = math.getAABB3Center(uab);
            const sp = sectionPlanes.createSectionPlane({ id: "sp1", pos: c, dir: [-1, 0, 0] });
            sectionPlanes.showControl(sp.id);
        } else {
            sectionPlanes.clear();
        }
        sync();
    };
    bX.onclick = () => { xrayed = !xrayed; applyLook(); sync(); };
    document.getElementById('btnItems').onclick = () => {
        const panel = document.getElementById('items');
        const show = panel.style.display !== 'block';
        panel.style.display = show ? 'block' : 'none';
        document.getElementById('btnItems').classList.toggle('on', show);
    };
    bE.onclick = () => { edgesOn = !edgesOn; applyLook(); sync(); };
    bO.onclick = () => {
        // "Ortho" is a telephoto perspective (2° fov, camera pulled back to
        // hold the apparent size): real ortho is off the table — this xeokit
        // build stops rendering once ortho.near/far are touched, and a
        // mm-scale part sits inside the default ortho near plane. At 2° the
        // foreshortening across a part is ~2%: visually orthographic, and
        // measurements/sections/clip planes all keep working.
        const camera = viewer.camera;
        const fovNow = camera.perspective.fov;
        const fovTo = orthoMode ? 60 : 2;
        const t = Math.tan((fovNow / 2) * Math.PI / 180)
                / Math.tan((fovTo / 2) * Math.PI / 180);
        const dir = math.normalizeVec3(
            math.subVec3(camera.look, camera.eye, []));
        const dist = math.lenVec3(math.subVec3(camera.look, camera.eye, []));
        camera.eye = [camera.look[0] - dir[0] * dist * t,
                      camera.look[1] - dir[1] * dist * t,
                      camera.look[2] - dir[2] * dist * t];
        camera.perspective.fov = fovTo;
        orthoMode = !orthoMode;
        // Flights frame the model to a fit-FOV, not the live fov — track the
        // telephoto or they park the camera ~30x too close. Two knobs: Fit
        // reads viewer.cameraFlight.fitFOV; a NavCube face-click computes its
        // own eye from the plugin's cameraFitFOV (dist = diag/tan(fitFOV)).
        viewer.cameraFlight.fitFOV = orthoMode ? 1.5 : 45;
        navCube.setCameraFitFOV(orthoMode ? 1.5 : 45);
        refreshClip();
        sync();
    };
    document.getElementById('btnReset').onclick = () => {
        const uab = unionAabb();
        if (uab) viewer.cameraFlight.flyTo({ aabb: uab });
    };
    document.getElementById('btnVolMode').onclick = () => {
        volMode = 1 - volMode;
        document.getElementById('btnVolMode').innerText =
            volMode ? "Fog" : "MIP";
        volDirty();
    };
    {   // right-drag = window/level, live only while a volume is shown
        const cvs = document.getElementById('xk');
        const wl = document.getElementById('wl');
        let wling = false, wx = 0, wy = 0;
        cvs.addEventListener('contextmenu', (e) => {
            if (volumes.size) e.preventDefault();
        });
        cvs.addEventListener('mousedown', (e) => {
            if (e.button === 2 && volumes.size) {
                wling = true; wx = e.clientX; wy = e.clientY;
            }
        });
        document.addEventListener('mouseup', (e) => {
            if (e.button === 2) wling = false;
        });
        document.addEventListener('mousemove', (e) => {
            if (!wling) return;
            volWin = Math.min(2, Math.max(0.01,
                volWin + (e.clientX - wx) * 0.003));
            volLevel = Math.min(1.5, Math.max(-0.5,
                volLevel + (e.clientY - wy) * 0.003));
            wx = e.clientX; wy = e.clientY;
            wl.innerText = `W ${volWin.toFixed(2)} / L ${volLevel.toFixed(2)}`
                + " — right-drag to window";
            volDirty();
        });
    }
    document.getElementById('btnClear').onclick = () => {
        distance.clear();
        angle.clear();
        sectionPlanes.clear();
        measuring = false; angling = false; sectioning = false;
        measureCtrl.deactivate();
        angleCtrl.deactivate();
        sync();
    };

    canvas.send({event: 'ready'});
</script>
"""


class Model3D(Custom):
    """A prebuilt 3D/CAD viewer: GLB in, orbit/measure/section out."""

    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {},
        "updates": {},
        "events": [{"event": "ready"},
                   {"event": "pick",
                    "id": "str|null -- picked entity (glTF node name)",
                    "layer": "str -- the layer the entity lives on",
                    "point": "int|null -- point index within a picked "
                             "point-cloud entity",
                    "pos": "[x,y,z] -- picked world coordinate, snapped to "
                           "the nearest vertex when one is in range"}],
        "binary": "receives CUSTOM (code 3): either a raw glTF-Binary (GLB)"
                  " — replaces the 'model' layer — or a layer frame: "
                  "b'DVL1' + u16le(name length) + UTF-8 layer name + "
                  "payload, replacing that named layer only. Payload is a "
                  "GLB (geometry) or b'DVV2' + u32le nx,ny,nz + f32le "
                  "spacing xyz + f32le origin xyz + u8 cmap (0 gray/1 hot/"
                  "2 viridis) + 3 pad + uint8 voxels x-fastest (a "
                  "ray-marched volume fused into the scene)",
        "controls": "JSON via the update channel's `post` (Custom.push): "
                    "{cmd:'visible', layer, on} shows/hides a layer; "
                    "{cmd:'clear', layer|null} removes one layer or all",
        "note": "the viewer (xeokit) loads from its CDN; the browser needs "
                "network access to cdn.jsdelivr.net on first render",
    }

    default_w = 800
    default_h = 600

    def __init__(self, name="model3d", label=None, color=None):
        super().__init__(html=_VIEWER_HTML, name=name, label=label,
                         # the wheel zooms the model, not the canvas
                         forward_wheel=False)
        self._init_color(color)
        # Layer state: name -> {"glb": bytes|None, "visible": bool}. Binary
        # streams aren't replayed by the hub, so every layer's latest GLB
        # (and its visibility) is held here and re-pushed whenever a viewer
        # mounts and says so.
        self._layers = {}
        self._handles = {}
        self.on("ready")(lambda _msg: self._repush())

    def _repush(self):
        for lname, st in self._layers.items():
            if st.get("glb") is not None:
                self.push_binary(_layer_frame(lname, st["glb"]))
            if not st.get("visible", True):
                self.push({"cmd": "visible", "layer": lname, "on": False})

    def _layer_state(self, lname):
        return self._layers.setdefault(lname, {"glb": None, "visible": True})

    def layer(self, lname):
        """The named layer's handle — an independently updatable slice of
        the scene (its own Items row, its own lifecycle)::

            viewer.layer("part").update(glb, mesh_color=(110, 150, 220, 255))
            viewer.layer("cloud").points(pts)          # part stays put
            viewer.layer("path").curve(samples)
            viewer.layer("cloud").visible = False
            viewer.layer("path").clear()
        """
        if lname not in self._handles:
            self._handles[lname] = Model3DLayer(self, lname)
        return self._handles[lname]

    def clear(self, lname=None):
        """Remove one layer (``clear("cloud")``) or the whole scene."""
        if lname is None:
            self._layers.clear()
        else:
            self._layers.pop(lname, None)
        self.push({"cmd": "clear", "layer": lname})

    @property
    def model(self):
        """The default ("model") layer's GLB bytes (``None`` before the
        first update); assign to show a new model (same as ``update``)."""
        return self._layers.get("model", {}).get("glb")

    @model.setter
    def model(self, source):
        self.update(source)

    def update(self, source=None, *, layer="model", points=None, lines=None,
               curve=None, point_color=None, line_color=None,
               curve_color=None, mesh_color=None):
        """Show a model and/or overlays (composed into one GLB, pushed to
        ``layer`` — the default layer unless named).

        ``source``: GLB ``bytes``, a ``.glb``/``.gltf`` path, or a trimesh
        object (anything with ``export(file_type="glb")``). Overlays and
        colors as in :func:`compose_glb`::

            viewer.update(part_glb, points=pts, curve=helix,
                          mesh_color=(110, 150, 220, 255))
        """
        if (source is not None and points is None and lines is None
                and curve is None and mesh_color is None):
            # Nothing to compose — ship the bytes opaquely (no reparse).
            glb = _coerce_glb(source)
        else:
            glb = compose_glb(source, points=points, lines=lines,
                              curve=curve, point_color=point_color,
                              line_color=line_color,
                              curve_color=curve_color,
                              mesh_color=mesh_color)
        self._push_composed(layer, glb)

    def _push_composed(self, layer, glb, buckets=None, values=None):
        st = self._layer_state(layer)
        st["glb"] = glb
        # color_by bookkeeping: bucket node -> original point indices, so
        # picks report indices into the caller's arrays (see _handle_input).
        st["buckets"] = buckets
        st["values"] = values
        self.push_binary(_layer_frame(layer, glb))

    def _handle_input(self, payload, viewer=None):
        # A pick on a color_by-bucketed node carries the node's local point
        # index — translate it back to the caller's index (and value), and
        # collapse the "base/bucket" node name to its base.
        try:
            if isinstance(payload, dict) and payload.get("event") == "pick":
                st = self._layers.get(payload.get("layer") or "", {})
                maps = st.get("buckets") or {}
                node = payload.get("id")
                if node in maps and payload.get("point") is not None:
                    orig = int(maps[node][payload["point"]])
                    payload["point"] = orig
                    payload["id"] = node.split("/")[0]
                    if st.get("values") is not None:
                        payload["value"] = float(st["values"][orig])
        except Exception:
            pass
        super()._handle_input(payload, viewer)

    def on_pick(self, fn):
        """Register a handler for clicks on the model (``@viewer.on_pick``).

        The handler receives ``{"event": "pick", "id", "layer", "point",
        "pos"}``: ``id`` is the glTF node name of what was hit (name your
        geometries — e.g. ``scene.add_geometry(mesh, node_name="housing")``
        in trimesh — and that name comes back here), ``layer`` the layer it
        lives on, ``pos`` the world coordinate snapped to the nearest
        vertex, and on a point cloud ``point`` is the index of that point in
        the order the positions were supplied (``None`` elsewhere). Hovering
        shows the same readout as a tooltip in the viewer without involving
        Python. Sugar for ``on("pick")``.
        """
        return self.on("pick")(fn)


class Model3DLayer:
    """A named, independently updatable slice of a Model3D scene.

    Obtained via ``viewer.layer(name)``. Each layer holds one GLB; pushing
    to a layer replaces only that layer — the others stay put, which is
    what makes per-frame animation of one overlay (or a slider-driven part
    rebuild that leaves a million-point cloud alone) cheap.
    """

    def __init__(self, parent, name):
        self._parent = parent
        self._name = name

    def update(self, source=None, **kwargs):
        """Replace this layer's content — same signature as
        :meth:`Model3D.update` (minus ``layer``)."""
        self._parent.update(source, layer=self._name, **kwargs)

    def points(self, pts, color=None, color_by=None, cmap=None, buckets=16):
        """Show an (N, 3) point cloud as this layer's content.

        ``color_by=`` value-colors the cloud: an array of N values is
        banded into ``buckets`` color bands over ``cmap`` (built-in viridis
        by default; pass RGB(A) stops or a callable t→RGBA). Picks on a
        value-colored cloud still report the index into YOUR array (plus
        the point's ``value``).
        """
        if color_by is None:
            self.update(points=pts, point_color=color)
            return
        import numpy as np
        pts = np.asarray(pts, float).reshape(-1, 3)
        v = np.asarray(color_by, float).ravel()
        idx, centers = _bucketize(v, buckets)
        nodes, maps = [], {}
        for b in range(buckets):
            m = idx == b
            if not m.any():
                continue
            name = f"points/{b:02d}"
            nodes.append({"name": name, "mode": 0, "verts": pts[m],
                          "rgba": _cmap_rgba(centers[b], cmap)})
            maps[name] = np.where(m)[0]
        self._parent._push_composed(self._name, compose_glb(_nodes=nodes),
                                    buckets=maps, values=v)

    def lines(self, segments, color=None):
        """Show (M, 2, 3) segments — endpoint pairs — as this layer."""
        self.update(lines=segments, line_color=color)

    def curve(self, samples, color=None):
        """Show a (K, 3)-sampled function as a connected polyline."""
        self.update(curve=samples, curve_color=color)

    def vectors(self, origins, vecs, scale=1.0, color=None, color_by=None,
                cmap=None, buckets=16):
        """Show a vector field: a segment from each of the (N, 3)
        ``origins`` along ``vecs * scale``.

        ``color_by="magnitude"`` (or an array of N values) colors the
        arrows by value over ``cmap``, banded into ``buckets``.
        """
        import numpy as np
        o = np.asarray(origins, float).reshape(-1, 3)
        d = np.asarray(vecs, float).reshape(-1, 3) * scale
        segs = np.stack([o, o + d], axis=1)          # (N, 2, 3)
        if color_by is None:
            self.update(lines=segs, line_color=color)
            return
        v = (np.linalg.norm(d, axis=1)
             if isinstance(color_by, str) and color_by == "magnitude"
             else np.asarray(color_by, float).ravel())
        idx, centers = _bucketize(v, buckets)
        nodes = []
        for b in range(buckets):
            m = idx == b
            if not m.any():
                continue
            nodes.append({"name": f"lines/{b:02d}", "mode": 1,
                          "verts": segs[m].reshape(-1, 3),
                          "rgba": _cmap_rgba(centers[b], cmap)})
        self._parent._push_composed(self._name, compose_glb(_nodes=nodes))

    def volume(self, volume, spacing=(1.0, 1.0, 1.0), origin=(0, 0, 0),
               vmin=None, vmax=None, cmap="hot"):
        """Show a 3D array as a TRUE volume rendering fused into the scene
        (GPU ray marching on an overlay locked to the panel camera) — for
        PET/CT recons and density fields living in the same space as the
        model.

        ``volume`` is any 3D array indexed ``[x, y, z]``; values are
        windowed to ``vmin``..``vmax`` (data min/max by default) and
        quantized to uint8. ``spacing`` is the per-axis voxel size and
        ``origin`` the position of voxel (0,0,0)'s corner — both in the
        model's world units (glTF = meters), so a PET recon with 2mm
        voxels is ``spacing=(0.002, 0.002, 0.0028)``.

        In the panel: MIP by default (a "MIP"/"Fog" toolbar button appears
        with a volume in the scene, Fog = shaded alpha compositing);
        right-drag adjusts window/level. ``cmap`` is ``"gray"``, ``"hot"``
        or ``"viridis"``. The volume composites OVER the geometry — dim
        regions are transparent, so models show through; there is no depth
        interleaving between fog and meshes.
        """
        import numpy as np
        vol = np.asarray(volume)
        if vol.ndim != 3:
            raise ValueError(f"expected a 3D array, got shape {vol.shape}")
        lo = float(vol.min()) if vmin is None else float(vmin)
        hi = float(vol.max()) if vmax is None else float(vmax)
        u8 = np.clip((vol.astype(float) - lo) / max(hi - lo, 1e-12) * 255,
                     0, 255).astype(np.uint8)
        cmaps = {"gray": 0, "hot": 1, "viridis": 2}
        if cmap not in cmaps:
            raise ValueError(f"cmap must be one of {sorted(cmaps)}")
        frame = (b"DVV2"
                 + struct.pack("<IIIffffff", *vol.shape,
                               *(float(s) for s in spacing),
                               *(float(o) for o in origin))
                 + bytes([cmaps[cmap], 0, 0, 0])
                 + np.ascontiguousarray(u8.transpose(2, 1, 0)).tobytes())
        self._parent._push_composed(self._name, frame)

    def voxels(self, grid, pitch=1.0, origin=(0, 0, 0), color=None,
               bands=8):
        """Show a voxel body (exposed-face box mesh).

        ``grid`` is 3D: **boolean/int** = occupancy (solid voxels in
        ``color``); **float** = per-voxel OPACITY — values are clipped to
        [0, 1] and a cell's value scales its alpha (0 = absent), banded
        into ``bands`` opacity groups. ``color`` supplies the hue either
        way (its own alpha scales the whole body).

        Cell (i,j,k) spans ``origin + [i, i+1] * pitch`` per axis
        (``pitch`` may be a scalar or per-axis triple). Interior faces
        between same-band neighbors are culled, so a solid 100-cube costs
        ~60k faces, not a million — but a smooth float gradient exposes
        every band boundary, so keep float grids modest.
        """
        import numpy as np
        g = np.asarray(grid)
        rgba = _rgba(color, MESH_COLOR)
        if not np.issubdtype(g.dtype, np.floating):
            verts, normals, indices = _box_surface(g, pitch, origin)
            self._parent._push_composed(self._name, compose_glb(_nodes=[{
                "name": "voxels", "mode": 4, "verts": verts,
                "normals": normals, "indices": indices, "rgba": rgba}]))
            return
        v = np.clip(g, 0.0, 1.0)
        rank = np.where(v > 0,
                        np.minimum((v * bands).astype(int), bands - 1), -1)
        surf = _rank_surfaces(rank, pitch, origin)
        if not surf:
            raise ValueError("float voxel grid has no cells with value > 0")
        nodes = [{"name": f"voxels/{b:02d}", "mode": 4, "verts": vs,
                  "normals": ns, "indices": ix,
                  "rgba": (*rgba[:3],
                           int(round((b + 0.5) / bands * rgba[3])))}
                 for b, (vs, ns, ix) in sorted(surf.items())]
        self._parent._push_composed(self._name, compose_glb(_nodes=nodes))

    def isosurface(self, field, level=None, levels=None, spacing=1.0,
                   origin=(0, 0, 0), color=None, cmap=None):
        """Show isosurface(s) of a 3D scalar field (pure-numpy surface
        nets, gradient-shaded).

        One ``level`` or a list of ``levels`` — multiple levels render as
        one Items row, colored along ``cmap`` (or all ``color``). Sample
        (i,j,k) sits at ``origin + (i,j,k) * spacing``. For per-level
        show/hide, push each level as its own layer instead.
        """
        lvls = [level] if level is not None else list(levels or [])
        if not lvls:
            raise ValueError("isosurface needs level= or levels=[...]")
        lo, hi = min(lvls), max(lvls)
        nodes = []
        for lv in lvls:
            verts, normals, indices = _surface_net(field, lv, spacing,
                                                   origin)
            t = 0.5 if hi <= lo else (lv - lo) / (hi - lo)
            nodes.append({"name": f"iso/{lv:g}", "mode": 4, "verts": verts,
                          "normals": normals, "indices": indices,
                          "rgba": (_rgba(color) if color is not None
                                   else _cmap_rgba(t, cmap))})
        self._parent._push_composed(self._name, compose_glb(_nodes=nodes))

    def clear(self):
        """Remove this layer from the scene."""
        self._parent.clear(self._name)

    @property
    def glb(self):
        """This layer's current GLB bytes (``None`` if never pushed)."""
        return self._parent._layers.get(self._name, {}).get("glb")

    @property
    def visible(self):
        return self._parent._layer_state(self._name).get("visible", True)

    @visible.setter
    def visible(self, on):
        self._parent._layer_state(self._name)["visible"] = bool(on)
        self._parent.push({"cmd": "visible", "layer": self._name,
                           "on": bool(on)})

