"""Build a native canvas panel from your own React component — no npm, no rebuild.

``React`` is the native counterpart to ``Custom``: instead of sandboxed HTML in
an iframe, you ship JSX *source* and the prebuilt frontend compiles it in the
browser and mounts it as a real React subtree inside the panel. So it picks up
the canvas theme and selection chrome, and talks to Python directly:

  * your component calls ``canvas.send({...})`` to talk to Python;
  * ``@panel.on("event")`` routes those by their ``event`` field;
  * ``panel.update(**props)`` patches the component's ``props`` (live re-render);
  * ``panel.push(data)`` streams into the component's ``value`` prop (no reload).

This builds a live "ping" widget in a handful of lines of user code.

Run:  python examples/react_component.py
"""

import threading
import time

import danvas

# The component is named ``Component`` and gets { canvas, value, props }.
# ``React`` (with hooks) is in scope. This is real JSX — compiled in the browser.
PING_JSX = """
function Component({ canvas, value, props }) {
  const [count, setCount] = React.useState(0)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
      <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--pc-text)' }}>
        {props.title}
      </div>
      <div style={{ fontSize: 14, color: 'var(--pc-muted)' }}>
        server time: {value ?? '—'}
      </div>
      <button
        style={{
          padding: '8px 16px', border: 'none', borderRadius: 6, fontSize: 14,
          fontWeight: 600, cursor: 'pointer',
          background: 'var(--pc-accent)', color: 'var(--pc-accent-text)',
        }}
        onClick={() => { setCount(count + 1); canvas.send({ event: 'ping', count: count + 1 }) }}
      >
        ping ({count})
      </button>
    </div>
  )
}
"""

canvas = danvas.Canvas()

panel = canvas.react(PING_JSX, name="ping", props={"title": "Hello, React"}, x=80, y=80, frame=False)
status = canvas.label("status", value="click ping", x=400, y=80)


@panel.on("ping")
def on_ping(msg):
    status.update(f"ping #{msg['count']}")


# Stream the server clock into the component's `value` prop (no re-mount).
def clock():
    while True:
        panel.push(time.strftime("%H:%M:%S"))
        time.sleep(1)


threading.Thread(target=clock, daemon=True).start()

# Patch a prop after 3s to show update() re-rendering live.
def retitle():
    time.sleep(3)
    panel.update(title="Still React")


threading.Thread(target=retitle, daemon=True).start()

# `scope=` pulls third-party libraries (loaded as ESM in the browser, no npm
# build) into the component as the `libs` global — here d3 for a quick scale.
# h="auto"/w="auto" shrink the panel to hug the rendered content on each axis.
canvas.react('''
function Component() {
  const x = libs.d3.scaleLinear().domain([0, 100]).range([0, 200])
  return <div style={{ color: 'var(--pc-text)', whiteSpace: 'nowrap' }}>d3 maps 50 → {x(50)}</div>
}
''', scope=["d3"], x=80, y=300, h='auto', w='auto')

# High-rate binary telemetry: `push_binary` sends packed bytes on a binary
# WebSocket frame (no JSON, no base64); `canvas.onFrame(cb)` receives it as a
# zero-copy ArrayBuffer and the component paints it to a <canvas> itself — no
# React re-render per frame (reading the `value` prop would re-render each time).
WAVE_JSX = """
const WINDOW = 480
function Component({ canvas }) {
  const ref = React.useRef(null)
  const buf = React.useRef([])
  // Measure the *actual* incoming frame rate: count push_binary frames/second.
  // All of this stays in refs and is drawn onto the canvas, so the FPS readout
  // costs no React re-render either — the same point as onFrame itself.
  const count = React.useRef(0)
  const fps = React.useRef(0)
  const last = React.useRef(performance.now())
  React.useEffect(() => {
    const off = canvas.onFrame((d) => {
      const b = buf.current
      // Binary frames arrive as an ArrayBuffer (push_binary): unpack the packed
      // float32 samples zero-copy. A plain push() would arrive as a JSON value.
      if (d instanceof ArrayBuffer) {
        const arr = new Float32Array(d)
        for (let i = 0; i < arr.length; i++) b.push(arr[i])
      } else {
        b.push(d)
      }
      while (b.length > WINDOW) b.shift()
      count.current++ // one tally per frame, regardless of samples packed in it
    })
    let raf
    const draw = () => {
      const now = performance.now()
      if (now - last.current >= 500) {
        fps.current = Math.round((count.current * 1000) / (now - last.current))
        count.current = 0
        last.current = now
      }
      const cv = ref.current
      if (cv) {
        const ctx = cv.getContext('2d')
        const css = getComputedStyle(cv)
        ctx.clearRect(0, 0, cv.width, cv.height)
        ctx.strokeStyle = css.getPropertyValue('--pc-accent') || '#3b82f6'
        ctx.lineWidth = 2
        ctx.beginPath()
        const b = buf.current
        const n = b.length
        b.forEach((v, i) => {
          const px = n > 1 ? (i / (n - 1)) * cv.width : 0
          const py = cv.height - ((v + 1) / 2) * cv.height
          i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)
        })
        ctx.stroke()
        ctx.fillStyle = css.getPropertyValue('--pc-muted') || '#888'
        ctx.font = '12px system-ui, sans-serif'
        ctx.fillText(fps.current + ' fps', 6, 14)
      }
      raf = requestAnimationFrame(draw)
    }
    draw()
    return () => { off(); cancelAnimationFrame(raf) }
  }, [canvas])
  return <canvas ref={ref} width={300} height={120} style={{ width: '100%', height: '100%' }} />
}
"""

