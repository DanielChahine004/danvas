"""Build a standalone executable from a danvas script via PyInstaller.

Used by :meth:`danvas.Canvas.bake` and the ``python -m danvas.bake`` CLI.
The build bundles the entry script, the danvas backend, and the pre-built
frontend (``pycanvas/frontend/dist``) into one app that runs the canvas in a
native window with nothing else installed on the target machine.

The argument list is assembled by the pure :func:`_build_args` helper so it can
be tested without PyInstaller present; :func:`build_app` resolves/validates
paths and hands those args to PyInstaller.
"""

import argparse
import os
import re
import sys

# Where the frontend is embedded inside the executable. Deliberately NOT under
# `pycanvas/` — a data dir by that name would shadow the real package as a
# namespace dir and break `import danvas`. server._dist_dir() reads it back
# from here (`sys._MEIPASS/pcframe/dist`) when frozen.
_FRONTEND_DEST = "pcframe/dist"


def _frontend_dist():
    return os.path.join(os.path.dirname(__file__), "frontend", "dist")


# Heavy optional dependencies that specific components import only at runtime,
# and dynamically (via importlib) so PyInstaller's analysis can't see them — see
# the imports in components/audio.py, video.py and image.py. We bundle them back
# only when the canvas actually uses the component, so a slider-only app doesn't
# pay for numpy/OpenCV/Pillow (numpy alone is ~60 MB). Keyed by component *class*
# name (Image renders as a "Custom" panel, so the frontend type can't tell them
# apart). ``cv2``/``PIL`` are collected whole; ``numpy`` means a conda/MKL-aware
# collect.
_COMPONENT_PACKAGES = {
    "AudioFeed": {"numpy"},
    "VideoFeed": {"cv2"},
    "Image": {"PIL"},
}


def _packages_for_components(component_types):
    """Map a set of component class names to the extra packages they need bundled."""
    needs = set()
    for t in component_types or ():
        needs |= _COMPONENT_PACKAGES.get(t, set())
    return needs


# Optional dependencies a *baked* app never needs — it's a standalone, local
# desktop app — but which, left in, drag enormous unrelated trees into the build:
#   * pycloudflared (the [tunnel] extra's binary downloader) imports tqdm.auto,
#     which statically pulls tqdm's notebook/gui/pandas integrations and so
#     cascades into ipywidgets -> IPython, matplotlib, and pandas -> scipy ->
#     torch -> networkx/tensorboard. A frozen app opens no public tunnel.
#   * IPython (also reachable via python-dotenv's dotenv.ipython) pulls the
#     Jupyter stack. A frozen app runs no notebook kernel.
#   * ipywidgets is optionally imported by rich.live (reached via
#     fastapi -> pydantic -> rich) for Jupyter display, and pulls ipykernel ->
#     pyzmq, comm, and matplotlib. A frozen app renders no Jupyter widgets.
#   * PyInstaller itself is dragged in by danvas.bake (which imports it to
#     build), and its splash support pulls Pillow -> numpy. A frozen app never
#     rebuilds, so neither PyInstaller nor danvas.bake belongs in it.
# Excluding these severs the cascade without excluding numpy/pandas/etc.
# themselves, so an app that imports them directly still bundles them. The
# caller can force one back by naming it in ``include``.
_DEFAULT_EXCLUDES = (
    "pycloudflared", "IPython", "ipywidgets", "PyInstaller", "danvas.bake",
)


# Component-only optional deps: their sole realistic use in a canvas app is via
# the component that needs them (Pillow for Image, OpenCV for VideoFeed). When no
# such component is on the canvas we exclude them, so a transitive/typing import
# can't drag them — and their own heavy deps — in. Pillow is the worst offender:
# pygments' image formatter (reached via Markdown -> codehilite -> pygments) and
# others import it, and PIL._typing then imports numpy.typing, pulling all of
# numpy (~30 MB). numpy itself is deliberately NOT auto-excluded — scripts
# commonly import it directly — but with Pillow gone its transitive path is cut.
_EXCLUDABLE_COMPONENT_PACKAGES = ("PIL", "cv2")


# Core MKL/OpenMP runtime DLLs a conda NumPy depends on. They live in
# <prefix>/Library/bin, outside the numpy package, so PyInstaller's analysis
# can't see them and the frozen app dies with "mkl_intel_thread...dll not found".
_CONDA_MKL_DLLS = (
    "mkl_intel_thread.2", "mkl_core.2", "mkl_def.2",
    "mkl_avx2.2", "mkl_avx512.2",
    "mkl_vml_def.2", "mkl_vml_avx2.2", "mkl_vml_avx512.2",
    "libiomp5md",
)


