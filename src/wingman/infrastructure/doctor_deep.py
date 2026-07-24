"""'wingman doctor --deep': a guided, one-step-at-a-time diagnostic ladder
(#137, #138) — git-rebase-style. A live incident took a ~6-step manual
ladder, discovered one command at a time, to localize a fault that
'wingman mcp status' (RFC-032) missed entirely: it verifies a pidfile's pid
is alive and named right, never that it is actually bound to the port it
claims. A second incident added cross-account port squatting, an 'lsof'
hang, and systemd's crash-loop rate limit to the list of things the ladder
must specifically catch.

Design: each step is a pure function (config, port) -> StepResult, re-run
fresh on every invocation rather than remembered — these are live system
facts, not one-time checkboxes, so "did the operator fix it" is answered by
re-probing, not by asking. The CLI persists only a cursor (which step is
"current"); a step that passes advances the cursor for next time, one step
per invocation, matching the acceptance criteria in #137 ("print exactly
one step's result and one concrete next action — not the whole ladder
dumped at once"). '--continue' force-advances past a step whose live check
is only ever informational (e.g. no tunnel configured) or whose fix must
happen outside this process (e.g. reset-failed, then restart, then rerun).
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from pathlib import Path

import httpx

from wingman.infrastructure import mcp_process, portcheck, systemd_check
from wingman.infrastructure.config import Config

STATE_FILENAME = "doctor-deep-state.json"
SYSTEMD_UNIT = "wingman-mcp.service"
_HEALTH_TIMEOUT_SECONDS = 5.0
_LOG_TAIL_LINES = 15

STEP_NAMES = [
    "pidfile & port binding",
    "loopback health",
    "tunnel health",
    "process state & logs",
    "systemd restart state",
]


@dataclass(frozen=True)
class StepResult:
    step: int
    name: str
    ok: bool
    summary: str
    next_action: str | None = None


def _state_path(config: Config) -> Path:
    return config.data_dir / STATE_FILENAME


def read_cursor(config: Config) -> int:
    path = _state_path(config)
    if not path.exists():
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        step = int(data["step"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return 1
    return step if 1 <= step <= len(STEP_NAMES) else 1


def write_cursor(config: Config, step: int) -> None:
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"step": step}), encoding="utf-8")


def reset_cursor(config: Config) -> None:
    _state_path(config).unlink(missing_ok=True)


def default_log_path() -> Path:
    """Matches wingman-ctl's LOG default — same fallback the operator
    already knows, so the ladder points at the file they'd otherwise have
    to remember the platform-specific path for."""
    import os

    if override := os.environ.get("WINGMAN_LOG", "").strip():
        return Path(override)
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Logs" / "wingman-mcp.log"
    return Path.home() / ".local" / "state" / "wingman-mcp.log"


def _step_pidfile_and_port(config: Config, port: int) -> StepResult:
    managed_pid = mcp_process.read_server_pid(config)
    owner = portcheck.find_port_owner(port)

    if owner is None:
        if managed_pid is not None:
            return StepResult(
                1,
                STEP_NAMES[0],
                ok=False,
                summary=(
                    f"pidfile names pid {managed_pid} as the HTTP server, but nothing is "
                    f"listening on port {port} — the process is alive but not bound where "
                    "it claims."
                ),
                next_action=(
                    "check what that pid is actually doing (`ps -p "
                    f"{managed_pid} -o pid,stat,etime,command`), then `wingman mcp stop` "
                    "and restart it."
                ),
            )
        return StepResult(
            1,
            STEP_NAMES[0],
            ok=False,
            summary=f"No pidfile, and nothing is listening on port {port}.",
            next_action="start the server: `wingman-ctl start` (or `wingman-mcp --http`).",
        )

    if managed_pid is not None and owner.pid == managed_pid:
        return StepResult(
            1,
            STEP_NAMES[0],
            ok=True,
            summary=f"pid {managed_pid} is alive, named right, and bound to port {port}.",
        )

    # Something is listening, but it isn't the pid our own pidfile names —
    # either an unmanaged local orphan or, worse, a different account's
    # process squatting the port (#138's root cause).
    cross_account = not owner.readable_identity
    detail = owner.describe()
    if managed_pid is not None:
        summary = (
            f"pidfile names pid {managed_pid}, but port {port} is actually held by "
            f"{detail} — a stray or foreign process, not the one this workspace thinks "
            "is running."
        )
    else:
        summary = f"No pidfile, but port {port} is held by {detail}."
    if cross_account:
        summary += " Its command line is unreadable, which usually means a different Unix account."
    action = (
        f"if this process is yours, stop it (`kill {owner.pid}` or `wingman mcp stop` if it "
        "matches your workspace) before starting your own server on this port. If it belongs "
        "to a different account, do not kill it from here — coordinate with that account's "
        "owner; killing a process you don't own from the wrong account can silently take down "
        "someone else's live server."
    )
    return StepResult(1, STEP_NAMES[0], ok=False, summary=summary, next_action=action)


def _get_health(url: str) -> tuple[bool, str]:
    try:
        response = httpx.get(url, timeout=_HEALTH_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return False, str(exc)
    version = payload.get("version", "?")
    started_at = payload.get("started_at", "?")
    return True, f"v{version}, running since {started_at}"


def _step_loopback_health(config: Config, port: int, prefix: str = "") -> StepResult:
    url = f"http://127.0.0.1:{port}{prefix}/health"
    ok, detail = _get_health(url)
    if ok:
        return StepResult(2, STEP_NAMES[1], ok=True, summary=f"{url} answered: {detail}.")
    return StepResult(
        2,
        STEP_NAMES[1],
        ok=False,
        summary=f"{url} did not answer: {detail}.",
        next_action="the process itself is unhealthy or unreachable — the next step looks "
        "at its state and logs.",
    )


def _step_tunnel_health(config: Config, port: int, prefix: str = "") -> StepResult:
    from wingman.mcp_server import _extra_allowed_hosts, _tunnel_port

    hosts = _extra_allowed_hosts(None)
    if not hosts:
        return StepResult(
            3,
            STEP_NAMES[2],
            ok=True,
            summary="no tunnel hostname detected (Tailscale not up, or no funnel/serve "
            "configured) — nothing to check.",
        )
    tunnel_port = _tunnel_port(None)
    authority = hosts[0] if tunnel_port is None else f"{hosts[0]}:{tunnel_port}"
    url = f"https://{authority}{prefix}/health"
    ok, detail = _get_health(url)
    if ok:
        return StepResult(3, STEP_NAMES[2], ok=True, summary=f"{url} answered: {detail}.")
    return StepResult(
        3,
        STEP_NAMES[2],
        ok=False,
        summary=f"{url} did not answer: {detail}. The loopback check passed, so this "
        "isolates the fault to tunnel routing, not the process itself.",
        next_action="check the tunnel: `tailscale funnel status` / `tailscale serve status`, "
        "and that the funnel target port matches the server's --port.",
    )


def _tail_log(path: Path, lines: int = _LOG_TAIL_LINES) -> str:
    if not path.exists():
        return f"(no log file at {path})"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(could not read {path}: {exc})"
    return "\n".join(content[-lines:]) or "(log file is empty)"


def _step_process_and_logs(config: Config, port: int) -> StepResult:
    owner = portcheck.find_port_owner(port)
    managed_pid = mcp_process.read_server_pid(config)
    pid = owner.pid if owner is not None else managed_pid

    if pid is None:
        return StepResult(
            4,
            STEP_NAMES[3],
            ok=False,
            summary="No process is bound to the port and no pidfile is recorded — there is "
            "nothing running to inspect.",
            next_action="start the server: `wingman-ctl start`.",
        )

    log_path = default_log_path()
    log_tail = _tail_log(log_path)
    unit_active = systemd_check.unit_exists(SYSTEMD_UNIT)
    source = (
        f"journalctl --user -u {SYSTEMD_UNIT} -n {_LOG_TAIL_LINES} --no-pager"
        if unit_active
        else str(log_path)
    )
    summary = f"pid {pid}. Last {_LOG_TAIL_LINES} lines from {source}:\n{log_tail}"
    return StepResult(
        4,
        STEP_NAMES[3],
        ok=True,
        summary=summary,
        next_action=None
        if not unit_active
        else "if the log points at a crash loop, the "
        "next step checks whether systemd's own restart rate limit needs clearing.",
    )


def _step_systemd_rate_limit(config: Config, port: int) -> StepResult:
    if not systemd_check.systemd_available():
        return StepResult(
            5, STEP_NAMES[4], ok=True, summary="systemd is not present on this host — skipped."
        )
    if not systemd_check.unit_exists(SYSTEMD_UNIT):
        return StepResult(
            5,
            STEP_NAMES[4],
            ok=True,
            summary=f"no '{SYSTEMD_UNIT}' --user unit on this account — not systemd-managed, "
            "skipped.",
        )
    if systemd_check.is_rate_limited(SYSTEMD_UNIT):
        return StepResult(
            5,
            STEP_NAMES[4],
            ok=False,
            summary=f"'{SYSTEMD_UNIT}' is stuck behind systemd's restart rate limit — a plain "
            "restart will silently no-op.",
            next_action=f"clear it, then restart: `systemctl --user reset-failed {SYSTEMD_UNIT} "
            f"&& systemctl --user restart {SYSTEMD_UNIT}`.",
        )
    return StepResult(5, STEP_NAMES[4], ok=True, summary=f"'{SYSTEMD_UNIT}' is not rate-limited.")


_STEPS = [
    _step_pidfile_and_port,
    _step_loopback_health,
    _step_tunnel_health,
    _step_process_and_logs,
    _step_systemd_rate_limit,
]


def run_step(config: Config, step: int, port: int) -> StepResult:
    if not 1 <= step <= len(_STEPS):
        raise ValueError(f"no such step: {step}")
    return _STEPS[step - 1](config, port)


def total_steps() -> int:
    return len(_STEPS)
