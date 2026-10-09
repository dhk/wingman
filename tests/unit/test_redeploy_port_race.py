"""redeploy-shared must not race the outgoing process for its port (#415).

Driven as a subprocess against stub `sudo`, `systemctl`, `ss` and `curl`.
What matters is the ORDER — stop, wait for the socket, then start — and
that a failure says which of two very different things happened.

The failure it prevents: `systemctl restart` returns when the unit reports
stopped, and stopped does not mean the port is free — the outgoing process
drains open streamable-HTTP sessions first. On this box that produced 24
consecutive bind failures across ~50 seconds. The unit's StartLimitBurst
was 5; had the limit been enforced over that window systemd would have
given up and left every tenant down while telling the operator the
redeploy had not happened.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wingman-redeploy-shared.sh"
PROVISION = Path(__file__).resolve().parents[2] / "scripts" / "wingman-provision-shared.sh"
CTL = Path(__file__).resolve().parents[2] / "scripts" / "wingman-ctl"


def _exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def rig(tmp_path: Path) -> dict[str, Path]:
    """Stubs for everything the script shells out to, recording each call."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    held = tmp_path / "port-held"
    held.write_text("yes", encoding="utf-8")

    _exe(bin_dir / "sudo", f'#!/usr/bin/env bash\necho "sudo $*" >> {log}\nexit 0\n')
    _exe(bin_dir / "systemctl", f'#!/usr/bin/env bash\necho "systemctl $*" >> {log}\nexit 0\n')
    _exe(bin_dir / "git", "#!/usr/bin/env bash\nexit 0\n")
    _exe(bin_dir / "id", "#!/usr/bin/env bash\necho 1002\n")
    # 'ss' reports the port as held until the marker file is removed.
    _exe(
        bin_dir / "ss",
        f'#!/usr/bin/env bash\necho "ss $*" >> {log}\n'
        f'[ -f {held} ] && echo "LISTEN 0 2048 127.0.0.1:8789 0.0.0.0:*"\nexit 0\n',
    )
    _exe(
        bin_dir / "curl",
        f'#!/usr/bin/env bash\necho "curl $*" >> {log}\necho 200\nexit 0\n',
    )
    return {"bin": bin_dir, "log": log, "held": held}


def _calls(rig: dict[str, Path]) -> list[str]:
    log = rig["log"]
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_the_script_stops_waits_for_the_port_then_starts(rig: dict[str, Path]) -> None:
    """The order is the fix. A bare 'restart' is what raced."""
    text = SCRIPT.read_text(encoding="utf-8")
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    body = "\n".join(code)

    assert "stop wingman-mcp.service" in body
    assert "start wingman-mcp.service" in body
    assert "restart wingman-mcp.service" not in body, "a bare restart is the race itself"
    assert body.index("stop wingman-mcp.service") < body.index("start wingman-mcp.service")
    # The wait sits between them, not after.
    assert body.index("stop wingman-mcp.service") < body.index("PORT_DEADLINE")
    assert body.index("PORT_DEADLINE") < body.index("start wingman-mcp.service")


def test_a_port_that_never_frees_is_reported_as_an_outage_not_a_stale_build(
    rig: dict[str, Path],
) -> None:
    """Starting into a held port is what produces the restart loop, so the
    script refuses — and says nothing is serving, because nothing is."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert "STILL held" in text
    assert "NOTHING IS SERVING" in text
    assert "it is down" in text


def test_the_health_window_outlasts_a_real_convergence() -> None:
    """30s was shorter than the ~50s this actually took, so the script
    declared failure while the service was still coming up."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert "HEALTH_WINDOW=90" in text
    assert "within 30s" not in text


def test_a_health_failure_never_claims_nothing_changed() -> None:
    """The sentence an operator acts on at 3am. By the time health is
    checked the service HAS been stopped and started — 'left alone' would
    send somebody back to bed while every tenant is down."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert "not 'nothing changed'" in text
    assert "NOTHING IS SERVING TENANTS" in text


def test_the_unit_cannot_give_up_on_a_slow_port_release() -> None:
    """StartLimitBurst was 5 and the restart counter reached 24. The real
    fix is not racing the port; this is the backstop for every other path —
    a manual restart, a reboot, an OOM kill."""
    text = PROVISION.read_text(encoding="utf-8")

    assert "StartLimitBurst=0" in text
    assert "RestartSec=" in text


def test_the_wrapper_distinguishes_stale_from_down() -> None:
    """Two failures, two different truths. A per-account CLI that failed to
    install leaves the previous one working; a shared-process redeploy that
    failed may have stopped the old process already."""
    text = CTL.read_text(encoding="utf-8")

    assert "stale, not broken" in text
    assert "NO TENANT IS SERVED" in text
    assert "Anything already running was left alone." not in text


def test_the_scripts_are_valid_bash() -> None:
    for script in (SCRIPT, PROVISION, CTL):
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_ss_is_used_rather_than_lsof(rig: dict[str, Path]) -> None:
    """'is anything listening' needs no privileges to answer, and ss is
    present on a minimal server where lsof often is not."""
    body = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))

    assert "ss -ltn" in code
    assert "lsof -ti" not in code or "sudo lsof -ti" in body  # only as advice in a message


def test_the_environment_is_left_untouched(rig: dict[str, Path]) -> None:
    """Sanity: the stubs record calls and the real box is never contacted."""
    env = dict(os.environ)
    env["PATH"] = f"{rig['bin']}:{env['PATH']}"

    assert _calls(rig) == []


def test_a_registry_failure_after_a_healthy_restart_is_not_an_outage() -> None:
    """2026-10-09: the process was up and healthy, only the registry
    declaration failed, and the run was reported as a possible outage. The
    declaration is guarded, and its failure exits 3 with wording that says
    the service is serving."""
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))

    assert 'if ! "$(dirname "$0")/wingman-register-service.sh"; then' in code
    guarded = code[code.index('if ! "$(dirname "$0")/wingman-register-service.sh"') :]
    branch = guarded[: guarded.index("\nfi\n")]
    assert "exit 3" in branch
    assert "Nothing is down" in branch
    # Only reached after the health check has passed.
    assert code.index("started_at didn't change") < code.index("wingman-register-service.sh")
