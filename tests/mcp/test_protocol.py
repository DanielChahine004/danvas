import asyncio
import base64
import json
import os
import socket
import struct
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from playwright.async_api import async_playwright

from danvas.mcp_server import create_server


async def call(client, tool, **args):
    result = await client.call_tool(tool, args)
    assert not result.is_error, result
    return result.structured_content


@pytest.mark.asyncio
async def test_discovery_resources_prompt_and_errors(tmp_path):
    async with Client(create_server(tmp_path)) as c:
        tools = (await c.list_tools()).tools
        assert len(tools) == 18
        assert next(t for t in tools if t.name == "get_canvas").annotations.read_only_hint
        assert next(t for t in tools if t.name == "close_canvas").annotations.destructive_hint
        assert (await c.read_resource("danvas://catalog")).contents
        assert (await c.get_prompt("build_dashboard", {"topic": "Sales"})).messages
        bad = await c.call_tool("get_canvas", {"canvas": "missing"})
        assert bad.is_error and "Unknown canvas" in bad.content[0].text
        negative = await c.call_tool("get_events", {"canvas": "missing", "after": -1})
        assert negative.is_error and "non-negative" in negative.content[0].text
        with pytest.raises(MCPError, match="Unknown canvas"):
            await c.read_resource("danvas://canvases/missing")
        bad = await c.call_tool("add_panel", {"canvas": "missing", "spec": {"name": "p", "kind": "wrong"}})
        assert bad.is_error
        bad = await c.call_tool("move_panel", {"canvas": "missing", "name": "p", "layout": {"w": -1}})
        assert bad.is_error


