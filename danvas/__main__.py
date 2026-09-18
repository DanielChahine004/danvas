"""``python -m danvas`` / ``danvas`` — find, inspect and stop running canvases.

    danvas ps                 every running canvas: port, pid, owner script, viewers, uptime
    danvas kill 8010          stop the canvas on a port (its owner process, then the broker)
    danvas kill --all         stop every one

A served canvas is two processes: your script and the ``danvasd`` broker,
and the broker outlives the script on purpose (viewers keep the last state).
So a closed terminal can leave a canvas serving silently. Every broker
writes ``~/.danvas/running/<pid>.json`` when it binds (``$DANVAS_HOME``
overrides the directory); ``ps`` reads those, asks each ``/__health__`` who
owns it and who's looking, and prunes entries that no longer answer.
"""
import json
import os
import signal
import sys
import time
import urllib.request


def registry_dir():
    base = os.environ.get("DANVAS_HOME")
    if not base:
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or "."
        base = os.path.join(home, ".danvas")
    return os.path.join(base, "running")


def _health(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__health__", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def running(prune=True):
    """Live canvases as dicts: port, pid, host, started, cwd, sources, viewers."""
    d = registry_dir()
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        if not name.endswith(".json"):
            continue
        try:
            entry = json.load(open(path, encoding="utf-8"))
        except Exception:
            entry = None
        health = _health(entry.get("port")) if entry else None
        # the port answering but from a DIFFERENT broker (reused port): not this entry
        if health and entry and health.get("pid") not in (None, entry.get("pid")):
            health = None
        if not health:
            if prune and (not entry or not _pid_alive(entry.get("pid"))):
                try:
                    os.remove(path)
                except OSError:
                    pass
            continue
        out.append({**entry, **{k: health.get(k) for k in ("sources", "retained", "viewers", "danvasd")},
                    "_file": path})
    return out


def _fmt_uptime(started):
    try:
        s = int(time.time() - float(started))
    except (TypeError, ValueError):
        return "?"
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s % 60:02d}s"


def cmd_ps(args):
    rows = running()
    if not rows:
        print("no running canvases (registry: %s)" % registry_dir())
        return 0
    print(f"{'PORT':<6} {'BROKER':<8} {'OWNER':<28} {'VIEWERS':<8} {'UP':<8} CWD")
    for r in rows:
        owners = ", ".join(
            f"{s.get('script') or s.get('label')}({s.get('pid')})" if s.get("pid") else str(s.get("label"))
            for s in (r.get("sources") or [])) or ("retained: " + ", ".join(r.get("retained") or []) or "-")
        print(f"{r['port']:<6} {r['pid']:<8} {owners:<28} {r.get('viewers', 0):<8} "
              f"{_fmt_uptime(r.get('started')):<8} {r.get('cwd', '')}")
    return 0


def _terminate(pid, what):
    try:
        os.kill(int(pid), signal.SIGTERM)       # TerminateProcess on Windows
        print(f"stopped {what} (pid {pid})")
        return True
    except Exception as exc:
        print(f"could not stop {what} (pid {pid}): {exc}")
        return False


def cmd_kill(args):
    rows = running()
    if "--all" in args:
        targets = rows
    else:
        ports = set()
        for a in args:
            try:
                ports.add(int(a))
            except ValueError:
                print(f"usage: danvas kill <port> [<port> ...] | --all")
                return 2
        targets = [r for r in rows if r["port"] in ports]
        missing = ports - {r["port"] for r in targets}
        for p in sorted(missing):
            print(f"no running canvas on port {p}")
    if not targets:
        return 1
    for r in targets:
        # owner scripts first (they'd otherwise reconnect), then the broker
        for s in r.get("sources") or []:
            if s.get("pid") and int(s["pid"]) != os.getpid():
                _terminate(s["pid"], f"owner {s.get('label')}")
        _terminate(r["pid"], f"broker on port {r['port']}")
        try:
            os.remove(r["_file"])
        except OSError:
            pass
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "ps"
    if cmd in ("ps", "list", "ls"):
        return cmd_ps(argv[1:])
    if cmd in ("kill", "stop"):
        return cmd_kill(argv[1:])
    print(__doc__.strip())
    return 0 if cmd in ("-h", "--help", "help") else 2


if __name__ == "__main__":
    sys.exit(main())
