"""systemd --user unit state: the crash-loop rate limit (#138).

After enough failed restarts in a short window, systemd stops trying and a
plain 'restart' silently no-ops with "start request repeated too quickly"
— visible only by reading raw journal output. This module names that state
so a diagnostic tool can tell the operator to clear it (`reset-failed`)
before retrying, instead of leaving them to discover it by hand.

Read-only: nothing here mutates systemd state. The diagnostic ladder prints
the exact command to run; it does not run it, matching every other
'FAIL: do this next' step in the ladder rather than silently taking a
mutating action on the operator's behalf.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable

_TIMEOUT_SECONDS = 5.0

# (argv) -> (returncode, stdout). Injectable for tests.
Runner = Callable[[list[str]], tuple[int, str]]


def _default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(  # fixed binary, no shell, hard timeout
            argv, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return result.returncode, result.stdout


def systemd_available(run: Runner = _default_runner) -> bool:
    return shutil.which("systemctl") is not None


def unit_exists(unit: str, run: Runner = _default_runner) -> bool:
    """Whether a '--user' unit with this name is known to systemd at all —
    distinguishes 'not systemd-managed' from 'systemd-managed and fine'."""
    if shutil.which("systemctl") is None:
        return False
    code, out = run(["systemctl", "--user", "show", unit, "--property=LoadState"])
    return code == 0 and "LoadState=not-found" not in out


def unit_result(unit: str, run: Runner = _default_runner) -> str | None:
    """The unit's last 'Result=' property (e.g. 'success', 'exit-code',
    'start-limit-hit'), or None if it can't be determined."""
    if shutil.which("systemctl") is None:
        return None
    code, out = run(["systemctl", "--user", "show", unit, "--property=Result"])
    if code != 0:
        return None
    _, _, value = out.strip().partition("=")
    return value.strip() or None


def is_rate_limited(unit: str, run: Runner = _default_runner) -> bool:
    """True when the unit is stuck behind systemd's own restart rate limit —
    the state where 'systemctl restart' no-ops until 'reset-failed' runs."""
    return unit_result(unit, run=run) == "start-limit-hit"