@pytest.mark.asyncio
async def test_stdio_browser_roundtrip_save_reload_and_shutdown(tmp_path, broker_env):
    server = StdioServerParameters(command=sys.executable,
        args=["-m", "danvas.mcp_server", "--workspace", str(tmp_path)], env=dict(os.environ))
    async with Client(server) as c, async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            board = await call(c, "create_canvas", name="demo")
            url = board["url"]
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await call(c, "add_panel", canvas="demo", spec={"name": "status", "kind": "label",
                "options": {"value": "Waiting for input"}, "layout": {"x": 30, "y": 30, "w": 300}})
            await call(c, "add_panel", canvas="demo", spec={"name": "run", "kind": "button",
                "options": {"text": "Run simulation"}, "layout": {"x": 30, "y": 160}})
            await call(c, "add_panel", canvas="demo", spec={"name": "answer", "kind": "text_field",
                "options": {"placeholder": "Type here"}, "layout": {"x": 30, "y": 290}})
            await call(c, "add_panel", canvas="demo", spec={"name": "chart", "kind": "plot",
                "options": {"figure": {"data": [{"x": [1, 2, 3], "y": [2, 5, 3]}]}},
                "layout": {"x": 440, "y": 30, "w": 550, "h": 400}})
            await call(c, "add_shape", canvas="demo", spec={"name": "note", "kind": "note",
                "options": {"text": "MCP verified", "x": 440, "y": 500}})
            await call(c, "connect_objects", canvas="demo", name="flow", start="run", end="note", text="Result")
            await page.goto(url)
            await page.get_by_text("Waiting for input", exact=True).wait_for()
            await page.locator(".js-plotly-plot").wait_for()
            await page.get_by_role("button", name="Run simulation", exact=True).click()
            await page.get_by_placeholder("Type here").fill("Live browser input")
            await page.get_by_placeholder("Type here").press("Enter")
            for _ in range(100):
                events = await call(c, "get_events", canvas="demo")
                if {"run", "answer"}.issubset({e["name"] for e in events["events"]}):
                    break
                await asyncio.sleep(0.05)
            assert any(e["name"] == "answer" and e["value"] == "Live browser input" for e in events["events"])
            await call(c, "update_panel", canvas="demo", name="status", value="Simulation complete")
            await page.get_by_text("Simulation complete", exact=True).wait_for()
            await call(c, "move_panel", canvas="demo", name="status", layout={"x": 60, "y": 40})
            await call(c, "set_panel_visibility", canvas="demo", name="status", visible=False)
            await page.get_by_text("Simulation complete", exact=True).wait_for(state="hidden")
            await call(c, "set_panel_visibility", canvas="demo", name="status", visible=True)
            await page.get_by_text("Simulation complete", exact=True).wait_for()
            shot = await c.call_tool("screenshot", {"canvas": "demo"})
            assert not shot.is_error, shot
            images = [v for v in shot.content if v.type == "image"]
            assert images, shot
            png = base64.b64decode(images[0].data)
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            assert len(png) > 5000
            # Native export must include the note below all panels, not crop at the chart.
            width, height = struct.unpack(">II", png[16:24])
            assert width > 1000 and height > 1100, (width, height)
            await page.get_by_text("MCP verified", exact=True).wait_for()
            evidence = os.environ.get("DANVAS_MCP_EVIDENCE")
            if evidence:
                Path(evidence).mkdir(parents=True, exist_ok=True)
                Path(evidence, "mcp-browser.png").write_bytes(png)
            await call(c, "save_board", canvas="demo", filename="demo.json")
            assert (tmp_path / "demo.json").is_file()
            resource = await c.read_resource("danvas://canvases/demo")
            assert "Live browser input" in resource.contents[0].text
            restored = await call(c, "load_board", name="restored", filename="demo.json")
            assert len(restored["panels"]) == 4
            assert len(restored["shapes"]) == 1 and len(restored["arrows"]) == 1
            other = await browser.new_page()
            await other.goto(restored["url"])
            await other.get_by_text("Simulation complete", exact=True).wait_for()
            assert await other.get_by_placeholder("Type here").input_value() == "Live browser input"
            await call(c, "remove_object", canvas="restored", name="note")
            assert not (await call(c, "get_canvas", canvas="restored"))["arrows"]
            await call(c, "close_canvas", canvas="restored")
            assert len((await call(c, "list_canvases"))["canvases"]) == 1
            assert not errors, errors
        finally:
            await browser.close()
    # The lifespan stops the original board even without an explicit close_canvas.
    port = int(url.rsplit(":", 1)[1])
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.asyncio
async def test_save_roundtrip_all_kinds(tmp_path, broker_env):
    from danvas._mcp_service import EXAMPLES
    async with Client(create_server(tmp_path)) as c:
        await call(c, "create_canvas", name="original")
        for kind, options in EXAMPLES.items():
            await call(c, "add_panel", canvas="original", spec={"name": kind, "kind": kind, "options": options})
        await call(c, "update_panel", canvas="original", name="react", value={"count": 2})
        await call(c, "update_panel", canvas="original", name="react", value={"title": "Yes"})
        await call(c, "set_panel_visibility", canvas="original", name="label", visible=False)
        await call(c, "set_view", canvas="original", x=10, y=20, zoom=0.75)
        await call(c, "save_board", canvas="original", filename="all.json")
        result = await call(c, "load_board", name="restored", filename="all.json")
        assert len(result["panels"]) == len(EXAMPLES)
        assert not next(p for p in result["panels"] if p["name"] == "label")["visible"]
        await call(c, "save_board", canvas="restored", filename="again.json")
        a = json.loads((tmp_path / "all.json").read_text())
        b = json.loads((tmp_path / "again.json").read_text())
        assert a == b


@pytest.mark.asyncio
async def test_streamable_http_transport_and_shutdown(tmp_path, broker_env):
    import httpx

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = (tmp_path / "http.log").open("w")
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "danvas.mcp_server", "--workspace", str(tmp_path),
                                "--transport", "streamable-http", "--port", str(port),
                               stdout=log, stderr=log)
    try:
        async with httpx.AsyncClient() as http:
            for _ in range(150):
                assert process.returncode is None, (tmp_path / "http.log").read_text()
                try:
                    await http.get(f"http://127.0.0.1:{port}/mcp")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
            else:
                pytest.fail("HTTP MCP server did not start")
            # DNS rebinding protections remain enabled on the loopback transport.
            denied = await http.post(f"http://127.0.0.1:{port}/mcp", headers={"Host": "evil.example", "Content-Type": "application/json"}, json={})
            assert denied.status_code == 421
            assert "Invalid Host" in denied.text
        async with Client(f"http://127.0.0.1:{port}/mcp") as c:
            board = await call(c, "create_canvas", name="http")
            await call(c, "add_panel", canvas="http", spec={"name": "status", "kind": "label"})
            bad = await c.call_tool("update_panel", {"canvas": "http", "name": "missing", "value": "x"})
            assert bad.is_error and "Unknown panel" in bad.content[0].text
            assert len((await call(c, "get_canvas", canvas="http"))["panels"]) == 1
        # HTTP sessions share the server's local canvas workspace.
        async with Client(f"http://127.0.0.1:{port}/mcp") as c:
            assert len((await call(c, "list_canvases"))["canvases"]) == 1
    finally:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            pytest.fail("HTTP server failed to shut down")
        log.close()
    assert process.returncode in (0, -15)
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", int(board["url"].rsplit(":", 1)[1]))) != 0


