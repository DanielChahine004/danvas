import os
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from danvas.remote import _find_danvasd


@pytest.fixture
def broker_env(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("PATH", str(root / ".venv/bin") + os.pathsep + os.environ["PATH"])
    if _find_danvasd() is None:
        pytest.fail("MCP integration tests require a real danvasd binary on PATH or DANVASD")
