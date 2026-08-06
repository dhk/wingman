"""One table for every wingman instance on this box, and what's stale (#263).

The states worth catching only appear in the *combination* of facts, which
is why no existing surface catches them: 'wingman status' is per-workspace,
'wingman doctor' is per-instance, and the admin Installations page
(RFC-034) knows running/stopped but not managedness or build drift. An
instance can be listening, healthy, and on the very latest build and still
be wrong — that is exactly the shape lobster's own dhk instance was in for
five days:

    listening on 8787, /health 200, newest build ... and
    'wingman-mcp.service' failed since Jul 31, so every nightly
    'wingman-upgrade-all' logged "not active for this user — build
    updated, nothing to restart" and never restarted the live process.

The build moved nightly; the process didn't. So this tool's headline job is
not "is it up" — it is "is anything running outside the thing that is
supposed to restart it".

Its own entry point rather than a 'wingman' subcommand, for RFC-042's
reason: the 'wingman' CLI bootstraps exactly one workspace per invocation
via WINGMAN_DATA_DIR and has no notion of "every instance on this box".
'scripts/wingman-ctl hosts' is the thin wrapper, same shape as
'cmd_upgrade_all'.

Privilege: everything here works as an ordinary unprivileged user, because
/health needs no capability token (webui.ui_health is deliberately
unauthenticated). What an unprivileged user genuinely cannot see — which
Unix account owns another account's listening socket, and that account's
systemd state — prints '?' with a footer saying what root would add. A
diagnostic that guesses is worse than one that admits the gap.

Tokens are never read. The instance names come from installations.toml,
but only its 'name'/'port'/'host' fields are parsed — a tool whose whole
job is printing should never hold a capability token in memory.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from wingman.infrastructure.host_config import HostEnvironmentError, read_host_settings
from wingman.infrastructure.portcheck import find_port_owner

SYSTEMD_UNIT = "wingman-mcp.service"

# Where wingman instances live on a shape-B/RFC-048 box: dhk 8787, trent
# 8788, the shared multi-tenant process 8789, with room to grow. Scanned
# rather than configured so an instance nobody registered still shows up —
# an unregistered instance is precisely the kind this tool exists to find.
DEFAULT_PORT_RANGE = (8787, 8799)

_HEALTH_TIMEOUT_SECONDS = 1.0
_COMMAND_TIMEOUT_SECONDS = 5.0

# (argv) -> (returncode, stdout). Injectable so tests never shell out.
Runner = Callable[[list[str]], tuple[int, str]]
# (host, port) -> parsed /health payload, or None if nothing answered.
HealthFetcher = Callable[[str, int], dict[str, object] | None]


def _default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(  # fixed binaries, no shell, hard timeout
            argv, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return result.returncode, result.stdout


def _default_health(host: str, port: int) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(  # loopback only; fixed scheme
            f"http://{host}:{port}/health", timeout=_HEALTH_TIMEOUT_SECONDS
        ) as response:
            payload = json.load(response)
    except (OSError, urllib.error.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def is_wingman_health(payload: dict[str, object]) -> bool:
    """Tell a wingman /health from a neighbour's.

    wingman answers {"version", "started_at"} and names no service
    (webui.ui_health). Alexandria — which shares this box and this port
    neighbourhood — answers the same two fields plus "service". So a
    payload that names a service other than wingman is somebody else's,
    and one that names none is wingman's.
    """
    service = payload.get("service")
    if service is not None:
        return str(service) == "wingman"
    return "version" in payload and "started_at" in payload


@dataclass(frozen=True)
class InstanceRow:
    port: int
    name: str
    running: bool
    version: str | None
    started_at: str | None
    build: str
    service: str
    note: str


def read_installation_names(path: Path) -> dict[int, str]:
    """port -> name from installations.toml. Tokens are never touched.

    A malformed file is not worth failing a status command over — the
    ports still scan, they just print without friendly names.
    """
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    names: dict[int, str] = {}
    for entry in data.get("instance", []):
        if not isinstance(entry, dict) or "port" not in entry:
            continue
        try:
            names[int(entry["port"])] = str(entry.get("name", "")) or "?"
        except (TypeError, ValueError):
            continue
    return names


def commit_of(version: str) -> str | None:
    """The git sha out of a setuptools-scm version: 0.4.1.dev116+gc5fcdc275."""
    match = re.search(r"\+g([0-9a-f]{7,40})", version)
    return match.group(1) if match else None


def is_dirty_build(version: str) -> bool:
    """setuptools-scm's '.dYYYYMMDD' suffix: built from a dirty tree."""
    return re.search(r"\.d\d{8}$", version) is not None


