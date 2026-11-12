"""Preview a danvas app from local folders before pushing: serves the page and
answers, for the repos named here, the three GitHub calls the page makes.

    python web/build.py
    python web/dev_server.py --repo you/notes=../notes [--repo you/pkg=../pkg] [--port 8765]

then open http://127.0.0.1:8765/?repo=you/notes. Any other repo is still read
from GitHub. The page polls local repos every 2 s, so a saved edit shows within
a few seconds.
"""
import argparse
import hashlib
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

SKIP = {".git", "node_modules", "__pycache__", ".venv", ".pixi", "build", ".mypy_cache"}
HERE = Path(__file__).parent


def walk(root: Path):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for f in sorted(files):
            p = Path(dirpath) / f
            st = p.stat()
            rel = p.relative_to(root).as_posix()
            yield rel, hashlib.sha1(f"{rel}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest(), st.st_size


def stamp(root: Path) -> str:
    h = hashlib.sha1()
    for rel, sha, _ in walk(root):
        h.update(f"{rel}:{sha}\n".encode())
    return h.hexdigest()


def handler(repos: dict, site: Path):
    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(site), **k)

        def log_message(self, *a):
            pass

        def _json(self, obj, etag=None):
            if etag and self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.end_headers()
                return
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if etag:
                self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            if path == "/_gh/repos.json":
                return self._json(sorted(repos))
            if path.startswith("/_gh/api/repos/"):
                parts = path[len("/_gh/api/repos/"):].split("/")
                name, rest = "/".join(parts[:2]), parts[2:]
                root = repos.get(name)
                if root is None:
                    return self.send_error(404)
                if not rest:
                    return self._json({"default_branch": "main"})
                if rest[0] == "commits":
                    s = stamp(root)
                    return self._json({"sha": s, "commit": {"tree": {"sha": s}}}, etag=f'"{s}"')
                if rest[:2] == ["git", "trees"]:
                    return self._json({"tree": [{"path": rel, "type": "blob", "sha": sha, "size": size}
                                                for rel, sha, size in walk(root)]})
                return self.send_error(404)
            if path.startswith("/_gh/raw/"):
                parts = path[len("/_gh/raw/"):].split("/")
                root = repos.get("/".join(parts[:2]))
                f = (root / "/".join(parts[3:])) if root else None
                if not f or not f.is_file() or root.resolve() not in f.resolve().parents:
                    return self.send_error(404)
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            return super().do_GET()

    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", default=[], help="owner/name=local/folder (repeatable)")
    ap.add_argument("--site", default=str(HERE / "site"))
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    repos = {}
    for spec in a.repo:
        name, _, folder = spec.partition("=")
        repos[name] = Path(folder).resolve()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), handler(repos, Path(a.site)))
    print(f"http://127.0.0.1:{a.port}/?repo={next(iter(repos), 'owner/name')}  (local: {', '.join(repos) or 'none'})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
