"""Model3D: a prebuilt CAD/3D viewer panel — hand it GLB bytes, done.

The viewer inside is `xeokit <https://xeokit.io>`_ (the established
BIM/engineering web viewer, loaded from its CDN), pre-wired with the tools a
part viewer needs: orbit/pan with fine-grained proportional zoom, snap-to-edge
distance **measurements**, a draggable **section plane**, reset/clear — the
distilled version of the panel every CAD script used to hand-write.

The input is **glTF-Binary (GLB)** — the industry-standard 3D binary, which
every CAD/mesh stack exports::

    viewer = canvas.model3d("part")

    @canvas.on_edit
    def rebuild():
        export_gltf(make_part(), "part.glb", binary=True)   # build123d
        viewer.update(open("part.glb", "rb").read())

``update`` accepts GLB ``bytes``, a filesystem path to a ``.glb``/``.gltf``,
or any object exposing a glTF exporter danvas recognises (a trimesh
``Trimesh``/``Scene`` via ``export(file_type="glb")``). Each update replaces
the model in place; the camera frames the first load and holds your viewpoint
afterwards.

Polyglot: the panel ships as the ``model3d`` template, and the bytes ride the
CUSTOM binary envelope — any SDK renders a part by sending one register frame
and pushing GLB bytes (Rust: ``send_media(3, id, glb)``).

Section-cap note: a filled cut face needs a closed solid mesh. GLB exported
from CAD kernels is usually one open surface per face, so the section slices
without a cap — the standard trade for taking the standard format.
"""

import os

from .custom import Custom

_VIEWER_HTML = """
<style>
    html, body { margin: 0; height: 100%; background: #1a1a1b; overflow: hidden;
                 font-family: monospace; }
    #xk { width: 100vw; height: 100vh; cursor: grab; }
    #xk:active { cursor: grabbing; }
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
</style>

<canvas id="xk"></canvas>
<div id="status">WAITING FOR MODEL…</div>
<div id="toolbar">
    <button id="btnMeasure">Measure</button>
    <button id="btnSection">Section</button>
    <button id="btnReset">Reset view</button>
    <button id="btnClear">Clear</button>
</div>

<script type="module">
    import {
        Viewer, GLTFLoaderPlugin, SectionPlanesPlugin,
        DistanceMeasurementsPlugin, DistanceMeasurementsMouseControl,
        PointerLens, math
    } from "https://cdn.jsdelivr.net/npm/@xeokit/xeokit-sdk@2/dist/xeokit-sdk.es.min.js";

    const status = document.getElementById('status');
    const viewer = new Viewer({ canvasId: "xk", transparent: true,
                                readableGeometryEnabled: true });
    viewer.camera.eye = [60, 60, 60];
    viewer.camera.look = [0, 0, 0];
    viewer.camera.up = [0, 1, 0];
    viewer.camera.perspective.near = 0.1;
    viewer.camera.perspective.far = 100000;
    viewer.cameraControl.mouseWheelDollyRate = 10;
    viewer.cameraControl.dollyProportionalToCameraDistance = true;

    const loader        = new GLTFLoaderPlugin(viewer);
    const sectionPlanes = new SectionPlanesPlugin(viewer, { overviewVisible: false });
    const distance      = new DistanceMeasurementsPlugin(viewer);
    const measureCtrl   = new DistanceMeasurementsMouseControl(distance, {
        pointerLens: new PointerLens(viewer)
    });
    measureCtrl.snapping = true;

    let model = null;
    let blobUrl = null;
    let firstLoad = true;
    let measuring = false;
    let sectioning = false;
    let seq = 0;

    canvas.onPush((data) => {
        // GLB bytes -> a blob URL the GLTF loader streams from.
        const mySeq = ++seq;
        distance.clear();
        if (sectioning) { sectionPlanes.clear(); sectioning = false; sync(); }
        if (model) { model.destroy(); model = null; }
        if (blobUrl) { URL.revokeObjectURL(blobUrl); }
        blobUrl = URL.createObjectURL(
            new Blob([data], { type: "model/gltf-binary" }));
        status.innerText = "LOADING…";
        const m = loader.load({ id: "cad" + mySeq, src: blobUrl, edges: true });
        m.on("loaded", () => {
            if (mySeq !== seq) { m.destroy(); return; }   // a newer push won
            model = m;
            status.innerText = "RENDER COMPLETE";
            if (firstLoad) { viewer.cameraFlight.jumpTo(model); firstLoad = false; }
        });
        m.on("error", (e) => { status.innerText = "LOAD ERROR: " + e; });
    });

    const bM = document.getElementById('btnMeasure');
    const bS = document.getElementById('btnSection');
    function sync() {
        bM.classList.toggle('on', measuring);
        bS.classList.toggle('on', sectioning);
    }
    bM.onclick = () => {
        measuring = !measuring;
        measuring ? measureCtrl.activate() : measureCtrl.deactivate();
        sync();
    };
    bS.onclick = () => {
        if (!model) return;
        sectioning = !sectioning;
        if (sectioning) {
            const c = math.getAABB3Center(viewer.scene.aabb);
            const sp = sectionPlanes.createSectionPlane({ id: "sp1", pos: c, dir: [-1, 0, 0] });
            sectionPlanes.showControl(sp.id);
        } else {
            sectionPlanes.clear();
        }
        sync();
    };
    document.getElementById('btnReset').onclick = () => {
        if (model) viewer.cameraFlight.flyTo(model);
    };
    document.getElementById('btnClear').onclick = () => {
        distance.clear();
        sectionPlanes.clear();
        measuring = false; sectioning = false;
        measureCtrl.deactivate();
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
        "events": [{"event": "ready"}],
        "binary": "receives CUSTOM (code 3): a complete glTF-Binary (GLB) "
                  "model; each push replaces the current one",
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
        # A model pushed before any browser was ready would be dropped
        # (binary streams aren't replayed) — hold the latest GLB and
        # re-push whenever a viewer mounts and says so.
        self._latest = None
        self.on("ready")(lambda _msg: self._repush())

    def _repush(self):
        if self._latest is not None:
            self.push_binary(self._latest)

    def update(self, source):
        """Show a model: GLB ``bytes``, a ``.glb``/``.gltf`` path, or a
        trimesh object (anything with ``export(file_type="glb")``)."""
        if isinstance(source, (bytes, bytearray, memoryview)):
            glb = bytes(source)
        elif isinstance(source, (str, os.PathLike)) and os.path.isfile(source):
            with open(source, "rb") as f:
                glb = f.read()
        elif hasattr(source, "export"):
            glb = source.export(file_type="glb")
        else:
            raise TypeError(
                "model3d.update takes GLB bytes, a .glb/.gltf path, or a "
                "trimesh-like object with export(file_type='glb') — for "
                "build123d, export_gltf(part, path, binary=True) first")
        self._latest = glb
        self.push_binary(glb)