def invoking_identity(env: dict[str, str] | None = None) -> tuple[str, Path]:
    """(name, home) of the human who ran this, not the euid it runs under.

    Under sudo the effective user is root, whose home holds no wingman
    workspace and no WINGMAN_REPO — so resolving config from the effective
    user made `sudo wingman-host-status` show strictly LESS than the
    unprivileged run: the registry vanished, every name with it, and build
    drift regressed to '?' (#265). Privilege should only ever add.
    """
    environment = os.environ if env is None else env
    name = environment.get("SUDO_USER", "").strip()
    if name:
        try:
            entry = pwd.getpwnam(name)
            return entry.pw_name, Path(entry.pw_dir)
        except KeyError:
            pass
    entry = pwd.getpwuid(os.geteuid())
    return entry.pw_name, Path(entry.pw_dir)


def repo_path(env: dict[str, str] | None = None, home: Path | None = None) -> Path | None:
    """WINGMAN_REPO from the process env, else the canonical host file.

    Same resolution order wingman-ctl uses (RFC-046), for the same reason:
    a tool invoked from cron or a fresh SSH session never exported it.
    """
    environment = os.environ if env is None else env
    raw = environment.get("WINGMAN_REPO", "").strip()
    if not raw:
        try:
            raw = read_host_settings(home).get("WINGMAN_REPO", "").strip()
        except HostEnvironmentError:
            return None
    if not raw:
        return None
    return Path(raw).expanduser()


def build_state(version: str | None, repo: Path | None, run: Runner = _default_runner) -> str:
    """'current', 'N behind', or an honest 'unknown'/'?' — never a guess."""
    if version is None:
        return "-"
    sha = commit_of(version)
    if sha is None or repo is None:
        return "?"
    code, out = run(["git", "-C", str(repo), "rev-list", "--count", f"{sha}..HEAD"])
    if code != 0:
        # The sha isn't in this checkout: a build from a different repo, or
        # from history this checkout has never fetched.
        return "unknown"
    count = out.strip()
    if not count.isdigit():
        return "?"
    behind = int(count)
    return "current" if behind == 0 else f"{behind} behind"


def unit_state(
    user: str | None,
    *,
    run: Runner = _default_runner,
    euid: int | None = None,
) -> str | None:
    """'active'/'failed'/'inactive' for that account's unit, or None if
    this process has no way to find out.

    Decided by **uid, not by name** (#265). Comparing names got this wrong
    twice over: a truncated name never matched, and under sudo the
    "that's me, ask directly" branch would fire for the invoking user's
    account while actually running as root — querying root's session bus
    and reporting the wrong account's state.

    'systemctl --user' driven from root needs XDG_RUNTIME_DIR pointed at
    the target account's own runtime directory or it cannot reach that
    account's session bus at all — the same gotcha upgrade_all.py carries
    on every one of its sudo calls (RFC-042).
    """
    if user is None:
        return None
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        return None
    effective = os.geteuid() if euid is None else euid
    if effective == uid:  # we are that account: its bus is already ours
        _, out = run(["systemctl", "--user", "is-active", SYSTEMD_UNIT])
        return out.strip() or None
    if effective != 0:  # someone else's account, and we are not root
        return None
    _, out = run(
        [
            "sudo",
            "-u",
            user,
            "env",
            f"XDG_RUNTIME_DIR=/run/user/{uid}",
            "systemctl",
            "--user",
            "is-active",
            SYSTEMD_UNIT,
        ]
    )
    return out.strip() or None


def service_column(running: bool, state: str | None) -> tuple[str, str]:
    """(service, note). The one row that must never read as fine.

    Listening with no active unit is the failure this whole tool is for:
    nothing will restart it, and 'wingman-upgrade-all' says so in its log
    every night while everyone reads the '[ok]' at the front of the line.
    """
    if running and state is None:
        return "?", ""
    if running and state != "active":
        return (
            "UNMANAGED",
            f"unit is '{state}' — upgrade-all will not restart this instance",
        )
    if running:
        return "active", ""
    if state is None:
        return "-", ""
    return state, ""


def _relative(started_at: str | None, now: datetime) -> str:
    if not started_at:
        return ""
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return ""
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    seconds = int((now - started).total_seconds())
    if seconds < 0:
        return ""
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _started_column(row: InstanceRow, now: datetime) -> str:
    if not row.started_at:
        return "-"
    clock = row.started_at[11:16] if len(row.started_at) >= 16 else row.started_at
    relative = _relative(row.started_at, now)
    return f"{clock} ({relative})" if relative else clock


