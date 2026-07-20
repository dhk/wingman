"""HTTP MCP server process management: pidfile, status, stop (RFC-032).

'wingman mcp stop' manages exactly one process — the HTTP-transport server
the user started themselves. Stdio servers spawned by Claude clients are
those clients' children and are never touched: killing them would break
live sessions wingman doesn't own. The pidfile makes stop deterministic
(no pattern-matching over process tables), and every read verifies the
recorded pid is alive AND still a wingman-mcp before trusting it, so a
stale file from a crash or reboot can never kill an innocent process
that reused the pid.
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

# Injectable for tests: pid -> that process's command line ('' when gone).
CommandOf = Callable[[int], str]


def _ps_command_of(pid: int) -> str:
    try:
        result = subprocess.run(  # noqa: S603 — fixed binary, no shell
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


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


def server_status(config: Config, command_of: CommandOf = _ps_command_of) -> str:
    pid = read_server_pid(config, command_of=command_of)
    if pid is None:
        return "The HTTP MCP server is not running. Start it with: wingman-mcp --http"
    return f"The HTTP MCP server is running (pid {pid}). Stop it with: wingman mcp stop"


def stop_server(
    config: Config,
    command_of: CommandOf = _ps_command_of,
    grace_seconds: float = 5.0,
) -> tuple[bool, str]:
    """TERM the recorded server, escalate to KILL after the grace period.

    Returns (stopped_something, detail). Only ever signals a pid the
    pidfile names AND that verifies as a live wingman-mcp.
    """
    pid = read_server_pid(config, command_of=command_of)
    if pid is None:
        return False, "The HTTP MCP server is not running (nothing to stop)."
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            clear_pidfile(config)
            _logger.info("http server stopped pid=%d", pid)
            return True, f"Stopped the HTTP MCP server (pid {pid})."
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)
    clear_pidfile(config)
    _logger.info("http server force-killed pid=%d", pid)
    return True, f"Stopped the HTTP MCP server (pid {pid}, forced after {grace_seconds:g}s)."
