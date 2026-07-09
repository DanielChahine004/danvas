"""spawn_owned: a spawned broker/worker must die with its parent.

Two escalating scenarios, run with real processes:

- the parent exits NORMALLY without stopping the child (a block=False
  script falling off the end, an uncaught exception, Ctrl+C in user
  code): the atexit layer reaps.
- the parent is HARD-killed (an IDE stop button is TerminateProcess on
  Windows; kill -9 elsewhere) — no Python cleanup runs: the kernel
  layer (Windows kill-on-close Job Object / Linux PDEATHSIG) reaps.
  macOS has no parent-death signal, so the hard-kill case is skipped
  there by design.
"""

import os
import subprocess
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The parent: spawns an owned do-nothing child, prints its pid, then acts
# per scenario. The child sleeps far longer than the test — it only dies
# if something reaps it.
_PARENT = """
import sys, time
sys.path.insert(0, {root!r})
from danvas._procown import spawn_owned

child = spawn_owned([sys.executable, "-c", "import time; time.sleep(120)"])
print(child.pid, flush=True)
{tail}
"""


def _pid_alive(pid):
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000 | SYNCHRONIZE, False, pid)  # +QUERY_LIMITED
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _run_parent(tail):
    proc = subprocess.Popen(
        [sys.executable, "-c", _PARENT.format(root=_ROOT, tail=tail)],
        stdout=subprocess.PIPE, text=True)
    child_pid = int(proc.stdout.readline())
    assert _pid_alive(child_pid), "owned child never started"
    return proc, child_pid


def _assert_dies(pid, within=15):
    deadline = time.time() + within
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.25)
    pytest.fail(f"owned child {pid} outlived its parent")


def test_child_reaped_on_normal_parent_exit():
    # parent falls off the end (after a beat, so the test can observe the
    # child alive first) — atexit must terminate the child
    proc, child_pid = _run_parent("time.sleep(2)")
    proc.wait(timeout=30)
    _assert_dies(child_pid)


@pytest.mark.skipif(sys.platform == "darwin",
                    reason="no parent-death signal on macOS; atexit-only there")
def test_child_reaped_on_hard_parent_kill():
    # parent parks; we TerminateProcess/SIGKILL it — the kernel layer reaps
    proc, child_pid = _run_parent("time.sleep(120)")
    proc.kill()          # TerminateProcess on Windows: the IDE stop button
    proc.wait(timeout=30)
    _assert_dies(child_pid)