def _conda_mkl_binaries():
    """``--add-binary`` specs for a conda NumPy's MKL DLLs (empty off conda).

    A pip/venv NumPy bundles OpenBLAS inside ``numpy.libs``, which PyInstaller's
    hook already collects — nothing to do. Conda's MKL build keeps its DLLs in
    the environment's ``Library/bin`` instead, so we locate and embed them.
    """
    libbin = os.path.join(sys.prefix, "Library", "bin")
    if not os.path.isdir(libbin):
        return []
    specs = []
    for stem in _CONDA_MKL_DLLS:
        path = os.path.join(libbin, stem + ".dll")
        if os.path.isfile(path):
            specs.append(f"{path}{os.pathsep}.")
    return specs


def _failure_hint(exc):
    """Turn a raw PyInstaller failure into actionable guidance for the user."""
    msg = str(exc)
    lines = ["danvas failed to build the desktop app."]
    # PyInstaller analyses each dependency in an isolated subprocess; a package
    # that crashes on import takes the build down with it. Surface its name.
    m = re.search(r"_collect\w+\(\) with args=\('([\w.]+)'", msg)
    if m:
        culprit = m.group(1)
        lines.append(
            f"  A dependency ('{culprit}') crashed while being analysed. If your "
            f"app doesn't use it, exclude it: bake(exclude=['{culprit}']) "
            f"(or `--exclude {culprit}` on the CLI)."
        )
    lines += [
        "Common causes:",
        "  - the frontend isn't built: cd pycanvas/frontend && npm run build",
        "  - a broken/optional dependency crashes on import during analysis — "
        "exclude it with bake(exclude=[...])",
        "  - missing PyInstaller/pywebview: pip install 'danvas[desktop]'",
        f"Original error: {msg[:400]}",
    ]
    return "\n".join(lines)


def _build_args(entry, name, dist_src, pkg_root, *, icon=None, onefile=True,
                windowed=True, distpath="dist", workpath="build", clean=False,
                add_data=None, binaries=None, exclude=None, include=None,
                hidden_imports=None, collect_numpy=False, extra_args=None):
    """Assemble the PyInstaller argument list (pure; no filesystem side effects).

    ``entry``/``dist_src``/``icon``/``pkg_root`` are expected already absolute.
    ``dist_src`` is the frontend ``dist`` folder to embed at :data:`_FRONTEND_DEST`;
    ``pkg_root`` is the directory containing the ``danvas`` package, added to
    the search path so an editable (``pip install -e .``) install is found.
    """
    sep = os.pathsep  # ';' on Windows, ':' elsewhere — PyInstaller's add-data sep
    args = [
        entry,
        "--name", name,
        "--noconfirm",
        "--distpath", distpath,
        "--workpath", workpath,
        "--specpath", workpath,
        # Find danvas itself even when it's installed editable (-e), where the
        # default analysis can't resolve the package location.
        "--paths", pkg_root,
        # Embed the built frontend so the server serves it from inside the app.
        "--add-data", f"{dist_src}{sep}{_FRONTEND_DEST}",
        # danvas imports some modules lazily (tunnel, components); collect them
        # all so analysis can't miss one. uvicorn/websockets load their protocol
        # backends dynamically, so collect those wholesale too.
        "--collect-submodules", "danvas",
        "--collect-all", "uvicorn",
        "--collect-all", "websockets",
    ]
    if collect_numpy:
        # Only when the app actually uses numpy (e.g. an AudioFeed). numpy loads
        # C-extension submodules dynamically that the default analysis misses on
        # newer versions, so pull the whole package in.
        args += ["--collect-submodules", "numpy"]
    args.append("--onefile" if onefile else "--onedir")
    if windowed:
        args.append("--windowed")
    if clean:
        args.append("--clean")
    if icon:
        args += ["--icon", icon]
    for d in add_data or []:
        args += ["--add-data", d]
    for b in binaries or []:
        args += ["--add-binary", b]
    for m in exclude or []:
        args += ["--exclude-module", m]
    for pkg in include or []:
        # Pull in a package the import analysis can't reach (dynamic/plugin
        # imports), with its submodules, data, and binaries.
        args += ["--collect-all", pkg]
    for h in hidden_imports or []:
        args += ["--hidden-import", h]
    args += list(extra_args or [])
    return args


