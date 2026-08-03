"""The shared multi-tenant process's own pidfile, and a SIGHUP-triggered
reload of its in-memory TenantIndex (docs/RFC.md RFC-048, #210).

Mirrors infrastructure.mcp_process's pidfile rigor — verify the recorded
pid is alive AND actually a wingman-mcp process before trusting it — but
keyed to the tenant registry path instead of a single Config.data_dir:
the shared process has no one tenant's workspace to keep its pidfile in.

Rotation is one step, not two (#210's resolved design): the operator
writes a fresh token to a tenant's own 'mcp-http-token' file (unchanged
mechanism, mcp_server._http_token) and signals SIGHUP here. The running
process reloads the registry and every tenant's token file, so the old
token stops matching and the new one starts working — no restart (which
would drop every OTHER tenant's session too, RFC-041's finding), and no
draining, since the index is only ever consulted once per request, at
the start (infrastructure.tenant_asgi.TenantRoutingASGIApp).
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from wingman.infrastructure.logs import get_logger

if TYPE_CHECKING:
    from wingman.infrastructure.tenants import TenantIndex

_logger = get_logger("infrastructure.tenant_process")

PIDFILE_SUFFIX = ".pid"

CommandOf = Callable[[int], str]


def _ps_command_of(pid: int) -> str:
    try:
        result = subprocess.run(  # noqa: S603 — fixed binary, no shell
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, but someone else's — identity check will reject it
    return True


def tenant_pidfile_path(registry_path: Path) -> Path:
    """Under the system temp dir, NOT alongside the registry file itself.

    Hit live on the first real deployment: the registry conventionally
    lives under a root-owned, non-group-writable directory (RFC-047's
    '/etc/wingman/', mode 750 — deliberately not group-writable, since it
    also holds 'global-secrets.env') that the account actually running
    the shared process usually cannot write into at all, even as a
    member of the 'wingman' group (which only grants read+traverse).
    Keyed by a hash of the registry's own resolved absolute path — not
    the running account's home directory — so this stays a pure function
    of 'registry_path' alone, giving the same answer everywhere,
    regardless of which account ends up running the process or how the
    path was spelled (relative, symlinked, etc.) at each call site.
    """
    digest = hashlib.sha256(str(registry_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"wingman-tenants-{digest}{PIDFILE_SUFFIX}"


def write_tenant_pidfile(registry_path: Path) -> Path:
    """Record this process as the shared multi-tenant server (called from
    'wingman-mcp --http --tenant-registry ...')."""
    path = tenant_pidfile_path(registry_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return path


def clear_tenant_pidfile(registry_path: Path) -> None:
    tenant_pidfile_path(registry_path).unlink(missing_ok=True)


def read_tenant_process_pid(registry_path: Path, command_of: CommandOf = _ps_command_of) -> int | None:
    """The recorded shared-process pid, verified; a stale pidfile is
    removed on sight — mirrors 'mcp_process.read_server_pid's rigor."""
    path = tenant_pidfile_path(registry_path)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if not _alive(pid) or "wingman-mcp" not in command_of(pid):
        _logger.info("stale tenant pidfile removed pid=%d", pid)
        path.unlink(missing_ok=True)
        return None
    return pid


def signal_reload(registry_path: Path, command_of: CommandOf = _ps_command_of) -> int | None:
    """Send SIGHUP to the running shared process so it reloads its
    TenantIndex from disk. Returns the signaled pid, or None if no
    verified-live shared process was found (the new token is still
    written either way — it just won't be recognized until the process
    starts or is otherwise reloaded)."""
    pid = read_tenant_process_pid(registry_path, command_of=command_of)
    if pid is None:
        return None
    os.kill(pid, signal.SIGHUP)
    return pid


def register_reload_handler(index: "TenantIndex", registry_path: Path) -> None:
    """Install a SIGHUP handler on the CURRENT process that reloads
    'index' from 'registry_path'. Call once, from the shared-process
    startup path, before serving. Safe to run directly inside a signal
    handler: 'TenantIndex.reload' is synchronous, file-read-and-dict-swap
    work — no async marshaling needed, and the swap itself is a single
    attribute reassignment, atomic from any concurrent request's view.
    """

    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        _logger.info("SIGHUP received — reloading tenant registry from %s", registry_path)
        index.reload(registry_path)

    signal.signal(signal.SIGHUP, _handler)
