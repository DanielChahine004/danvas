# Danvas MCP

This optional MCP server lets an assistant build and edit interactive Danvas
canvases, read user input, inspect live state, capture PNGs, and save/reload boards.
It uses the official Python MCP SDK 2.x. No Python execution tool is exposed.

## Install and launch

Python **3.10+** is required for MCP; the base Danvas package still supports 3.9.
From this checkout:

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -e '.[mcp]'
```

Serving also needs `danvasd` built from this checkout, because it embeds the
frontend screenshot fix shipped with the MCP integration:

```sh
cd danvas/frontend
npm ci
npm run build
cd ../..
cargo build --release --locked --manifest-path broker/Cargo.toml
export DANVASD="$PWD/broker/target/release/danvasd"
```

A `danvasd` executable beside the virtual environment's Python is also discovered
by the MCP launcher. Prebuilt upstream 0.8.0 brokers can serve panels, but do not
contain the fix for capturing diagrams outside panel bounds; use this build for
the complete, tested integration.

```sh
# Default: MCP protocol on stdin/stdout; diagnostics go to stderr.
.venv/bin/danvas-mcp --workspace /absolute/path/to/boards

# Optional local HTTP endpoint: http://127.0.0.1:8765/mcp
.venv/bin/danvas-mcp --workspace /absolute/path/to/boards \
  --transport streamable-http --port 8765
```

`python -m danvas.mcp_server` is equivalent. `--help` documents launch arguments.
The launch process stays running while the MCP host is connected. The workspace
need not exist until the first save. This integration is in this checkout; an
unmodified upstream release does not include it.

## MCP host configuration

Use the absolute path to the installed executable. For hosts with an
`mcpServers` JSON configuration:

```json
{
  "mcpServers": {
    "danvas": {
      "command": "/absolute/path/to/danvas/.venv/bin/danvas-mcp",
      "args": ["--workspace", "/absolute/path/to/boards"],
      "env": {"DANVASD": "/absolute/path/to/danvasd"}
    }
  }
}
```

Omit `env` when the wheel bundles the broker, it is on PATH, or it is beside
Python. The server supports normal MCP discovery; no extra agent skill is needed.

## Tool workflow

1. `panel_catalog` lists the exact creation options and examples for each kind.
2. `create_canvas(name="demo")` starts a loopback canvas and returns its URL.
3. `add_panel` inserts a typed spec with `name`, `kind`, `options`, and optional
   `layout` (`x`, `y`, `w`, `h`, `rotation`). Names are unique within a canvas.
4. Open the returned URL in a browser. `update_panel`, `move_panel` and
   `set_panel_visibility` apply live. `get_canvas` reads state back.
5. `get_events(canvas="demo", after=0)` returns browser inputs and a sequence
   cursor. Pass the returned `latest` as `after` next time. Polling does not consume
   events. The ring retains 500 events; `gap` reports missed, expired events.
6. `screenshot` returns an MCP PNG image from the connected browser, either the
   full canvas or one named panel. Without a viewer it returns an actionable error.
7. `save_board` writes JSON beneath `--workspace`; `load_board` rebuilds it under a
   new name. `close_canvas` releases the board and its broker process.

Example `add_panel` arguments:

```json
{
  "canvas": "demo",
  "spec": {
    "name": "revenue",
    "kind": "plot",
    "options": {
      "figure": {
        "data": [{"type": "bar", "x": ["Jan", "Feb"], "y": [12, 18]}],
        "layout": {"title": "Revenue"}
      }
    },
    "layout": {"x": 40, "y": 40, "w": 560, "h": 420}
  }
}
```

### Supported surface

| Tool(s) | Purpose |
|---|---|
| `panel_catalog` | Discover supported options and example payloads |
| `create_canvas`, `list_canvases`, `get_canvas`, `close_canvas` | Manage local canvases owned by this MCP process |
| `add_panel`, `update_panel` | Label, Markdown, table, Plotly chart, slider, toggle, button, text field, image, React, HTML |
| `move_panel`, `set_panel_visibility` | Position, size, rotate, hide and reveal panels |
| `add_shape`, `update_shape`, `connect_objects`, `remove_object` | Seven native shape types and arrows, including endpoint cleanup |
| `get_events` | Buttons, input controls and custom `canvas.send` messages |
| `set_view` | Shared camera position and zoom |
| `screenshot` | Browser-rendered PNG through MCP image content |
| `save_board`, `load_board` | Reconstructable JSON documents |

Resources: `danvas://catalog` and `danvas://canvases/{name}` (JSON).
Prompt: `build_dashboard(topic)` guides an assistant through the workflow.

