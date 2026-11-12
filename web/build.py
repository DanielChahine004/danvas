"""Assemble the static danvas page in site/.

    python web/build.py [--danvas .] [--out web/site]

site/
  index.html, boot.js   the page: reads a repo's danvas.toml, fetches the app, runs it
  runtime.zip           danvas's Python (no frontend), danvas/static.py included
  danvas/               danvas's built frontend (frontend/dist), unchanged

No app is baked in: the page fetches each app's code from GitHub (or from the
repo it is pointed at) when it opens.
"""
import argparse
import shutil
import zipfile
from pathlib import Path

HERE = Path(__file__).parent          # web/
SKIP_DIRS = {"__pycache__", "frontend", "node_modules", "tests"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--danvas", default=str(HERE.parent), help="danvas checkout (holds danvas/ and danvas/frontend/dist)")
    ap.add_argument("--out", default=str(HERE / "site"))
    a = ap.parse_args()
    pkg = Path(a.danvas) / "danvas"
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    n = 0
    with zipfile.ZipFile(out / "runtime.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pkg.rglob("*")):
            rel = f.relative_to(pkg.parent)
            if f.is_file() and not SKIP_DIRS & set(rel.parts):
                z.write(f, rel.as_posix())
                n += 1
    shutil.copytree(pkg / "frontend" / "dist", out / "danvas", ignore=shutil.ignore_patterns("*.br", "*.gz"))
    for f in ("index.html", "boot.js", "worker.js"):
        shutil.copy(HERE / f, out / f)
    print(f"{out}: runtime.zip ({n} files, {(out / 'runtime.zip').stat().st_size // 1024} KB), danvas frontend")


if __name__ == "__main__":
    main()
