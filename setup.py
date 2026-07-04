"""Build shim: make the wheel PLATFORM-SPECIFIC when a danvasd binary is
bundled (danvas/_bin/), so a per-OS wheel ships the broker inside it (offline,
no runtime download). With no binary present the build stays a pure
``py3-none-any`` wheel — the automatic fallback for platforms we don't ship a
binary for. Everything else lives in pyproject.toml.

The wheel-building CI (a v* tag) does, per OS:
    cargo build --release  ->  copy into danvas/_bin/  ->  python -m build
which lands here and produces e.g. danvas-<v>-py3-none-win_amd64.whl.
"""

import os

from setuptools import setup

_BIN_DIR = os.path.join(os.path.dirname(__file__), "danvas", "_bin")
_HAS_BINARY = os.path.isdir(_BIN_DIR) and any(
    n.startswith("danvasd") for n in os.listdir(_BIN_DIR))

cmdclass = {}
if _HAS_BINARY:
    try:
        from wheel.bdist_wheel import bdist_wheel as _bdist_wheel

        class bdist_wheel(_bdist_wheel):
            def finalize_options(self):
                super().finalize_options()
                # A bundled native binary makes this platform-specific, but the
                # package has no compiled *extension*, so keep the broad "py3"
                # ABI tag and only pin the platform.
                self.root_is_pure = False

            def get_tag(self):
                _py, _abi, plat = super().get_tag()
                return "py3", "none", plat

        cmdclass["bdist_wheel"] = bdist_wheel
    except ImportError:
        pass

setup(cmdclass=cmdclass)
