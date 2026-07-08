"""Volume3D: a volumetric data viewer — hand it a 3D array, get true
volume rendering.

Built for tomographic/simulation volumes (PET/CT recons, density fields):
the panel ray-marches the volume on the GPU in a zero-dependency WebGL2
shader (no CDN, no three.js) with the views that kind of data actually
wants:

- **MIP** (maximum intensity projection) — the standard PET view; rotate
  to read the tracer distribution.
- **Volume** — front-to-back alpha compositing with a density transfer.
- **Slice** — an axis-aligned plane through the volume, scrubbable, in
  the same 3D space (X/Y/Z axis cycle + position slider).

Window/level is a right-drag (horizontal = window/contrast, vertical =
level/brightness), colormaps cycle Gray → Hot → Viridis, and the camera
orbits/pans/zooms like the Model3D panel. Feed it from Python::

    vol = canvas.volume3d("pet")

    vol.update(activity, spacing=(2.0, 2.0, 3.0))   # (nx,ny,nz) + mm
    vol.update(activity, vmax=np.percentile(activity, 99.5))  # window cap

``update`` takes any 3D numpy-like array — float activity/HU/density —
normalized to the ``vmin``/``vmax`` range (data min/max by default) and
shipped as a uint8 volume: one byte per voxel, so a 344³ PET recon is a
~40MB push. ``spacing`` is the per-axis voxel size (anisotropic Z is the
norm in PET); the render box is scaled accordingly.

The wire format (CUSTOM binary envelope) is language-neutral:
``b"DVV1"`` + u32le nx,ny,nz + f32le sx,sy,sz + nx*ny*nz uint8 voxels,
x-fastest. Any SDK can push a volume with one frame.
"""

import struct

from .custom import Custom

