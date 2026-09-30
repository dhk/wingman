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
    from wingman.infrastructure.oauth_bearer import IdentityMap
    from wingman.infrastructure.tenants import TenantIndex

_logger = get_logger("infrastructure.tenant_process")


class TenantProcessSignalError(RuntimeError):
    """The shared process is running but could not be signaled (#329).

    Distinct from "no process found", which is an ordinary outcome this
    module reports by returning None — here the process is alive and the
    reload simply did not happen, which is a different thing for the
    operator to do something about.
    """


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


def read_tenant_process_pid(
    registry_path: Path, command_of: CommandOf = _ps_command_of
) -> int | None:
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
    try:
        os.kill(pid, signal.SIGHUP)
    except ProcessLookupError:
        # It exited between the pid read and the signal. That is genuinely
        # "no verified-live process", which this function's None branch and
        # the caller's message already describe correctly (#329).
        _logger.info("shared process pid=%s exited before it could be signaled", pid)
        return None
    except PermissionError:
        # Different in kind: the process IS there, this account may not
        # signal it. Flattening that into "not found" would send the
        # operator looking for a process that is running fine.
        raise TenantProcessSignalError(
            f"the shared process (pid {pid}) is running but this account may not signal it "
            "— rerun as its owner (or via the redeploy script, which runs as root). The new "
            "token is written; it just will not be recognized until that process reloads."
        ) from None
    return pid


def register_reload_handler(
    index: TenantIndex,
    registry_path: Path,
    identities: IdentityMap | None = None,
    identity_path: Path | None = None,
) -> None:
    """Reload the tenant registry and optional OAuth map as one validated update."""
    if (identities is None) != (identity_path is None):
        raise ValueError("identities and identity_path must be provided together")

    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        if identity_path is None:
            _logger.info("SIGHUP received — reloading tenant registry from %s", registry_path)
        else:
            _logger.info(
                "SIGHUP received — reloading tenant registry from %s and OAuth identity map "
                "from %s",
                registry_path,
                identity_path,
            )
        try:
            fresh_index = type(index).from_registry_path(registry_path)
            fresh_identities = None
            if identities is not None and identity_path is not None:
                from wingman.infrastructure.oauth_bearer import IdentityMap

                fresh_identities = IdentityMap.from_toml(identity_path)
                unknown = sorted(
                    slug for slug in fresh_identities.slugs() if fresh_index.by_slug(slug) is None
                )
                if unknown:
                    raise ValueError(
                        "OAuth identity map names tenant(s) missing from the registry: "
                        + ", ".join(unknown)
                    )

            # Parse and cross-validate both files before changing either live
            # object. The signal handler is synchronous, so request handling
            # cannot observe the assignments halfway through.
            index.replace_with(fresh_index)
            if identities is not None and fresh_identities is not None:
                identities.replace_with(fresh_identities)
        except Exception:  # noqa: BLE001 — see below: this must never propagate
            # An exception raised in a signal handler propagates into whatever
            # the main thread was executing, and this process serves every
            # tenant. A malformed registry — one typo'd key while adding
            # somebody — would therefore take the whole host down on the next
            # ordinary 'tenant rotate-token', which is what sends the SIGHUP
            # (#328).
            #
            if identity_path is None:
                _logger.exception(
                    "SIGHUP reload failed; retaining the previous tenant registry "
                    "(%d tenant(s)). Fix %s and signal again.",
                    len(index),
                    registry_path,
                )
            else:
                _logger.exception(
                    "SIGHUP reload failed; retaining the previous tenant registry "
                    "(%d tenant(s)) and OAuth identity map (%d mapping(s)). Fix %s and %s "
                    "and signal again.",
                    len(index),
                    len(identities) if identities is not None else 0,
                    registry_path,
                    identity_path,
                )

    signal.signal(signal.SIGHUP, _handler)
