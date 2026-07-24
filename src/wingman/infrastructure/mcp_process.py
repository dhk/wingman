"""HTTP MCP server process management: pidfile, status, stop (RFC-032).

'wingman mcp stop' manages exactly one process — the HTTP-transport server
the user started themselves. Stdio servers spawned by Claude clients are
those clients' children and are never touched: killing them would break
live sessions wingman doesn't own. The pidfile makes stop deterministic,
and every read verifies the recorded pid is alive AND still a wingman-mcp
before trusting it, so a stale file from a crash or reboot can never kill
an innocent process that reused the pid.

One narrow exception to pidfile-only: an HTTP server running WITHOUT a
pidfile (pre-RFC-032 build, or SIGKILL before atexit) holds the port,
blocks every restart with EADDRINUSE, and is invisible to both the
pidfile and wingman-ctl's stdio listing. orphan_http_pids() finds those —
matching only 'wingman-mcp … --http' commands on OUR port, never stdio
servers, never other instances' ports — so status can name them and stop
can clear them.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger

_logger = get_logger("infrastructure.mcp_process")

PIDFILE_NAME = "mcp-http.pid"
DEFAULT_PORT = 8787

# Injectable for tests: pid -> that process's command line ('' when gone).
CommandOf = Callable[[int], str]
# Injectable for tests: the whole process table as (pid, command) pairs.
ListProcesses = Callable[[], list[tuple[int, str]]]
# Injectable for tests: pid -> that process's owning uid, None if unknown.
OwnerOf = Callable[[int], int | None]


def _ps_command_of(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _ps_all() -> list[tuple[int, str]]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    table: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            table.append((int(parts[0]), parts[1]))
    return table


def _ps_owner_of(pid: int) -> int | None:
    """The pid's owning uid, or None when it can't be determined (gone,
    ps failure) — treated as 'don't know', never as 'ours'."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "uid="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = result.stdout.strip()
    return int(text) if result.returncode == 0 and text.isdigit() else None


def _is_wingman_http(command: str) -> bool:
    """True for a 'wingman-mcp … --http' command line.

    The wingman-mcp token must be the executable — first argument, or right
    after an interpreter/wrapper ('python …/wingman-mcp', 'uv run
    wingman-mcp') — with a literal --http after it. A command that merely
    quotes the words deeper in its argument list (a shell script, an
    editor) must never match: stop kills what this matches.
    """
    parts = command.split()
    for index, part in enumerate(parts[:3]):
        if part.rsplit("/", 1)[-1] == "wingman-mcp":
            return "--http" in parts[index + 1 :]
    return False


