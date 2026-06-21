"""Example: A custom analog clock component.
This demonstrates how to use canvas.custom() to render a custom HTML/SVG widget
that is updated in real-time from a background thread.
"""

import threading
import time
from datetime import datetime
import danvas

# --- The Widget's Front End ---
# We use an SVG circle for the clock face and three lines for the hands.
# The JS function 'drawClock' is called whenever the Python backend pushes
# new time values (h, m, s).
CLOCK_HTML = """
<style>
  body { margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; background: transparent; }
  #clock-face { filter: drop-shadow(2px 2px 2px rgba(0,0,0,0.3)); }
  .hand { stroke-linecap: round; }
</style>

<svg id="clock-face" width="200" height="200" viewBox="-100 -100 200 200">
  <!-- Clock Face -->
  <circle r="95" fill="white" stroke="#333" stroke-width="4"/>
  <circle r="90" fill="none" stroke="#eee" stroke-width="1" stroke-dasharray="2,2"/>
  <g id="clock-numbers" transform="rotate(-90)"></g>
  <!-- Hands -->
  <line id="hour-hand" class="hand" x1="0" y1="0" x2="0" y2="-40" stroke="#333" stroke-width="6"/>
  <line id="min-hand" class="hand" x1="0" y1="0" x2="0" y2="-60" stroke="#666" stroke-width="4"/>
  <line id="sec-hand" class="hand" x1="0" y1="0" x2="0" y2="-80" stroke="#ff0000" stroke-width="2"/>
  <!-- Center Pin -->
  <circle r="4" fill="#333"/>
</svg>

<script>
  const hourHand = document.getElementById('hour-hand');
  const minHand = document.getElementById('min-hand');
  const secHand = document.getElementById('sec-hand');
  const numbersGroup = document.getElementById('clock-numbers');

  // Generate clock numbers
  for (let i = 1; i <= 12; i++) {
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", "0");
    text.setAttribute("y", "0");
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("dominant-baseline", "middle");
    text.setAttribute("font-family", "sans-serif");
    text.setAttribute("font-size", "14px");
    text.setAttribute("fill", "#333");
    // Translate the numbers out to a radius of 80 before rotating them
    text.setAttribute("transform", `rotate(${i * 30}) translate(80, 0) rotate(-${i * 30})`);
    text.textContent = i;
    numbersGroup.appendChild(text);
  }

  function drawClock(data) {
    // data = { h: 0-23, m: 0-59, s: 0-59 }
    const { h, m, s } = data;

    // Calculate angles
    const secDeg = s * 6;
    const minDeg = (m + s / 60) * 6;
    const hourDeg = (h % 12 + m / 60) * 30;

    // Apply rotation
    secHand.setAttribute('transform', `rotate(${secDeg})`);
    minHand.setAttribute('transform', `rotate(${minDeg})`);
    hourHand.setAttribute('transform', `rotate(${hourDeg})`);
  }

  // Receive updates from Python via canvas.onPush
  canvas.onPush((data) => drawClock(data));
</script>
"""

canvas = danvas.Canvas()

# Create the custom clock component
clock = canvas.custom(
    html=CLOCK_HTML,
    name="analog_clock",
    w=250,
    h=250,
    x=100,
    y=100
)

# Add a label for some context
status = canvas.label("status", "Clock updating...", x=100, y=360)

def time_worker():
    """Background thread to update the clock every second."""
    while True:
        now = datetime.now()
        # Push a dictionary with h, m, s values to the custom component
        clock.push({
            "h": now.hour,
            "m": now.minute,
            "s": now.second
        })

        # Also update the status label
        status.update(f"Time: {now.strftime('%H:%M:%S')}")

        time.sleep(1)

# Start the background thread
threading.Thread(target=time_worker, daemon=True).start()

if __name__ == "__main__":
    print("Starting analog clock demo on port 8000...")
    canvas.serve(port=8000, host="")

