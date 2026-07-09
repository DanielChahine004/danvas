"""Heatmap: a 2D array as a colormapped image — the scientific workhorse.

Hand it a 2D numpy array and you get the panel every imaging/simulation
script hand-rolls with PIL: colormapped cells, a labelled colorbar, and a
live readout of the value under the cursor (in ``extent`` units, so a CT
slice reads in mm, not pixels)::

    hm = canvas.heatmap("dose")

    hm.update(dose_slice, extent=(0, 350, 0, 350), cmap="hot",
              vmax=np.percentile(dose_slice, 99.5))

``update`` takes any 2D array (row 0 renders at the top, matplotlib's
``origin="upper"``), windows it to ``vmin``..``vmax`` (data range by
default), and ships one uint8 frame — cheap enough to stream per slice.
Clicks reach Python via ``@hm.on_pick`` with the cursor's extent
coordinates and the value under it.

Wire format (CUSTOM binary envelope, language-neutral): ``b"DVH1"`` +
u32le nx,ny + f32le vmin,vmax + f32le x0,x1,y0,y1 (extent) + u8 cmap
(0 gray / 1 hot / 2 viridis) + u8 smooth + 2 pad + ny*nx uint8 cells,
row-major.
"""

import struct

from .custom import Custom

_VIEWER_HTML = """
<style>
    html, body { margin: 0; height: 100%; background: #1a1a1b;
                 overflow: hidden; font-family: monospace; }
    #wrap { display: flex; width: 100vw; height: 100vh; }
    #main { flex: 1; position: relative; min-width: 0; }
    #hm { position: absolute; inset: 8px 8px 26px 8px;
          width: calc(100% - 16px); height: calc(100% - 34px);
          image-rendering: pixelated; cursor: crosshair; }
    #hm.smooth { image-rendering: auto; }
    #bar { width: 52px; position: relative; }
    #cb { position: absolute; top: 8px; bottom: 26px; left: 6px; width: 14px; }
    #bmax, #bmin { position: absolute; left: 24px; color: #aaa;
                   font-size: 10px; }
    #bmax { top: 6px; }
    #bmin { bottom: 24px; }
    #ro { position: absolute; left: 8px; bottom: 6px; color: #888;
          font-size: 11px; pointer-events: none; }
    #status { position: absolute; top: 10px; left: 10px; color: #888;
              font-size: 11px; pointer-events: none;
              background: rgba(0,0,0,0.8); padding: 3px 7px;
              border-radius: 4px; }
</style>

<div id="wrap">
  <div id="main">
    <canvas id="hm"></canvas>
    <div id="ro"></div>
    <div id="status">WAITING FOR DATA…</div>
  </div>
  <div id="bar"><canvas id="cb" width="14" height="256"></canvas>
    <span id="bmax"></span><span id="bmin"></span></div>
</div>

<script>
(function () {
    const hm = document.getElementById('hm');
    const cb = document.getElementById('cb');
    const ro = document.getElementById('ro');
    const status = document.getElementById('status');
    const ctx = hm.getContext('2d');

    // colormap LUTs (mirrors the model3d volume shader's named maps)
    function ramp(stops) {
        const lut = new Uint8Array(256 * 3);
        for (let i = 0; i < 256; i++) {
            const t = i / 255 * (stops.length - 1);
            const k = Math.min(Math.floor(t), stops.length - 2), f = t - k;
            for (let c = 0; c < 3; c++)
                lut[i * 3 + c] = Math.round(
                    stops[k][c] + (stops[k + 1][c] - stops[k][c]) * f);
        }
        return lut;
    }
    const LUTS = [
        ramp([[0, 0, 0], [255, 255, 255]]),
        ramp([[0, 0, 0], [180, 0, 0], [255, 160, 0], [255, 255, 255]]),
        ramp([[68, 1, 84], [59, 81, 139], [33, 144, 141], [92, 200, 99],
              [253, 231, 37]]),
    ];

    let D = null;   // {nx, ny, vmin, vmax, extent, cells}

    function drawColorbar(lut) {
        const g = cb.getContext('2d');
        const img = g.createImageData(14, 256);
        for (let y = 0; y < 256; y++) {
            const v = 255 - y;   // max at the top
            for (let x = 0; x < 14; x++) {
                const o = (y * 14 + x) * 4;
                img.data[o] = lut[v * 3];
                img.data[o + 1] = lut[v * 3 + 1];
                img.data[o + 2] = lut[v * 3 + 2];
                img.data[o + 3] = 255;
            }
        }
        g.putImageData(img, 0, 0);
    }

    const fmt = (v) => Number(v.toPrecision(4));

    canvas.onPush((data) => {
        const dv = new DataView(data);
        if (dv.getUint32(0, false) !== 0x44564831) {   // "DVH1"
            status.innerText = "BAD FRAME";
            return;
        }
        const nx = dv.getUint32(4, true), ny = dv.getUint32(8, true);
        const vmin = dv.getFloat32(12, true), vmax = dv.getFloat32(16, true);
        const extent = [dv.getFloat32(20, true), dv.getFloat32(24, true),
                        dv.getFloat32(28, true), dv.getFloat32(32, true)];
        const lut = LUTS[dv.getUint8(36)] || LUTS[2];
        hm.classList.toggle('smooth', !!dv.getUint8(37));
        const cells = new Uint8Array(data, 40, nx * ny);
        D = { nx, ny, vmin, vmax, extent, cells };
        hm.width = nx;
        hm.height = ny;
        const img = ctx.createImageData(nx, ny);
        for (let i = 0; i < nx * ny; i++) {
            const v = cells[i], o = i * 4;
            img.data[o] = lut[v * 3];
            img.data[o + 1] = lut[v * 3 + 1];
            img.data[o + 2] = lut[v * 3 + 2];
            img.data[o + 3] = 255;
        }
        ctx.putImageData(img, 0, 0);
        drawColorbar(lut);
        document.getElementById('bmax').innerText = fmt(vmax);
        document.getElementById('bmin').innerText = fmt(vmin);
        status.innerText = `${nx}×${ny}`;
    });

    function probe(e) {
        // cursor -> cell -> extent coords + value (row 0 = top,
        // matplotlib origin="upper": y runs extent[2] at top to [3])
        if (!D) return null;
        const r = hm.getBoundingClientRect();
        const fx = (e.clientX - r.left) / r.width;
        const fy = (e.clientY - r.top) / r.height;
        if (fx < 0 || fx >= 1 || fy < 0 || fy >= 1) return null;
        const cx = Math.floor(fx * D.nx), cy = Math.floor(fy * D.ny);
        const raw = D.cells[cy * D.nx + cx];
        return {
            x: fmt(D.extent[0] + fx * (D.extent[1] - D.extent[0])),
            y: fmt(D.extent[2] + fy * (D.extent[3] - D.extent[2])),
            value: fmt(D.vmin + raw / 255 * (D.vmax - D.vmin)),
            cell: [cx, cy],
        };
    }
    hm.addEventListener('mousemove', (e) => {
        const p = probe(e);
        ro.innerText = p ? `x=${p.x}  y=${p.y}  value=${p.value}` : '';
    });
    hm.addEventListener('mouseleave', () => { ro.innerText = ''; });
    hm.addEventListener('click', (e) => {
        const p = probe(e);
        if (p) canvas.send({ event: 'pick', ...p });
    });

    canvas.send({event: 'ready'});
})();
</script>
"""