def _command_port(command: str) -> int:
    """The --port a wingman-mcp command line serves on (default when absent)."""
    parts = command.split()
    for index, part in enumerate(parts):
        if part == "--port" and index + 1 < len(parts):
            try:
                return int(parts[index + 1])
            except ValueError:
                return DEFAULT_PORT
        if part.startswith("--port="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return DEFAULT_PORT
    return DEFAULT_PORT


def pidfile_path(config: Config) -> Path:
    return config.data_dir / PIDFILE_NAME


def write_pidfile(config: Config) -> Path:
    """Record this process as the HTTP server (called from wingman-mcp --http)."""
    path = pidfile_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return path


def clear_pidfile(config: Config) -> None:
    pidfile_path(config).unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, but someone else's — identity check will reject it
    return True


def read_server_pid(config: Config, command_of: CommandOf = _ps_command_of) -> int | None:
    """The recorded server pid, verified; stale pidfiles are removed on sight."""
    path = pidfile_path(config)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if not _alive(pid) or "wingman-mcp" not in command_of(pid):
        _logger.info("stale pidfile removed pid=%d", pid)
        path.unlink(missing_ok=True)
        return None
    return pid


def orphan_http_pids(
    config: Config,
    port: int = DEFAULT_PORT,
    command_of: CommandOf = _ps_command_of,
    processes: ListProcesses = _ps_all,
    owner_of: OwnerOf = _ps_owner_of,
) -> list[int]:
    """HTTP servers on OUR port, owned by US, that the pidfile doesn't know about.

    These hold the port (every restart dies with EADDRINUSE) while status
    says "not running". The match is deliberately narrow: the command must
    name wingman-mcp AND --http AND resolve to this port — stdio servers
    and other instances' ports never qualify. It is also deliberately
    narrow on ownership (#138): 'ps -axo' lists every account's processes,
    and on a shared multi-user host (RFC-032, the lobster shape) another
    account's wingman-mcp can legitimately match the same command shape —
    e.g. a caller who forgot --port and fell back to DEFAULT_PORT, which
    is someone else's real port, not an orphan of theirs at all. A pid
    whose owner can't be determined is treated the same as a mismatch:
    excluded, never assumed to be ours.
    """
    managed = read_server_pid(config, command_of=command_of)
    my_uid = os.getuid()
    return [
        pid
        for pid, command in processes()
        if pid not in (managed, os.getpid(), os.getppid())
        and _is_wingman_http(command)
        and _command_port(command) == port
        and owner_of(pid) == my_uid
    ]


def server_status(
    config: Config,
    port: int = DEFAULT_PORT,
    command_of: CommandOf = _ps_command_of,
    processes: ListProcesses = _ps_all,
    owner_of: OwnerOf = _ps_owner_of,
) -> str:
    pid = read_server_pid(config, command_of=command_of)
    if pid is None:
        line = "The HTTP MCP server is not running. Start it with: wingman-mcp --http"
    else:
        line = f"The HTTP MCP server is running (pid {pid}). Stop it with: wingman mcp stop"
    orphans = orphan_http_pids(
        config, port=port, command_of=command_of, processes=processes, owner_of=owner_of
    )
    if orphans:
        pids = ", ".join(str(orphan) for orphan in orphans)
        line += (
            f"\nOrphaned HTTP server holding port {port} without a pidfile: pid {pids}. "
            "'wingman mcp stop' will stop it too."
        )
    return line


def _terminate(pid: int, grace_seconds: float) -> bool | None:
    """TERM, then KILL after the grace period.

    True when KILL was needed, False when TERM alone was enough (or the
    process was already gone). None means the OS refused the signal — a
    different account's process, discovered despite the ownership filter
    in orphan_http_pids (e.g. ownership couldn't be determined up front).
    Never raises: a permission failure is data for the caller to report,
    not a crash — this runs on a shared multi-account host (RFC-032/#138).
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return False
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    return True


def _describe_stop(pid: int, forced: bool | None, grace_seconds: float, label: str) -> str:
    """label is a description ending in a space, e.g. 'the HTTP MCP server '."""
    if forced is None:
        return f"Found {label}(pid {pid}) owned by a different account — left it running, not ours to touch."
    suffix = f", forced after {grace_seconds:g}s" if forced else ""
    return f"Stopped {label}(pid {pid}{suffix})."


def stop_server(
    config: Config,
    port: int = DEFAULT_PORT,
    command_of: CommandOf = _ps_command_of,
    processes: ListProcesses = _ps_all,
    owner_of: OwnerOf = _ps_owner_of,
    grace_seconds: float = 5.0,
) -> tuple[bool, str]:
    """TERM the recorded server (and any port-holding orphan), KILL after grace.

    Returns (stopped_something, detail). Signals only pids that verify as
    wingman-mcp AND ours to signal: the pidfile's entry, plus
    orphan_http_pids' narrow, ownership-filtered match (#138) — a
    same-port process on a shared host that belongs to a different
    account is reported, never touched, even if it slips past the
    ownership filter (e.g. an undeterminable owner at scan time).
    """
    pid = read_server_pid(config, command_of=command_of)
    orphans = orphan_http_pids(
        config, port=port, command_of=command_of, processes=processes, owner_of=owner_of
    )
    if pid is None and not orphans:
        return False, "The HTTP MCP server is not running (nothing to stop)."
    details: list[str] = []
    touched_something = False
    if pid is not None:
        forced = _terminate(pid, grace_seconds)
        if forced is not None:
            touched_something = True
            clear_pidfile(config)
        _logger.info("http server stop attempt pid=%d forced=%s", pid, forced)
        details.append(_describe_stop(pid, forced, grace_seconds, "the HTTP MCP server "))
    for orphan in orphans:
        forced = _terminate(orphan, grace_seconds)
        if forced is not None:
            touched_something = True
        _logger.info("orphan http server stop attempt pid=%d forced=%s", orphan, forced)
        details.append(
            _describe_stop(
                orphan, forced, grace_seconds, f"an orphaned HTTP server on port {port} "
            )
        )
    return touched_something, "\n".join(details)