def build_app(entry, name=None, *, icon=None, onefile=True, windowed=True,
              distpath="dist", workpath="build", clean=False, exclude=None,
              include=None, components=None, add_data=None, hidden_imports=None,
              extra_args=None):
    """Build ``entry`` into a standalone executable, returning its path.

    PyInstaller bundles only the packages your script actually imports (not the
    whole environment) plus what its hooks add — so you normally specify nothing.
    ``include`` force-adds packages the static analysis can't see (dynamic or
    plugin imports); ``exclude`` skips modules, useful when a broken/unused
    optional dependency would otherwise crash the build.

    ``components`` is the set of component type names the canvas uses; their
    heavy optional dependencies (numpy for AudioFeed, OpenCV for VideoFeed) are
    imported dynamically and so invisible to PyInstaller — we bundle them back
    only when the matching component is present, keeping a slider-only app from
    dragging in numpy/OpenCV. Requesting numpy (an AudioFeed, or ``numpy`` in
    ``include``) also auto-detects and bundles the MKL DLLs a conda NumPy needs.
    """
    entry = os.path.abspath(entry)
    if not os.path.isfile(entry):
        raise FileNotFoundError(f"entry script not found: {entry}")
    name = name or os.path.splitext(os.path.basename(entry))[0]

    # Decide which heavy optional deps to bundle: those the canvas's components
    # need, plus anything the caller force-added via ``include``. numpy is
    # special (conda MKL stack); other packages (cv2) just need --collect-all.
    include = list(include or [])
    needs = _packages_for_components(components)
    collect_numpy = "numpy" in needs or "numpy" in include
    include = [p for p in include if p != "numpy"]
    for pkg in sorted(needs - {"numpy"}):
        if pkg not in include:
            include.append(pkg)

    # Drop dev/tunnel-only deps that would otherwise pull the whole scientific
    # and notebook stack (see _DEFAULT_EXCLUDES), unless the caller asked for one.
    exclude = list(exclude or [])
    for mod in _DEFAULT_EXCLUDES:
        if mod not in exclude and mod not in include:
            exclude.append(mod)
    # Drop component-only media deps the canvas doesn't use (see the constant).
    for mod in _EXCLUDABLE_COMPONENT_PACKAGES:
        if mod not in needs and mod not in include and mod not in exclude:
            exclude.append(mod)

    dist_src = _frontend_dist()
    if not os.path.isdir(dist_src):
        raise RuntimeError(
            f"the frontend is not built ({dist_src} is missing) — build it with "
            "`cd pycanvas/frontend && npm install && npm run build`"
        )

    try:
        import PyInstaller.__main__ as pyi
    except ImportError as exc:
        raise RuntimeError(
            "building a desktop app requires PyInstaller — install the desktop "
            "extra: pip install 'danvas[desktop]'"
        ) from exc

    pkg_root = os.path.dirname(os.path.dirname(__file__))  # contains pycanvas/
    # The conda MKL DLLs are only relevant when numpy itself is bundled.
    binaries = _conda_mkl_binaries() if collect_numpy else []
    args = _build_args(
        entry, name, dist_src, pkg_root,
        icon=os.path.abspath(icon) if icon else None,
        onefile=onefile, windowed=windowed, distpath=distpath,
        workpath=workpath, clean=clean, add_data=add_data,
        binaries=binaries, exclude=exclude, include=include,
        hidden_imports=hidden_imports, collect_numpy=collect_numpy,
        extra_args=extra_args,
    )

    # On conda, MKL and a dependency's own bundled OpenMP (e.g. torch) can both
    # load libiomp5md and abort analysis with "OMP: Error #15". Allow the
    # duplicate for the build subprocesses so such packages can be imported.
    prev_kmp = os.environ.get("KMP_DUPLICATE_LIB_OK")
    if binaries:
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    try:
        pyi.run(args)
    except (Exception, SystemExit) as exc:
        raise RuntimeError(_failure_hint(exc)) from exc
    finally:
        if binaries:
            if prev_kmp is None:
                os.environ.pop("KMP_DUPLICATE_LIB_OK", None)
            else:
                os.environ["KMP_DUPLICATE_LIB_OK"] = prev_kmp

    out = os.path.join(distpath, name + (".exe" if os.name == "nt" else ""))
    return os.path.abspath(out)


def _main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m danvas.bake",
        description="Package a danvas script into a standalone desktop app.",
    )
    parser.add_argument("entry", help="the Python script to package")
    parser.add_argument("--name", help="executable/app name (default: script name)")
    parser.add_argument("--icon", help="path to a .ico/.icns icon")
    parser.add_argument("--onedir", action="store_true",
                        help="emit a folder instead of a single file (faster start)")
    parser.add_argument("--console", action="store_true",
                        help="keep the console window (default: windowed)")
    parser.add_argument("--distpath", default="dist", help="output directory")
    parser.add_argument("--clean", action="store_true",
                        help="clear the PyInstaller cache before building")
    parser.add_argument("--exclude", action="append", metavar="MODULE",
                        help="module to skip during analysis (repeatable); use "
                             "for a broken/unused dependency that crashes the build")
    parser.add_argument("--include", action="append", metavar="PACKAGE",
                        help="force-bundle a package the analysis can't reach, "
                             "e.g. a dynamic/plugin import (repeatable)")
    args = parser.parse_args(argv)
    out = build_app(
        args.entry, name=args.name, icon=args.icon,
        onefile=not args.onedir, windowed=not args.console,
        distpath=args.distpath, clean=args.clean,
        exclude=args.exclude, include=args.include,
    )
    print(f"danvas baked: {out}")


if __name__ == "__main__":
    _main()