### State and execution boundaries

- Each server owns its canvases. It does not take over existing user scripts or
  provide arbitrary shell/Python execution. Up to 16 boards may be open.
- HTTP sessions on one server share its boards, like multiple local clients for
  one workspace. HTTP binds only to `127.0.0.1`, with SDK DNS-rebinding checks.
  It is not an authenticated multi-user remote service; do not proxy it publicly.
- Mutations are serialized. Viewer callbacks append to a separately locked event
  buffer, so a blocking image request does not prevent browser event handling.
- JSX/HTML and image URLs render in the viewer's browser and can request network
  resources. Supply only content you intend to run there. Viewer events and saved
  content are data, not instructions for the assistant.
- Board file paths are confined to `--workspace`, including resolved symlinks.
  Existing files require explicit `overwrite=true`. The server reads only files
  named through `load_board`; panel creation does not accept local file paths.
- Save/load retains managed panel definitions, updates, live input values,
  geometry, hidden state, managed shapes, arrows and camera. It does **not** save
  freehand viewer drawings, event history, browser-only component state, or
  external code callbacks. Invalid loads roll back and leave other boards intact.
- Managed brokers close on normal server shutdown. Save boards before closing or
  disconnecting a stdio host. Abrupt OS termination is subject to Danvas's existing
  platform process-ownership behavior.
- Media streaming, external Python callbacks, attach-to-script control and remote
  authentication remain native Danvas features outside this MCP tool surface.

## Framework research and decision (2026-09-22)

Primary sources checked:

- [MCP official Python SDK](https://github.com/modelcontextprotocol/python-sdk):
  current stable v2 API, typed tools/resources/prompts, in-memory and transport
  clients, and stdio/Streamable HTTP. Installed and tested `mcp==2.2.0`.
- [Official SDK testing guide](https://py.sdk.modelcontextprotocol.io/get-started/):
  recommends exercising server objects through the MCP client. This implementation
  also tests a real stdio subprocess and an HTTP server, not only in-memory calls.
- [FastMCP](https://gofastmcp.com/getting-started/welcome): a higher-level framework
  with additional application tooling. It is a viable option, but the official
  SDK already covers this adapter's transports, schema and lifecycle needs.
- [MCP tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools):
  discovery, input schemas, tool results, errors and tool annotations. The SDK owns
  negotiation and wire framing; this adapter uses read/write/destructive hints.
- [MCP Inspector](https://github.com/modelcontextprotocol/inspector): useful for
  interactive protocol exploration. The repeatable acceptance gate here uses the
  SDK client plus Playwright instead of depending on manual Inspector sessions.

Danvas is Python-native, so the official Python SDK avoids a second language or
an HTTP wrapper around the Python API. MCP is an optional extra and adds no
imports/dependencies to the base package. The lower bound `2.2` pins the API used;
`<3` prevents an unreviewed major upgrade. No custom JSON-RPC implementation is
needed. The v2 stdio transport isolates protocol descriptors from Danvas logging.

## Verification

The completed local run, baseline comparison and browser evidence are in
[mcp-verification.md](mcp-verification.md).

```sh
uv pip install --python .venv/bin/python -e '.[mcp,mcp-test,hub]' numpy plotly httpx pillow
.venv/bin/python -m playwright install chromium
DANVASD=/absolute/path/to/danvasd .venv/bin/pytest tests/mcp -q
.venv/bin/ruff check danvas/_mcp_service.py danvas/mcp_server.py tests/mcp
.venv/bin/mypy --follow-imports=silent --ignore-missing-imports \
  danvas/_mcp_service.py danvas/mcp_server.py
```

The MCP suite requires a real broker and Chromium; missing integration prerequisites
fail the MCP gate rather than producing a false green. Base-package CI without
MCP installed skips the optional suite. `DANVAS_MCP_EVIDENCE=/path/to/output` saves
the PNG from the browser acceptance test. The dedicated MCP CI job builds the
broker and runs these checks on Linux.
