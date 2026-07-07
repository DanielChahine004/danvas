"""Model3D: a prebuilt CAD/3D viewer panel — hand it GLB bytes, done.

The viewer inside is `xeokit <https://xeokit.io>`_ (the established
BIM/engineering web viewer, loaded from its CDN), pre-wired with the tools a
part viewer needs: orbit/pan with fine-grained proportional zoom, snap-to-edge
**distance** and **angle** measurements, a draggable **section plane**,
**X-ray** and **edges** display toggles, a **NavCube** orientation gizmo,
fit/clear — the distilled version of the panel every CAD script used to
hand-write.

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
</style>

<canvas id="xk"></canvas>
<canvas id="nav"></canvas>
<div id="status">WAITING FOR MODEL…</div>
<div id="toolbar">
    <button id="btnMeasure">Measure</button>
    <button id="btnAngle">Angle</button>
    <button id="btnSection">Section</button>
    <button id="btnXray">X-ray</button>
    <button id="btnEdges" class="on">Edges</button>
    <button id="btnReset">Fit</button>
    <button id="btnClear">Clear</button>
</div>

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
    viewer.camera.eye = [60, 60, 60];
    viewer.camera.look = [0, 0, 0];
    viewer.camera.up = [0, 1, 0];
    // near/far are set per-load, scaled to the model (a mm-modeled part
    // arrives in meters — a 20mm box is 0.02 units, inside any fixed near).
    viewer.cameraControl.mouseWheelDollyRate = 10;
    viewer.cameraControl.dollyProportionalToCameraDistance = true;

    // The sandboxed iframe (opaque origin) can't XHR-fetch blob: URLs, so
    // hand the loader its bytes directly instead of a src it would fetch.
    let pendingBuf = null;
    const loader        = new GLTFLoaderPlugin(viewer, {
        dataSource: {   // the loader picks the getter by src extension
            getGLB:  (src, ok, err) => ok(pendingBuf),
            getGLTF: (src, ok, err) => ok(pendingBuf),
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
    new NavCubePlugin(viewer, { canvasId: "nav", visible: true,
                                color: "#2a2a2c", textColor: "#ddd" });

    let model = null;
    let firstLoad = true;
    let measuring = false;
    let angling = false;
    let sectioning = false;
    let xrayed = false;
    let edgesOn = true;
    let seq = 0;

    function applyLook() {
        // Per-entity display state resets with each load — reapply the toggles.
        const scene = viewer.scene;
        scene.setObjectsXRayed(scene.objectIds, xrayed);
        for (const id of scene.objectIds) {
            const o = scene.objects[id];
            if (o) o.edges = edgesOn;
        }
    }

    canvas.onPush((data) => {
        // GLB bytes arrive as an ArrayBuffer; the dataSource serves them.
        const mySeq = ++seq;
        distance.clear();
        angle.clear();
        if (sectioning) { sectionPlanes.clear(); sectioning = false; sync(); }
        if (model) { model.destroy(); model = null; }
        pendingBuf = data;
        status.innerText = "LOADING…";
        const m = loader.load({ id: "cad" + mySeq, src: "model.glb", edges: true });
        m.on("loaded", () => {
            if (mySeq !== seq) { m.destroy(); return; }   // a newer push won
            model = m;
            const diag = Math.max(math.getAABB3Diag(m.aabb), 1e-6);
            viewer.camera.perspective.near = diag / 1000;
            viewer.camera.perspective.far  = diag * 1000;
            viewer.camera.ortho.near = diag / 1000;
            viewer.camera.ortho.far  = diag * 1000;
            applyLook();
            status.innerText = "RENDER COMPLETE";
            if (firstLoad) { viewer.cameraFlight.jumpTo(model); firstLoad = false; }
        });
        m.on("error", (e) => { status.innerText = "LOAD ERROR: " + e; });
    });

    const bM = document.getElementById('btnMeasure');
    const bA = document.getElementById('btnAngle');
    const bS = document.getElementById('btnSection');
    const bX = document.getElementById('btnXray');
    const bE = document.getElementById('btnEdges');
    function sync() {
        bM.classList.toggle('on', measuring);
        bA.classList.toggle('on', angling);
        bS.classList.toggle('on', sectioning);
        bX.classList.toggle('on', xrayed);
        bE.classList.toggle('on', edgesOn);
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
    bX.onclick = () => { xrayed = !xrayed; applyLook(); sync(); };
    bE.onclick = () => { edgesOn = !edgesOn; applyLook(); sync(); };
    document.getElementById('btnReset').onclick = () => {
        if (model) viewer.cameraFlight.flyTo(model);
    };
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
