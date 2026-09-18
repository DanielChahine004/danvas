"""`python -m danvas ps` / `kill`: find and stop running canvases.

The broker writes ~/.danvas/running/<pid>.json ($DANVAS_HOME overrides) when
it binds; /__health__ names the owning processes (each source announces its
pid + script) and counts viewers. `ps` joins the two and prunes dead entries;
`kill <port>` stops the owner script(s) then the broker.
"""

import io
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import redirect_stdout

import pytest

import danvas
from danvas import __main__ as cli

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _danvasd():
    exe = os.environ.get("DANVASD")
    if exe and os.path.isfile(exe):
        return exe
    name = "danvasd.exe" if os.name == "nt" else "danvasd"
    for rel in ("broker/target/release", "broker/target/debug"):
        p = os.path.join(_ROOT, rel, name)
        if os.path.isfile(p):
            return p
    return None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DANVAS_HOME", str(tmp_path))
    return tmp_path


def test_ps_finds_a_running_canvas_and_kill_stops_it(home, monkeypatch):
    binary = _danvasd()
    if binary is None:
        pytest.skip("danvasd not built")
    port = _free_port()
    # an owner script in its own process, serving via a broker it spawns
    script = home / "owner_script.py"
    script.write_text(
        "import danvas\n"
        "c = danvas.Canvas(); c.label('hello')\n"
        f"c.serve(port={port}, open_browser=False)\n", encoding="utf-8")
    env = dict(os.environ, DANVAS_HOME=str(home), DANVASD=binary, PYTHONPATH=_ROOT)
    owner = subprocess.Popen([sys.executable, str(script)], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _wait(lambda: cli._health(port) is not None, 30), "broker never answered"
        assert _wait(lambda: any(s.get("pid") for s in (cli._health(port) or {}).get("sources", [])), 15)
        rows = cli.running()
        assert [r["port"] for r in rows] == [port], rows
        (row,) = rows
        assert row["pid"] == cli._health(port)["pid"]
        src = row["sources"][0]
        # (owner.pid may be a launcher wrapper's; the source reports the real interpreter)
        assert src["pid"] and cli._pid_alive(src["pid"]) and src["script"] == "owner_script.py", src
        out = io.StringIO()
        with redirect_stdout(out):
            assert cli.main(["ps"]) == 0
        text = out.getvalue()
        assert str(port) in text and "owner_script.py" in text, text

        # a stale entry (dead pid, nothing on its port) is pruned by ps
        stale = home / "running" / "999999.json"
        stale.write_text(json.dumps({"pid": 999999, "port": _free_port()}), encoding="utf-8")
        cli.running()
        assert not stale.exists()

        out = io.StringIO()
        with redirect_stdout(out):
            assert cli.main(["kill", str(port)]) == 0
        assert "broker on port" in out.getvalue(), out.getvalue()
        assert _wait(lambda: owner.poll() is not None, 10), "owner script still running"
        assert _wait(lambda: cli._health(port) is None, 10), "broker still answering"
        assert cli.running() == []
    finally:
        if owner.poll() is None:
            owner.kill()
        # belt and braces: no broker left on that port
        h = cli._health(port)
        if h and h.get("pid"):
            cli._terminate(h["pid"], "leftover broker")


def test_kill_unknown_port_reports_it(home):
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(["kill", "1"]) == 1
    assert "no running canvas on port 1" in out.getvalue()
    with redirect_stdout(io.StringIO()):
        assert cli.main(["ps"]) == 0
        assert cli.main(["--help"]) == 0
        assert cli.main(["bogus"]) == 2