def collect(
    ports: Sequence[int],
    names: dict[int, str],
    *,
    repo: Path | None,
    health: HealthFetcher = _default_health,
    run: Runner = _default_runner,
    host: str = "127.0.0.1",
    euid: int | None = None,
) -> list[InstanceRow]:
    """One row per port that is a wingman instance or a named installation.

    A port that answers nothing but is named in installations.toml is kept
    and shown stopped — a configured instance that vanished is a finding,
    not an absence.
    """
    rows: list[InstanceRow] = []
    for port in ports:
        payload = health(host, port)
        if payload is not None and not is_wingman_health(payload):
            continue  # somebody else's service on a neighbouring port
        if payload is None and port not in names:
            continue
        running = payload is not None
        version = str(payload.get("version", "")) or None if payload else None
        started_at = str(payload.get("started_at", "")) or None if payload else None
        owner = find_port_owner(port, run) if running else None
        user = owner.user if owner else None
        state = unit_state(user, run=run, euid=euid)
        service, note = service_column(running, state)
        rows.append(
            InstanceRow(
                port=port,
                name=names.get(port) or user or "?",
                running=running,
                version=version,
                started_at=started_at,
                build=build_state(version, repo, run),
                service=service,
                note=note,
            )
        )
    return rows


def render(
    rows: Sequence[InstanceRow], *, now: datetime, repo: Path | None, euid: int | None = None
) -> str:
    if not rows:
        return "No wingman instances found on this box."
    header = ("PORT", "NAME", "VERSION", "STARTED", "BUILD", "SERVICE")
    body = [
        (
            str(row.port),
            row.name,
            row.version or "stopped",
            _started_column(row, now),
            row.build,
            row.service,
        )
        for row in rows
    ]
    widths = [max(len(cell) for cell in column) for column in zip(header, *body, strict=True)]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(header, widths, strict=True)).rstrip()
    ]
    for cells in body:
        lines.append(
            "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True)).rstrip()
        )
    notes = [f"  {row.port}: {row.note}" for row in rows if row.note]
    if notes:
        lines.append("")
        lines.append("Needs attention:")
        lines.extend(notes)
    footers = []
    # Name the command, don't gesture at it. The obvious reading of "rerun as
    # root" was 'sudo wg hosts', which can never work — 'wg' is a shell alias
    # sudo does not inherit (#265, and docs/SERVER.md §9's same trap). And when
    # already root there is nothing left to suggest.
    if any(row.service == "?" for row in rows) and (os.geteuid() if euid is None else euid) != 0:
        footers.append(
            "Service state is unknown for accounts other than yours — rerun as:\n"
            f"  sudo {Path(sys.argv[0]).resolve()}"
        )
    if any(row.version and is_dirty_build(row.version) for row in rows):
        footers.append("A version ending '.dYYYYMMDD' was built from a dirty working tree.")
    if repo is None and any(row.build == "?" for row in rows):
        footers.append(
            "Build drift needs WINGMAN_REPO (process env or ~/.config/wingman/wingman.env)."
        )
    if footers:
        lines.append("")
        lines.extend(footers)
    return "\n".join(lines)


def _parse_ports(raw: str) -> list[int]:
    if "-" in raw:
        first, _, last = raw.partition("-")
        return list(range(int(first), int(last) + 1))
    return [int(part) for part in raw.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wingman-host-status",
        description=(
            "Every wingman instance on this box: version, uptime, build drift, "
            "and whether anything is running outside systemd's control."
        ),
    )
    parser.add_argument(
        "--ports",
        default=f"{DEFAULT_PORT_RANGE[0]}-{DEFAULT_PORT_RANGE[1]}",
        help="port range ('8787-8799') or list ('8787,8789') to scan",
    )
    parser.add_argument("--host", default="127.0.0.1", help="loopback address to probe")
    args = parser.parse_args(argv)

    try:
        ports = _parse_ports(args.ports)
    except ValueError:
        print(f"ERROR: could not parse --ports {args.ports!r}", file=sys.stderr)
        return 2

    # Everything user-scoped is resolved from the INVOKING account, never the
    # effective one — see invoking_identity(). Reading it from the platform
    # default under that home rather than via load_config() also keeps a root
    # run from creating a stray workspace under /root as a side effect of
    # asking a read-only question.
    _, home = invoking_identity()
    names = read_installation_names(home / ".local" / "share" / "wingman" / "installations.toml")
    repo = repo_path(home=home)
    rows = collect(ports, names, repo=repo, host=args.host)
    print(render(rows, now=datetime.now(UTC), repo=repo))
    return 1 if any(row.note for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
