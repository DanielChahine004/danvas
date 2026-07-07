"""Model3D: a prebuilt 3D model viewer panel — hand it GLB bytes, done.

The viewer inside is Google's `\\<model-viewer>
<https://modelviewer.dev>`_ (the established web-component 3D viewer, loaded
from its CDN): smooth orbit/pan/zoom with inertia and proper PBR rendering,
zero HTML written. A presentation viewer, not an engineering one — no
measurements or section planes.

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
"""

import os

from .custom import Custom

_VIEWER_HTML = """
<style>
    html, body { margin: 0; height: 100%; background: #1a1a1b; overflow: hidden;
                 font-family: monospace; }
    model-viewer { width: 100vw; height: 100vh; --poster-color: transparent; }
    #status { position: absolute; top: 10px; left: 10px; color: #888;
              font-size: 11px; pointer-events: none;
              background: rgba(0,0,0,0.8); padding: 4px 8px; border-radius: 4px; }
    #toolbar { position: absolute; top: 10px; right: 10px; display: flex; gap: 6px; }
    #toolbar button { font-family: monospace; font-size: 11px; color: #ddd;
                      background: #2a2a2c; border: 1px solid #444;
                      border-radius: 4px; padding: 6px 10px; cursor: pointer; }
    #toolbar button.on { background: #3b6ea5; border-color: #5a8fc7; color: #fff; }
    #toolbar button:hover { border-color: #777; }
</style>

<script type="module"
    src="https://cdn.jsdelivr.net/npm/@google/model-viewer@3/dist/model-viewer.min.js"></script>

<model-viewer id="mv" camera-controls interaction-prompt="none"
              exposure="0.9" shadow-intensity="0.6"></model-viewer>
<div id="status">WAITING FOR MODEL…</div>
<div id="toolbar">
    <button id="btnSpin">Spin</button>
    <button id="btnReset">Reset view</button>
</div>

<script>
    const mv = document.getElementById('mv');
    const status = document.getElementById('status');
    let firstLoad = true;
    let savedOrbit = null, savedTarget = null, savedFov = null;

    canvas.onPush((data) => {
        // The sandboxed iframe (opaque origin) can't fetch blob: URLs, so
        // the GLB rides a data: URL, which fetch() accepts from anywhere.
        if (!firstLoad) {
            savedOrbit  = mv.getCameraOrbit().toString();
            savedTarget = mv.getCameraTarget().toString();
            savedFov    = mv.getFieldOfView() + "deg";
        }
        const bytes = new Uint8Array(data);
        let bin = "";
        const CHUNK = 0x8000;
        for (let i = 0; i < bytes.length; i += CHUNK)
            bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
        status.innerText = "LOADING…";
        mv.src = "data:model/gltf-binary;base64," + btoa(bin);
    });

    mv.addEventListener('load', () => {
        status.innerText = "RENDER COMPLETE";
        if (!firstLoad && savedOrbit) {
            // Each src swap re-frames; put the user's viewpoint back.
            mv.cameraOrbit  = savedOrbit;
            mv.cameraTarget = savedTarget;
            mv.fieldOfView  = savedFov;
            mv.jumpCameraToGoal();
        }
        firstLoad = false;
    });
    mv.addEventListener('error', (e) => {
        status.innerText = "LOAD ERROR: " + (e.detail ? e.detail.type : e.type);
    });

    document.getElementById('btnSpin').onclick = function () {
        mv.autoRotate = !mv.autoRotate;
        this.classList.toggle('on', mv.autoRotate);
    };
    document.getElementById('btnReset').onclick = () => {
        mv.cameraOrbit = "auto auto auto";
        mv.cameraTarget = "auto auto auto";
        mv.fieldOfView = "auto";
        mv.jumpCameraToGoal();
    };

    canvas.send({event: 'ready'});
</script>
"""


class Model3D(Custom):
    """A prebuilt 3D model viewer: GLB in, orbit/pan/zoom out."""

    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {},
        "updates": {},
        "events": [{"event": "ready"}],
        "binary": "receives CUSTOM (code 3): a complete glTF-Binary (GLB) "
                  "model; each push replaces the current one",
        "note": "the viewer (Google <model-viewer>) loads from its CDN; the "
                "browser needs network access to cdn.jsdelivr.net on first "
                "render",
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
