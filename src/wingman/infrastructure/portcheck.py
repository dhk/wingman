"""Who actually holds a TCP listen port, across Unix accounts (#137, #138).

'wingman mcp status' verifies a pidfile's recorded pid is alive and named
right, but never that it is bound to the port it claims — a stray process
started with the wrong '--port' passes that check while the real port sits
unbound (or squatted by someone else entirely). This module answers the
question status doesn't: which process, if any, is actually LISTENing on a
given port, regardless of which Unix account owns it or is asking.

'ss' is preferred over 'lsof': an incident used 'lsof -i :<port>' (no '-n')
and it hung, almost certainly on a reverse-DNS lookup for an unrelated
connection. 'ss' never resolves names. Where 'ss' is unavailable (macOS has
no 'ss'), fall back to 'lsof -nP …' — '-n'/'-P' suppress the DNS/service
lookups that caused the original hang — with a hard subprocess timeout as a
backstop either way, so a diagnostic tool can never itself hang.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

_TIMEOUT_SECONDS = 5.0

# (argv) -> (returncode, stdout). Injectable so tests never touch the real
# process table or a real 'ss'/'lsof' binary.
Runner = Callable[[list[str]], tuple[int, str]]


def _default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(  # fixed binaries, no shell, hard timeout
            argv, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return result.returncode, result.stdout


@dataclass(frozen=True)
class PortOwner:
    pid: int
    command: str | None  # None when unreadable — usually a different account
    user: str | None  # None when unreadable

    @property
    def readable_identity(self) -> bool:
        return self.command is not None

    def describe(self) -> str:
        if self.command:
            who = f" (user {self.user})" if self.user else ""
            return f"pid {self.pid}: {self.command}{who}"
        return f"pid {self.pid} — command unreadable (likely a different Unix account)"


def _parse_ss_pid(output: str) -> int | None:
    # ss -H -tlnp output line, e.g.:
    # LISTEN 0  4096  0.0.0.0:8787  0.0.0.0:*  users:(("wingman-mcp",pid=1234,fd=7))
    match = re.search(r"pid=(\d+)", output)
    return int(match.group(1)) if match else None


def _ss_listener(port: int, run: Runner) -> int | None:
    if shutil.which("ss") is None:
        return None
    code, out = run(["ss", "-H", "-tlnp", f"sport = :{port}"])
    if code != 0 or not out.strip():
        return None
    return _parse_ss_pid(out)


def _parse_lsof_pid(output: str) -> int | None:
    # lsof -nP -iTCP:<port> -sTCP:LISTEN output, e.g.:
    # COMMAND   PID USER   FD   TYPE ...
    # wingman-  123 dave    7u  IPv4 ...
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    fields = lines[1].split()
    if len(fields) < 2 or not fields[1].isdigit():
        return None
    return int(fields[1])


def _lsof_listener(port: int, run: Runner) -> int | None:
    if shutil.which("lsof") is None:
        return None
    # -n: no hostname resolution, -P: no port-name resolution — the two
    # flags whose absence caused the original hang.
    code, out = run(["lsof", "-n", "-P", f"-iTCP:{port}", "-sTCP:LISTEN"])
    if code != 0:
        return None
    return _parse_lsof_pid(out)


def _ps_identity(pid: int, run: Runner) -> tuple[str | None, str | None]:
    """(command, user) for a pid, best-effort — empty/None when unreadable.

    On a normal box 'ps' can see every account's command line; some hardened
    configurations (e.g. Linux hidepid=2) restrict it to the caller's own
    processes, which is exactly the case this whole module exists to
    surface rather than silently misreport.
    """
    code, out = run(["ps", "-o", "user=,command=", "-p", str(pid)])
    if code != 0 or not out.strip():
        return None, None
    line = out.strip().splitlines()[0]
    parts = line.split(None, 1)
    if len(parts) != 2:
        return None, None
    return parts[1], parts[0]


def find_port_owner(port: int, run: Runner = _default_runner) -> PortOwner | None:
    """The pid LISTENing on 'port', or None if nothing is.

    Tries 'ss' first (Linux; never hangs, never resolves DNS), then falls
    back to 'lsof' with the flags that avoid the DNS-lookup hang. Identity
    (command, user) is resolved separately via 'ps' so a cross-account
    owner is still reported as *present*, even when its details are
    unreadable — 'someone is squatting this port and I can't tell who'
    is itself the actionable finding.
    """
    pid = _ss_listener(port, run)
    if pid is None:
        pid = _lsof_listener(port, run)
    if pid is None:
        return None
    command, user = _ps_identity(pid, run)
    return PortOwner(pid=pid, command=command, user=user)
