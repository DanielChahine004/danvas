"""The file-watching monitor behind ``Canvas.serve(hot_reload=True)``.

Split out of :mod:`pycanvas.canvas` because it touches none of the canvas state —
it only watches files and respawns the worker subprocess. The worker process runs
the real server; this side just restarts it on edits.
"""

import os
import secrets
import subprocess
import sys
import time


def run_monitor(main_file, tunnel=False, port=8000, tunnel_provider="cloudflared"):
    """Re-run ``main_file`` as a subprocess, restarting it on ``.py`` edits.

    This is the monitor side of ``serve(hot_reload=True)``: it never binds a
    port itself, just watches the script's directory (top-level ``.py`` files
    only) by polling mtimes, and respawns the worker subprocess on any change or
    addition/removal. The worker is launched with ``_PYCANVAS_RELOAD_WORKER=1``
    so its own ``serve(hot_reload=True)`` call skips straight to actually
    serving; ``_PYCANVAS_RELOAD_RESTART=1`` is added from the second launch
    onward so it doesn't reopen the browser (the frontend reconnects its
    existing websocket automatically).

    Before tearing the running worker down, each edit is pre-flighted in
    ``_PYCANVAS_RELOAD_CHECK`` mode (the script runs but serve() exits before
    binding). If that fails -- a syntax slip, a bad import, an exception in the
    module body -- the restart is skipped and the last working version keeps
    serving, so a half-finished edit doesn't take the canvas down.

    When ``tunnel`` is set, the public tunnel is opened *here*, in the monitor,
    rather than in each worker: the monitor outlives every restart, so one tunnel
    to ``port`` stays up across reloads — the public URL never changes and the
    provider (e.g. cloudflared) is started only once, instead of a fresh tunnel
    per edit (which churns quick-tunnel rate limits). Workers bind ``port``
    behind it; during the brief restart gap visitors see a momentary 502 until
    the new worker is up, then the browser reconnects on its own.
    """
    directory = os.path.dirname(os.path.abspath(main_file)) or "."

    def snapshot():
        out = {}
        for fname in os.listdir(directory):
            if fname.endswith(".py"):
                fpath = os.path.join(directory, fname)
                try:
                    out[fpath] = os.path.getmtime(fpath)
                except OSError:
                    pass
        return out

    def stop(proc):
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    base_env = dict(os.environ)
    base_env["_PYCANVAS_RELOAD_WORKER"] = "1"
    # One signing key shared by every worker this monitor spawns, so a viewer's
    # session cookie stays valid across restarts (no re-login on each edit). See
    # server._session_secret.
    base_env.setdefault("_PYCANVAS_RELOAD_SECRET", secrets.token_urlsafe(32))

    def spawn(restart):
        env = dict(base_env)
        if restart:
            env["_PYCANVAS_RELOAD_RESTART"] = "1"
        return subprocess.Popen([sys.executable, main_file, *sys.argv[1:]],
                                env=env)

    def script_ok():
        """True if the edited script imports/runs cleanly (pre-flight).

        Runs it in check mode -- the body executes but serve() exits before
        binding a port or starting threads, so this never collides with the
        worker that's still serving. On failure the captured stderr is surfaced
        so the error is visible in the console.
        """
        env = dict(base_env)
        env["_PYCANVAS_RELOAD_CHECK"] = "1"
        result = subprocess.run(
            [sys.executable, main_file, *sys.argv[1:]],
            env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stderr or "")
        return result.returncode == 0

    def wait_for_edit(last):
        """Block until a watched file changes; return the new snapshot."""
        while True:
            time.sleep(0.5)
            snap = snapshot()
            if snap != last:
                return snap

    print(f"PyCanvas hot reload: watching {directory} (*.py)")
    proc = spawn(restart=False)
    last = snapshot()
    # Open the tunnel once, here in the long-lived monitor, so it survives every
    # worker restart (stable URL, no per-edit churn). A failure to start is
    # non-fatal: keep serving locally and say so.
    persistent_tunnel = None
    if tunnel:
        from .tunnel import open_tunnel
        try:
            persistent_tunnel = open_tunnel(port, provider=tunnel_provider)
            print(f"PyCanvas public URL: {persistent_tunnel.url}"
                  "   <- share this; it stays put across hot reloads")
        except Exception as exc:  # noqa: BLE001 - surface any provider failure
            print(f"PyCanvas hot reload: could not start the {tunnel_provider} "
                  f"tunnel ({exc}); serving locally only.")
    try:
        while True:
            # Wait for either a file edit or the worker exiting on its own.
            changed = False
            while proc.poll() is None:
                time.sleep(0.5)
                snap = snapshot()
                if snap != last:
                    last = snap
                    changed = True
                    break
            if not changed:
                # Worker ended without an edit: a clean exit (e.g. a closed
                # desktop window) stops the monitor; a crash leaves it watching
                # so the next save can bring the canvas back.
                if proc.returncode in (0, None):
                    return
                print("PyCanvas hot reload: the app exited with an error; "
                      "waiting for the next save...")
                last = wait_for_edit(last)
            print("PyCanvas hot reload: change detected, checking...")
            if not script_ok():
                print("PyCanvas hot reload: the edit has an error -- keeping "
                      "the running version. Fix it and save again.")
                continue
            if proc.poll() is None:
                stop(proc)
            print("PyCanvas hot reload: restarting...")
            proc = spawn(restart=True)
    except KeyboardInterrupt:
        pass
    finally:
        if proc is not None and proc.poll() is None:
            stop(proc)
        if persistent_tunnel is not None:
            persistent_tunnel.stop()