wave = canvas.react(WAVE_JSX, name="wave", label="binary onFrame stream", x=400, y=300, w=320, h=180)


# Stream a sine wave as packed float32 — 16 samples per binary frame, ~60 frames
# a second — over push_binary. The fps readout should hover near 60.
def stream():
    import array
    import math
    t = 0.0
    while True:
        chunk = array.array("f", (math.sin(t + i * 0.05) for i in range(16)))
        wave.push_binary(chunk.tobytes())
        t += 16 * 0.05
        time.sleep(1 / 60)


threading.Thread(target=stream, daemon=True).start()

# Binary camera feed, built entirely in user React — the native VideoFeed panel,
# reimplemented in a handful of lines: Python pushes JPEG *bytes* over
# push_binary, and onFrame wraps each ArrayBuffer in a Blob -> object URL -> <img>
# (revoking the previous URL once the next frame paints, so the stream can't leak
# memory). No JSON, no base64, no React re-render per frame.
CAMERA_JSX = """
function Component({ canvas }) {
  const imgRef = React.useRef(null)
  const urlRef = React.useRef(null)
  React.useEffect(() => {
    const off = canvas.onFrame((d) => {
      if (!(d instanceof ArrayBuffer)) return
      const el = imgRef.current
      if (!el) return
      const url = URL.createObjectURL(new Blob([d], { type: 'image/jpeg' }))
      const prev = urlRef.current
      el.onload = () => { if (prev) URL.revokeObjectURL(prev) }
      urlRef.current = url
      el.src = url
    })
    return () => {
      off()
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    }
  }, [canvas])
  return <img ref={imgRef} draggable={false}
    style={{ width: '100%', height: '100%', objectFit: 'contain', background: '#000' }} />
}
"""

# latest queue: drop stale frames for a slow viewer instead of piling up latency.
camera = canvas.react(CAMERA_JSX, name="camera", label="binary camera (onFrame)",
                      x=400, y=520, w=360, h=300, queue="latest")


# Capture from the webcam with OpenCV and stream JPEG bytes via push_binary. No
# webcam (or no OpenCV) falls back to a synthetic moving frame so the demo always
# shows something; the rendering side is identical either way.
def camera_feed():
    import importlib
    try:
        cv2 = importlib.import_module("cv2")
    except ImportError:
        print("camera demo skipped: OpenCV not installed "
              "(pip install 'danvas[video]').")
        return
    import numpy as np  # OpenCV pulls in numpy

    cap = cv2.VideoCapture(0)
    live = cap.isOpened()
    if not live:
        print("no webcam found — streaming a synthetic frame into the React panel.")
    t = 0
    while True:
        if live:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.03)
                continue
        else:
            frame = np.full((240, 320, 3), 24, np.uint8)
            cx = int((np.sin(t / 18) * 0.5 + 0.5) * 320)
            cv2.circle(frame, (cx, 120), 36, (235, 160, 60), -1)
            t += 1
        ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            camera.push_binary(jpeg.tobytes())
        time.sleep(1 / 30)


threading.Thread(target=camera_feed, daemon=True).start()

print("Click ping; watch the streamed clock and the title change after 3s. "
      "All user code — no npm build.")
canvas.serve(port=8000)