_VIEWER_HTML = """
<style>
    html, body { margin: 0; height: 100%; background: #1a1a1b; overflow: hidden;
                 font-family: monospace; }
    #gl { width: 100vw; height: 100vh; cursor: grab; display: block; }
    #gl:active { cursor: grabbing; }
    #status { position: absolute; top: 10px; left: 10px; color: #888;
              font-size: 11px; pointer-events: none;
              background: rgba(0,0,0,0.8); padding: 4px 8px; border-radius: 4px; }
    #toolbar { position: absolute; top: 10px; right: 10px; display: flex; gap: 6px; }
    #toolbar button { font-family: monospace; font-size: 11px; color: #ddd;
                      background: #2a2a2c; border: 1px solid #444;
                      border-radius: 4px; padding: 6px 10px; cursor: pointer; }
    #toolbar button.on { background: #3b6ea5; border-color: #5a8fc7; color: #fff; }
    #toolbar button:hover { border-color: #777; }
    #slice, #dens { position: absolute; left: 50%; bottom: 34px; width: 50%;
                    transform: translateX(-50%); display: none; }
    #wl { position: absolute; bottom: 10px; left: 10px; color: #888;
          font-size: 11px; pointer-events: none;
          background: rgba(0,0,0,0.8); padding: 4px 8px; border-radius: 4px; }
</style>

<canvas id="gl"></canvas>
<div id="status">WAITING FOR VOLUME…</div>
<div id="wl">W 1.00 / L 0.50 — right-drag to window</div>
<div id="toolbar">
    <button id="bMip" class="on">MIP</button>
    <button id="bVol">Volume</button>
    <button id="bSlice">Slice</button>
    <button id="bAxis">Z</button>
    <button id="bCmap">Gray</button>
    <button id="bReset">Reset</button>
</div>
<input type="range" id="slice" min="0" max="1000" value="500">
<input type="range" id="dens" min="0" max="1000" value="500">

<script>
(function () {
    const cvs = document.getElementById('gl');
    const status = document.getElementById('status');
    const wlText = document.getElementById('wl');
    const gl = cvs.getContext('webgl2', { alpha: true, antialias: false,
                                          premultipliedAlpha: false });
    if (!gl) { status.innerText = "WEBGL2 UNAVAILABLE"; return; }

    const VS = `#version 300 es
    out vec2 vUV;
    void main() {
        vUV = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
        gl_Position = vec4(vUV * 2.0 - 1.0, 0.0, 1.0);
    }`;

    const FS = `#version 300 es
    precision highp float; precision highp sampler3D;
    uniform sampler3D uVol;
    uniform sampler2D uCmap;
    uniform vec3 uCamPos, uRight, uUp, uFwd, uBoxHalf, uVoxel;
    uniform float uTanFov, uAspect, uWindow, uLevel, uSliceFrac, uDensity;
    uniform int uMode, uSliceAxis, uSteps;
    in vec2 vUV;
    out vec4 outColor;

    float vol(vec3 p) {                       // p in box space
        return texture(uVol, p / (2.0 * uBoxHalf) + 0.5).r;
    }
    float wl(float v) {
        return clamp((v - (uLevel - uWindow * 0.5)) / uWindow, 0.0, 1.0);
    }
    void main() {
        vec2 ndc = vUV * 2.0 - 1.0;
        vec3 dir = normalize(uFwd + uRight * ndc.x * uTanFov * uAspect
                                  + uUp * ndc.y * uTanFov);
        vec3 inv = 1.0 / dir;
        vec3 ta = (-uBoxHalf - uCamPos) * inv;
        vec3 tb = ( uBoxHalf - uCamPos) * inv;
        vec3 tmin = min(ta, tb), tmax = max(ta, tb);
        float t0 = max(max(tmin.x, tmin.y), max(tmin.z, 0.0));
        float t1 = min(min(tmax.x, tmax.y), tmax.z);
        if (t1 <= t0) { outColor = vec4(0.0); return; }

        if (uMode == 2) {                     // slice plane
            float h = uSliceAxis == 0 ? uBoxHalf.x
                    : uSliceAxis == 1 ? uBoxHalf.y : uBoxHalf.z;
            float plane = mix(-h, h, uSliceFrac);
            float d = uSliceAxis == 0 ? dir.x
                    : uSliceAxis == 1 ? dir.y : dir.z;
            float o = uSliceAxis == 0 ? uCamPos.x
                    : uSliceAxis == 1 ? uCamPos.y : uCamPos.z;
            if (abs(d) < 1e-6) { outColor = vec4(0.0); return; }
            float t = (plane - o) / d;
            if (t < t0 || t > t1) { outColor = vec4(0.0); return; }
            float v = wl(vol(uCamPos + dir * t));
            outColor = vec4(texture(uCmap, vec2(v, 0.5)).rgb, 1.0);
            return;
        }
        float dt = (t1 - t0) / float(uSteps);
        if (uMode == 0) {                     // MIP
            float m = 0.0;
            for (int i = 0; i < 2048; i++) {
                if (i >= uSteps) break;
                m = max(m, vol(uCamPos + dir * (t0 + (float(i) + 0.5) * dt)));
            }
            float v = wl(m);
            outColor = vec4(texture(uCmap, vec2(v, 0.5)).rgb, v);
            return;
        }
        // front-to-back composite: exponential extinction (uDensity is the
        // slider) + gradient-lit diffuse so structures read as 3D bodies
        vec3 acc = vec3(0.0);
        float T = 1.0;
        vec3 L = normalize(vec3(0.5, 0.8, 0.6));
        for (int i = 0; i < 2048; i++) {
            if (i >= uSteps) break;
            vec3 p = uCamPos + dir * (t0 + (float(i) + 0.5) * dt);
            float v = wl(vol(p));
            if (v < 0.004) continue;
            float a = 1.0 - exp(-v * v * uDensity * dt);
            vec3 g = vec3(
                wl(vol(p + vec3(uVoxel.x, 0, 0))) - wl(vol(p - vec3(uVoxel.x, 0, 0))),
                wl(vol(p + vec3(0, uVoxel.y, 0))) - wl(vol(p - vec3(0, uVoxel.y, 0))),
                wl(vol(p + vec3(0, 0, uVoxel.z))) - wl(vol(p - vec3(0, 0, uVoxel.z))));
            float gm = length(g);
            float diff = gm > 1e-4
                ? 0.35 + 0.65 * max(dot(-g / gm, L), 0.0) : 1.0;
            acc += T * a * texture(uCmap, vec2(v, 0.5)).rgb * diff;
            T *= 1.0 - a;
            if (T < 0.01) break;
        }
        outColor = vec4(acc, 1.0 - T);
    }`;

    function shader(type, src) {
        const s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
            throw new Error(gl.getShaderInfoLog(s));
        return s;
    }
    const prog = gl.createProgram();
    gl.attachShader(prog, shader(gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
        throw new Error(gl.getProgramInfoLog(prog));
    gl.useProgram(prog);
    const U = {};
    for (const n of ["uVol", "uCmap", "uCamPos", "uRight", "uUp", "uFwd",
                     "uBoxHalf", "uVoxel", "uTanFov", "uAspect", "uWindow",
                     "uLevel", "uSliceFrac", "uDensity", "uMode",
                     "uSliceAxis", "uSteps"])
        U[n] = gl.getUniformLocation(prog, n);

    // colormaps: 256x1 RGBA textures, generated in JS
    function ramp(stops) {
        const d = new Uint8Array(256 * 4);
        for (let i = 0; i < 256; i++) {
            const t = i / 255 * (stops.length - 1);
            const k = Math.min(Math.floor(t), stops.length - 2), f = t - k;
            for (let c = 0; c < 3; c++)
                d[i * 4 + c] = Math.round(
                    stops[k][c] + (stops[k + 1][c] - stops[k][c]) * f);
            d[i * 4 + 3] = 255;
        }
        return d;
    }
    const CMAPS = [
        ["Gray", ramp([[0, 0, 0], [255, 255, 255]])],
        ["Hot", ramp([[0, 0, 0], [180, 0, 0], [255, 160, 0],
                      [255, 255, 255]])],
        ["Viridis", ramp([[68, 1, 84], [59, 81, 139], [33, 144, 141],
                          [92, 200, 99], [253, 231, 37]])],
    ];
    const cmapTex = gl.createTexture();
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, cmapTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    let cmapIdx = 0;
    function setCmap(i) {
        cmapIdx = i;
        gl.activeTexture(gl.TEXTURE1);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA,
                      gl.UNSIGNED_BYTE, CMAPS[i][1]);
        document.getElementById('bCmap').innerText = CMAPS[i][0];
    }
    setCmap(0);

    const volTex = gl.createTexture();
    let dims = null, boxHalf = [0.5, 0.5, 0.5], steps = 256;

    // camera + display state
    let yaw = 0.6, pitch = 0.4, dist = 2.4, panX = 0, panY = 0;
    let mode = 0, sliceAxis = 2, sliceFrac = 0.5;
    let win = 1.0, level = 0.5, density = 400;
    let dirty = true;

    function render() {
        if (!dirty) return;
        dirty = false;
        const w = cvs.clientWidth, h = cvs.clientHeight;
        if (cvs.width !== w || cvs.height !== h) {
            cvs.width = w; cvs.height = h;
        }
        gl.viewport(0, 0, cvs.width, cvs.height);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        if (!dims) return;
        const cp = Math.cos(pitch), sp = Math.sin(pitch);
        const cy = Math.cos(yaw), sy = Math.sin(yaw);
        const fwd = [-cp * sy, -sp, -cp * cy];
        const right = [cy, 0, -sy];
        const up = [-sp * sy, cp, -sp * cy];
        const tgt = [right[0] * panX + up[0] * panY,
                     right[1] * panX + up[1] * panY,
                     right[2] * panX + up[2] * panY];
        const pos = [tgt[0] - fwd[0] * dist, tgt[1] - fwd[1] * dist,
                     tgt[2] - fwd[2] * dist];
        gl.uniform3fv(U.uCamPos, pos);
        gl.uniform3fv(U.uRight, right);
        gl.uniform3fv(U.uUp, up);
        gl.uniform3fv(U.uFwd, fwd);
        gl.uniform3fv(U.uBoxHalf, boxHalf);
        gl.uniform3fv(U.uVoxel, dims
            ? [2 * boxHalf[0] / dims[0], 2 * boxHalf[1] / dims[1],
               2 * boxHalf[2] / dims[2]]
            : [0.01, 0.01, 0.01]);
        gl.uniform1f(U.uDensity, density);
        gl.uniform1f(U.uTanFov, 0.4142);          // 45° vertical fov
        gl.uniform1f(U.uAspect, cvs.width / Math.max(cvs.height, 1));
        gl.uniform1f(U.uWindow, win);
        gl.uniform1f(U.uLevel, level);
        gl.uniform1f(U.uSliceFrac, sliceFrac);
        gl.uniform1i(U.uMode, mode);
        gl.uniform1i(U.uSliceAxis, sliceAxis);
        gl.uniform1i(U.uSteps, steps);
        gl.uniform1i(U.uVol, 0);
        gl.uniform1i(U.uCmap, 1);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
    }
    (function loop() { render(); requestAnimationFrame(loop); })();
    const redraw = () => { dirty = true; };
    new ResizeObserver(redraw).observe(cvs);

    canvas.onPush((data) => {
        // "DVV1" + u32le nx,ny,nz + f32le sx,sy,sz + uint8 voxels (x-fastest)
        const dv = new DataView(data);
        if (dv.getUint32(0, false) !== 0x44565631) {   // "DVV1"
            status.innerText = "BAD VOLUME FRAME";
            return;
        }
        const nx = dv.getUint32(4, true), ny = dv.getUint32(8, true),
              nz = dv.getUint32(12, true);
        const sx = dv.getFloat32(16, true), sy2 = dv.getFloat32(20, true),
              sz = dv.getFloat32(24, true);
        const vox = new Uint8Array(data, 28, nx * ny * nz);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_3D, volTex);
        gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
        gl.texImage3D(gl.TEXTURE_3D, 0, gl.R8, nx, ny, nz, 0, gl.RED,
                      gl.UNSIGNED_BYTE, vox);
        for (const [p, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR],
                              [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
                              [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE],
                              [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE],
                              [gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE]])
            gl.texParameteri(gl.TEXTURE_3D, p, v);
        const ex = nx * sx, ey = ny * sy2, ez = nz * sz;
        const m = Math.max(ex, ey, ez);
        boxHalf = [ex / m * 0.5, ey / m * 0.5, ez / m * 0.5];
        steps = Math.min(2 * Math.max(nx, ny, nz), 640);
        dims = [nx, ny, nz];
        status.innerText = `RENDER COMPLETE — ${nx}×${ny}×${nz}`;
        redraw();
    });

    // orbit / pan / zoom / window-level
    let btn = -1, lx = 0, ly = 0;
    cvs.addEventListener('contextmenu', (e) => e.preventDefault());
    cvs.addEventListener('mousedown', (e) => {
        btn = e.button; lx = e.clientX; ly = e.clientY;
        if (e.button === 1) e.preventDefault();
    });
    document.addEventListener('mouseup', () => { btn = -1; });
    document.addEventListener('mousemove', (e) => {
        if (btn < 0) return;
        const dx = e.clientX - lx, dy = e.clientY - ly;
        lx = e.clientX; ly = e.clientY;
        if (btn === 0) {                       // orbit
            yaw -= dx * 0.008;
            pitch = Math.min(1.55, Math.max(-1.55, pitch + dy * 0.008));
        } else if (btn === 1) {                // pan
            panX -= dx * dist * 0.0012;
            panY += dy * dist * 0.0012;
        } else if (btn === 2) {                // window/level
            win = Math.min(2, Math.max(0.01, win + dx * 0.003));
            level = Math.min(1.5, Math.max(-0.5, level + dy * 0.003));
            wlText.innerText =
                `W ${win.toFixed(2)} / L ${level.toFixed(2)} — right-drag to window`;
        }
        redraw();
    });
    cvs.addEventListener('wheel', (e) => {
        e.preventDefault();
        dist *= Math.pow(0.9, -Math.sign(e.deltaY));
        dist = Math.min(20, Math.max(0.3, dist));
        redraw();
    }, { passive: false });

    const bM = document.getElementById('bMip');
    const bV = document.getElementById('bVol');
    const bS = document.getElementById('bSlice');
    const bA = document.getElementById('bAxis');
    const slice = document.getElementById('slice');
    const dens = document.getElementById('dens');
    function setMode(m) {
        mode = m;
        bM.classList.toggle('on', m === 0);
        bV.classList.toggle('on', m === 1);
        bS.classList.toggle('on', m === 2);
        slice.style.display = m === 2 ? 'block' : 'none';
        dens.style.display = m === 1 ? 'block' : 'none';
        redraw();
    }
    bM.onclick = () => setMode(0);
    bV.onclick = () => setMode(1);
    bS.onclick = () => setMode(2);
    bA.onclick = () => {
        sliceAxis = (sliceAxis + 1) % 3;
        bA.innerText = "XYZ"[sliceAxis];
        redraw();
    };
    slice.oninput = () => { sliceFrac = slice.value / 1000; redraw(); };
    // density slider is exponential: 0..1000 -> ~20..8000 extinction
    dens.oninput = () => {
        density = 20 * Math.pow(10, dens.value / 1000 * 2.6);
        redraw();
    };
    document.getElementById('bCmap').onclick = () => {
        setCmap((cmapIdx + 1) % CMAPS.length);
        redraw();
    };
    document.getElementById('bReset').onclick = () => {
        yaw = 0.6; pitch = 0.4; dist = 2.4; panX = panY = 0;
        win = 1.0; level = 0.5; density = 400; dens.value = 500;
        wlText.innerText = "W 1.00 / L 0.50 — right-drag to window";
        redraw();
    };

    canvas.send({event: 'ready'});
})();
</script>
"""