_CMAPS = {"gray": 0, "hot": 1, "viridis": 2}


class Heatmap(Custom):
    """A 2D array, colormapped, with a colorbar and cursor readout."""

    # Language-neutral contract (see PROTOCOL.md section: component contracts).
    CONTRACT = {
        "data": {},
        "updates": {},
        "events": [{"event": "ready"},
                   {"event": "pick",
                    "x": "number -- cursor x in extent units",
                    "y": "number -- cursor y in extent units",
                    "value": "number -- value under the cursor (dequantized)",
                    "cell": "[col, row] -- the array cell"}],
        "binary": "receives CUSTOM (code 3): b'DVH1' + u32le nx,ny + f32le "
                  "vmin,vmax + f32le extent x0,x1,y0,y1 + u8 cmap (0 gray/"
                  "1 hot/2 viridis) + u8 smooth + 2 pad + ny*nx uint8 cells "
                  "row-major (row 0 top); each push replaces the array",
    }

    default_w = 460
    default_h = 380

    def __init__(self, name="heatmap", label=None, color=None):
        super().__init__(html=_VIEWER_HTML, name=name, label=label)
        self._init_color(color)
        # Binary streams aren't replayed by the hub — hold the latest frame
        # and re-push whenever a viewer mounts and says so.
        self._latest = None
        self.on("ready")(lambda _msg: self._repush())
        self.on("pick")(lambda _msg: None)   # panel-generated; opt-in via on_pick

    def _repush(self):
        if self._latest is not None:
            self.push_binary(self._latest)

    def on_pick(self, fn):
        """Register a click handler: ``{"x", "y", "value", "cell"}`` in
        extent units. Sugar for ``on("pick")``."""
        return self.on("pick")(fn)

    def update(self, array, vmin=None, vmax=None, cmap="viridis",
               extent=None, smooth=False):
        """Show a 2D array. ``vmin``/``vmax`` window the colormap (data
        range by default); ``cmap`` is ``"gray"``/``"hot"``/``"viridis"``;
        ``extent=(x0, x1, y0, y1)`` labels the axes/readout in real units
        (defaults to cell indices; row 0 renders at the top). ``smooth``
        interpolates cells instead of showing crisp pixels."""
        import numpy as np
        arr = np.asarray(array)
        if arr.ndim != 2:
            raise ValueError(f"expected a 2D array, got shape {arr.shape}")
        if cmap not in _CMAPS:
            raise ValueError(f"cmap must be one of {sorted(_CMAPS)}")
        ny, nx = arr.shape
        lo = float(arr.min()) if vmin is None else float(vmin)
        hi = float(arr.max()) if vmax is None else float(vmax)
        u8 = np.clip((arr.astype(float) - lo) / max(hi - lo, 1e-12) * 255,
                     0, 255).astype(np.uint8)
        x0, x1, y0, y1 = extent if extent is not None else (0, nx, 0, ny)
        frame = (b"DVH1"
                 + struct.pack("<IIffffff", nx, ny, lo, hi,
                               float(x0), float(x1), float(y0), float(y1))
                 + bytes([_CMAPS[cmap], 1 if smooth else 0, 0, 0])
                 + np.ascontiguousarray(u8).tobytes())
        self._latest = frame
        self.push_binary(frame)
