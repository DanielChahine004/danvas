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

import pycanvas

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

canvas = pycanvas.Canvas()

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

print("Click ping; watch the streamed clock and the title change after 3s. "
      "All user code — no npm build.")
canvas.serve(port=8000)
