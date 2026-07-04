"""Dependency-free networking/filesystem helpers shared by the serving hub and
the broker dial-in paths.

These live here (not in server.py) so the broker path — which needs the LAN
address and safe upload resolution but NOT the FastAPI/uvicorn server stack —
can use them without dragging the server dependencies into a light,
client/broker-only install. server.py re-exports them, so there's one
definition for every caller.
"""

import os
import socket


def _lan_ip():
    """Best-effort LAN IP of this machine — the address other devices dial.

    Opens a UDP socket toward a public address to discover which local interface
    routes outward, then reads that interface's IP. No packets are actually sent,
    and it works offline as long as a network interface is up. Returns ``None``
    if no route can be determined.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _safe_upload_path(dest_root, filename):
    """Resolve ``filename`` to a path strictly inside ``dest_root``.

    The browser supplies the filename, so it's untrusted: ``basename`` strips any
    directory parts (``../`` included) and the result is re-checked against the
    realpath of the root, so an upload can never land outside the destination.
    Collisions get a ``-1``/``-2`` suffix rather than overwriting.
    """
    name = os.path.basename(filename) or "upload.bin"
    root = os.path.realpath(dest_root)
    target = os.path.realpath(os.path.join(root, name))
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("upload filename escapes the destination directory")
    if not os.path.exists(target):
        return target
    base, ext = os.path.splitext(target)
    i = 1
    while os.path.exists(f"{base}-{i}{ext}"):
        i += 1
    return f"{base}-{i}{ext}"