class Volume3D(Custom):
    """True volume rendering for 3D arrays: MIP / composite / slice."""

    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {},
        "updates": {},
        "events": [{"event": "ready"}],
        "binary": "receives CUSTOM (code 3): b'DVV1' + u32le nx,ny,nz + "
                  "f32le sx,sy,sz (voxel spacing) + nx*ny*nz uint8 voxels, "
                  "x-fastest; each push replaces the volume",
        "note": "self-contained WebGL2 ray marcher — no CDN; the browser "
                "needs WebGL2",
    }

    default_w = 700
    default_h = 600

    def __init__(self, name="volume3d", label=None, color=None):
        super().__init__(html=_VIEWER_HTML, name=name, label=label,
                         # the wheel zooms the volume, not the canvas
                         forward_wheel=False)
        self._init_color(color)
        # Binary streams aren't replayed by the hub — hold the latest frame
        # and re-push whenever a viewer mounts and says so.
        self._latest = None
        self.on("ready")(lambda _msg: self._repush())

    def _repush(self):
        if self._latest is not None:
            self.push_binary(self._latest)

    def update(self, volume, spacing=(1.0, 1.0, 1.0), vmin=None, vmax=None):
        """Show a volume: any 3D array, indexed ``[x, y, z]``.

        Values are windowed to ``vmin``..``vmax`` (data min/max by
        default — for PET, capping ``vmax`` at a high percentile keeps a
        hot bladder from crushing the rest) and quantized to uint8.
        ``spacing`` is the per-axis voxel size; anisotropic spacing scales
        the render box, so non-cubic PET/CT voxels come out proportioned.
        """
        import numpy as np
        vol = np.asarray(volume)
        if vol.ndim != 3:
            raise ValueError(f"expected a 3D array, got shape {vol.shape}")
        lo = float(vol.min()) if vmin is None else float(vmin)
        hi = float(vol.max()) if vmax is None else float(vmax)
        u8 = np.clip((vol.astype(float) - lo) / max(hi - lo, 1e-12) * 255,
                     0, 255).astype(np.uint8)
        # x-fastest for the GPU upload (texture width = nx)
        payload = (b"DVV1"
                   + struct.pack("<IIIfff", *vol.shape,
                                 *(float(s) for s in spacing))
                   + np.ascontiguousarray(u8.transpose(2, 1, 0)).tobytes())
        self._latest = payload
        self.push_binary(payload)
