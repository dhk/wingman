"""Per-instance self-restart, gated by that instance's own token (#133).

Deliberately narrow, per the design recorded when this was scoped: not a
shared control panel — every other instance's admin surface stays
read-only + launcher (see 'wingman.admin' and RFC-041). This module backs
exactly one mutating action, reachable only through an instance's own
existing capability token (the same one guarding '/mcp' and '/ui' — no
new credential), and restart is the only verb: covers "the build didn't
pick up" without an SSH round-trip, without reopening cross-account
start/stop/backup control.

Detection is systemd-only for a first cut. docs/SERVER.md's production
shape (a shape-B Unix account) always runs 'wingman-mcp.service' as a
per-account '--user' unit, so 'systemctl --user restart
wingman-mcp.service' is unambiguous and correct without any cross-account
reasoning — a '--user' unit is inherently scoped to the calling account.
An instance NOT managed by systemd (an interactive 'wingman-mcp --http',
or wingman-ctl's nohup-managed path) has no supervisor to bring it back up
after this process exits, so self-restart there is reported as
unavailable rather than guessed at with a process-replacement trick that
could leave the instance down instead of merely stale.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable

SYSTEMD_UNIT = "wingman-mcp.service"

# (argv) -> (returncode, stdout). Injectable for tests.
Runner = Callable[[list[str]], tuple[int, str]]
# (argv) -> None. Injectable for tests; production value fires-and-forgets
# a detached process, since the caller (this instance) may be killed
# mid-response once systemd acts on the restart.
Launcher = Callable[[list[str]], None]


def _default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(  # fixed binary, no shell, hard timeout
            argv, capture_output=True, text=True, timeout=5.0, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return result.returncode, result.stdout


def _default_launcher(argv: list[str]) -> None:
    subprocess.Popen(argv, start_new_session=True)  # fixed binary, no shell


def systemd_manages_this_instance(run: Runner = _default_runner) -> bool:
    """Best-effort: is 'wingman-mcp.service' active for this account?

    A '--user' unit is inherently scoped to the calling account, so
    'active' here is a safe, unambiguous signal without any cross-account
    reasoning — no other account's unit can answer this call.
    """
    if shutil.which("systemctl") is None:
        return False
    code, out = run(["systemctl", "--user", "is-active", SYSTEMD_UNIT])
    return code == 0 and out.strip() == "active"


def trigger_restart(launch: Launcher = _default_launcher) -> None:
    """Ask systemd to restart the unit. Fire-and-forget: this process may be
    killed before it observes the result, which is fine — 'systemctl
    restart' is itself the durable action, not this call's return value.
    """
    launch(["systemctl", "--user", "restart", SYSTEMD_UNIT])