@pytest.mark.asyncio
async def test_shapes_only_and_single_panel_screenshots(tmp_path, broker_env):
    async with Client(create_server(tmp_path)) as c, async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            board = await call(c, "create_canvas", name="diagram")
            await call(c, "add_shape", canvas="diagram", spec={"name": "box", "kind": "geo",
                "options": {"x": 600, "y": 500, "w": 200, "h": 150, "text": "Only a shape", "fill": "solid"}})
            page = await browser.new_page()
            await page.goto(board["url"])
            await page.get_by_text("Only a shape", exact=True).wait_for()
            shot = await c.call_tool("screenshot", {"canvas": "diagram"})
            assert not shot.is_error, shot
            png = base64.b64decode(next(v.data for v in shot.content if v.type == "image"))
            assert struct.unpack(">II", png[16:24]) == (432, 332)
            await call(c, "add_panel", canvas="diagram", spec={"name": "panel", "kind": "label",
                "options": {"value": "Panel only"}, "layout": {"x": 0, "y": 0, "w": 200, "h": 84}})
            await page.get_by_text("Panel only", exact=True).wait_for()
            shot = await c.call_tool("screenshot", {"canvas": "diagram", "name": "panel"})
            assert not shot.is_error, shot
            png = base64.b64decode(next(v.data for v in shot.content if v.type == "image"))
            assert struct.unpack(">II", png[16:24]) == (432, 200)
            await call(c, "update_shape", canvas="diagram", name="box", properties={"text": "Updated shape"})
            await page.get_by_text("Updated shape", exact=True).wait_for()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_controls_and_custom_ui_send_events(tmp_path, broker_env):
    async with Client(create_server(tmp_path)) as c, async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            b = await call(c, "create_canvas", name="controls")
            specs = [
                {"name": "slider", "kind": "slider", "options": {"default": 10}},
                {"name": "toggle", "kind": "toggle", "options": {"options": ["Left", "Right"]}},
                {"name": "react", "kind": "react", "options": {"jsx": '<button onClick={() => canvas.send({answer:42})}>React action</button>'}},
                {"name": "html", "kind": "custom", "options": {"html": '<button onclick="canvas.send({answer:43})">HTML action</button>'}},
            ]
            for i, spec in enumerate(specs):
                spec["layout"] = {"x": 40, "y": 30 + i * 150, "w": 360, "h": 120}
                await call(c, "add_panel", canvas="controls", spec=spec)
            page = await browser.new_page(viewport={"width": 1200, "height": 1000})
            await page.goto(b["url"])
            slider = page.locator('[data-pc-panel-id] input[type="range"]')
            await slider.fill("65")
            await slider.dispatch_event("change")
            await page.get_by_text("Right", exact=True).click()
            await page.get_by_role("button", name="React action", exact=True).click()
            await page.frame_locator("iframe").get_by_role("button", name="HTML action", exact=True).click()
            for _ in range(100):
                events = await call(c, "get_events", canvas="controls")
                if {"slider", "toggle", "react", "html"}.issubset({e["name"] for e in events["events"]}):
                    break
                await asyncio.sleep(0.05)
            by_name = {e["name"]: e["value"] for e in events["events"]}
            assert by_name["slider"] == 65
            assert by_name["toggle"] == "Right"
            assert by_name["react"] == {"answer": 42}
            assert by_name["html"] == {"answer": 43}
            assert not (await call(c, "get_events", canvas="controls", after=events["latest"]))["events"]
            await call(c, "update_panel", canvas="controls", name="slider", value=25)
            await page.wait_for_function("document.querySelector('[data-pc-panel-id] input[type=range]').value === '25'")
            await call(c, "set_view", canvas="controls", x=0, y=0, zoom=0.8)
        finally:
            await browser.close()
