"""Clean monitor entry-point for ``serve(hot_reload=True)``.

Spawned as a subprocess by ``canvas._maybe_handoff_reload`` so the monitor
process never contains user code or user-launched daemon threads. The original
``python script.py`` process exits (killing its threads) after spawning this;
all file-watching and worker management happens here.

Usage (internal only):
    python -m danvas._hotreload_monitor <main_file> <port> <tunnel 0|1> <provider>

Extra ``serve(watch=...)`` globs ride in the ``_danvas_RELOAD_WATCH`` env var
(a JSON list) rather than argv, so they don't collide with the positional args.
"""

import json
import os
import sys

from danvas.hotreload import run_monitor

if __name__ == "__main__":
    main_file, port_s, tunnel_s, provider = sys.argv[1:5]
    watch = json.loads(os.environ.get("_danvas_RELOAD_WATCH", "[]"))
    run_monitor(main_file, port=int(port_s),
                tunnel=bool(int(tunnel_s)), tunnel_provider=provider,
                watch=watch)
