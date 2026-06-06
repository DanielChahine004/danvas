"""Custom HTML panel: hand-written markup that talks back to Python.

The panel's buttons and text box call the injected ``canvas.send(...)`` helper.
Python receives those messages via ``@panel.on_message`` and echoes a reply
back into a Label card.
"""

import pycanvas

canvas = pycanvas.Canvas()

# A completely hand-authored HTML/CSS/JS panel — no PyCanvas component involved.
PANEL_HTML = """
<!doctype html>
<html>
  <head>
    <style>
      body { margin: 0; font-family: system-ui, sans-serif; color: #222; }
      .wrap { padding: 14px; }
      h3 { margin: 0 0 10px; font-size: 15px; }
      button {
        font-size: 14px; padding: 8px 14px; margin: 0 6px 8px 0;
        border: none; border-radius: 6px; background: #2563eb; color: #fff;
        cursor: pointer;
      }
      button.alt { background: #16a34a; }
      input { font-size: 14px; padding: 7px 9px; width: 60%;
              border: 1px solid #ccc; border-radius: 6px; }
      .row { margin-top: 8px; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <h3>Hand-built control panel</h3>
      <button onclick="canvas.send({action: 'ping'})">Ping</button>
      <button class="alt" onclick="canvas.send({action: 'celebrate'})">
        Celebrate
      </button>
      <div class="row">
        <input id="msg" placeholder="type a message..." />
        <button onclick="canvas.send({action: 'text', text: document.getElementById('msg').value})">
          Send
        </button>
      </div>
    </div>
  </body>
</html>
"""

panel = canvas.custom(html=PANEL_HTML, name="my_panel", width=420, height=240)
reply = canvas.label("python received", value="(waiting for input)")


@panel.on_message
def handle(data):
    print("panel sent:", data)
    action = data.get("action")
    if action == "ping":
        reply.update("pong!")
    elif action == "celebrate":
        reply.update("🎉 woohoo")
    elif action == "text":
        reply.update(f"you said: {data.get('text', '')}")


canvas.serve(port=8000)
