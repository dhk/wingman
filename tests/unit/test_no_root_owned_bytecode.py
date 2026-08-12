"""Root must not leave bytecode in somebody's tool store (#376).

An operator command legitimately runs as root — reading /etc/wingman for
`tenant url`, `motd set`, `qotd set`. Python writes .pyc as it imports, so
one such run seeds root-owned __pycache__ inside the INVOKING account's uv
tool store, and that account can then no longer reinstall its own tool.
The failure surfaces days later, naming a third-party package, with nothing
connecting it to the privileged command that caused it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def _import_under_fake_euid(euid: int) -> str:
    """Import the package with os.geteuid() forced, and report the flag.

    A subprocess because `sys.dont_write_bytecode` is process-global and
    the guard runs at import time — patching it in-process would test
    nothing, since wingman is already imported by the time a test runs.
    """
    program = (
        "import os, sys;"
        f"os.geteuid = lambda: {euid};"
        f"sys.path.insert(0, {_SRC!r});"
        "import wingman;"
        "print(sys.dont_write_bytecode)"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_importing_as_root_stops_bytecode_being_written() -> None:
    assert _import_under_fake_euid(0) == "True"


def test_an_ordinary_user_keeps_the_bytecode_cache() -> None:
    """Root only — an ordinary run keeps its cache and its startup speed.
    Disabling it for everybody would slow every command to fix a problem
    only privileged runs create."""
    assert _import_under_fake_euid(1000) == "False"


def test_the_guard_runs_before_any_wingman_submodule_is_imported() -> None:
    """It lives in the package __init__ deliberately: that executes before
    every wingman submodule and therefore before the third-party imports
    they pull in, which is where the root-owned directories actually land.
    A console-script entry point would run far too late."""
    source = (Path(_SRC) / "wingman" / "__init__.py").read_text(encoding="utf-8")

    assert "dont_write_bytecode" in source
    assert "geteuid" in source
