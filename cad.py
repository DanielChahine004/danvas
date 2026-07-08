import time

import danvas
import numpy as np
import trimesh
from build123d import *

RED = (255, 40, 40, 255)
GREEN = (0, 200, 80, 255)
STEEL = (110, 150, 220, 255)
ORANGE = (255, 170, 0, 255)


def sample_inside(mesh, count, rng):
    """`count` points inside `mesh`, fast at any count: voxelize + fill
    once, then jitter random filled voxels. (Rejection sampling with
    per-point containment tests takes ~40s for 60k points; this is <1s.)

    Only INTERIOR voxels are jittered — the filled grid is eroded by one
    cell first, because a surface voxel's cube straddles the boundary and
    jittering it scatters points visibly outside the part."""
    from scipy.ndimage import binary_erosion
    pitch = mesh.extents.max() / 100
    vox = mesh.voxelized(pitch).fill()
    interior = binary_erosion(vox.matrix, structure=np.ones((3, 3, 3)))
    if not interior.any():          # wall thinner than 3 voxels: keep shell
        interior = vox.matrix
    centers = vox.indices_to_points(np.argwhere(interior))
    idx = rng.integers(0, len(centers), count)
    return centers[idx] + rng.uniform(-pitch / 2, pitch / 2, (count, 3))


canvas = danvas.Canvas()
sides_slider = canvas.slider("SIDES", min=3, max=120, step=1, default=6)
radius_slider = canvas.slider("HOLE_RADIUS", min=1, max=19, default=5, below=sides_slider)
trace_button = canvas.button("TRACE", text="▶ trace path", below=radius_slider)

viewer = canvas.model3d("part", w=800, left_of=sides_slider)

# The helix the TRACE button animates (kept so it needn't be recomputed).
helix = None

# @canvas.on_edit: edit this function's body, SAVE, and the canvas updates —
# no button, no restart; the viewer camera and process state stay put.
@canvas.on_edit
def update_geometry():
    global helix
    N_POINTS = 1_000   # even: every point gets paired into a line
    sides = int(sides_slider.value)
    radius = radius_slider.value
    try:
        profile = RegularPolygon(radius=20, side_count=sides)
        part = extrude(profile, amount=20)
        bore = RegularPolygon(radius=radius, side_count=sides)
        bore = extrude(bore, amount=60)
        part = part - bore

        asm = Compound(children=[part])
        export_gltf(asm, "part.glb", binary=True)

        # Random points inside the part's volume, and random pairings of
        # them as segments. to_geometry() BAKES node transforms (the GLB's
        # root carries the Z-up -> Y-up rotation), so samples land in the
        # same frame the part renders in.
        mesh = trimesh.load("part.glb").to_geometry()
        rng = np.random.default_rng(0)   # fixed seed: same points/pairs on
        n = N_POINTS & ~1                # every rebuild; even, so pairs
        pts = sample_inside(mesh, n, rng)   # come out whole

        # A 3D function plot: sample the parameter, hand over the (K, 3)
        # samples — a helix wound around the part (frame is Y-up, meters).
        t = np.linspace(0, 6 * np.pi, 1200)
        helix = np.column_stack([0.023 * np.cos(t),      # x(t)
                                 0.020 * t / t[-1],      # y(t): 0 -> top
                                 0.023 * np.sin(t)])     # z(t)

        # A scalar field around the part: distance to the bore axis, shown
        # as its r=24mm isosurface (a clearance shell, level 0 of the field).
        ax = np.linspace(-0.030, 0.030, 48)
        ay = np.linspace(-0.004, 0.024, 24)
        az = np.linspace(-0.030, 0.030, 48)
        X, _, Z = np.meshgrid(ax, ay, az, indexing="ij")
        field = 0.024 - np.sqrt(X ** 2 + Z ** 2)

        # >>> the viewer is LAYERS: each call replaces only its own layer.
        viewer.layer("part").update("part.glb", mesh_color=STEEL)
        viewer.layer("cloud").points(          # value-colored: radius from
            pts, color_by=np.hypot(pts[:, 0], pts[:, 2]))   # the bore axis
        viewer.layer("net").lines(pts[rng.permutation(n)].reshape(-1, 2, 3),
                                  color=GREEN)
        viewer.layer("path").curve(helix, color=ORANGE)
        viewer.layer("shell").isosurface(
            field, level=0.0, color="#88bbff55",   # translucent via #rgba
            spacing=(ax[1] - ax[0], ay[1] - ay[0], az[1] - az[0]),
            origin=(ax[0], ay[0], az[0]))

        # A napari-style VOLUME rendering (GPU ray marching, truly
        # transparent — MIP by default, "Fog" on the toolbar). Three
        # gaussian rods cross at the volume's center, so the transparency
        # is unmistakable: from any angle you see each rod THROUGH the
        # others. (For blocky occupancy voxels use layer.voxels() instead.)
        nv = 128
        i, j, k = np.indices((nv, nv, nv)).astype(float)
        c = (nv - 1) / 2
        # gaussian width scales with the grid so the rods stay proportional
        w = (nv / 16) ** 2
        rod_i = np.exp(-((j - c) ** 2 + (k - c) ** 2) / w)   # along x
        rod_j = np.exp(-((i - c) ** 2 + (k - c) ** 2) / w)   # along y
        rod_k = np.exp(-((i - c) ** 2 + (j - c) ** 2) / w)   # along z
        dens = np.maximum.reduce([rod_i, rod_j, rod_k])
        viewer.layer("rods").volume(dens, spacing=(0.02 / nv,) * 3,
                                    origin=(0.032, 0.0, -0.010),
                                    cmap="gray")
        print(f"part updated (sides={sides}, hole r={radius}, "
              f"{n} points, {n // 2} lines)")
    except Exception as e:
        print(f"CAD Error: {e}")


@trace_button.on_click(dedicated=True, queue="latest")
def trace_path():
    """Animate the helix being traced: ~90 frames, each replacing ONLY the
    "path" layer (~20KB), while the part and the million-point-capable
    cloud layers stay untouched."""
    if helix is None:
        return
    trace_button.text = "tracing…"
    try:
        for k in np.linspace(2, len(helix), 90).astype(int):
            viewer.layer("path").curve(helix[:k], color=ORANGE)
            time.sleep(1 / 30)
    finally:
        trace_button.text = "▶ trace path"


@sides_slider.on_change(dedicated=True, queue="latest")
def _(v):
    update_geometry()


@radius_slider.on_change(dedicated=True, queue="latest")
def _(v):
    update_geometry()


@canvas.on_connect
def _(_v):
    # Build once for the first viewer; the model3d panel itself re-pushes
    # every layer to viewers that mount later.
    if viewer.layer("part").glb is None:
        update_geometry()


canvas.serve(open_browser=False)
