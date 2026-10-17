"""Per-account (shape B) process commands are retired (#617).

Every tenant is served by the one shared multi-tenant process, so 'wg start',
'wg stop' and 'wg cycle' — which managed a per-account HTTP server — must say
where to go instead of starting, stopping or upgrading something that serves
nobody. Driven as a subprocess against stub binaries, like
test_wingman_ctl_upgrade_all.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CTL = Path(__file__).resolve().parents[2] / "scripts" / "wingman-ctl"


def _exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def rig(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    ctl = repo / "scripts" / "wingman-ctl"
    shutil.copy2(CTL, ctl)
    log = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Anything the retired commands used to run is recorded, so a test can
    # prove nothing was started, stopped or pulled.
    for name in ("wingman", "wingman-mcp", "git", "sudo", "nohup", "pgrep", "kill"):
        _exe(bin_dir / name, f'#!/usr/bin/env bash\necho "{name} $*" >> {log}\nexit 0\n')
    return {"repo": repo, "ctl": ctl, "bin": bin_dir, "log": log}


def _run(rig: dict[str, Path], *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{rig['bin']}:{env['PATH']}"
    env["WINGMAN_REPO"] = str(rig["repo"])
    env.pop("WINGMAN_CTL_REEXECED", None)
    return subprocess.run(
        ["bash", str(rig["ctl"]), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.mark.parametrize("command", ["start", "stop", "cycle"])
def test_retired_commands_point_at_the_shared_service_and_do_nothing(
    rig: dict[str, Path], command: str
) -> None:
    result = _run(rig, command)

    assert result.returncode == 2
    assert "per-account servers are retired" in result.stdout
    assert "redeploy-shared" in result.stdout
    calls = rig["log"].read_text(encoding="utf-8") if rig["log"].exists() else ""
    assert "wingman-mcp" not in calls
    assert "mcp stop" not in calls
    assert "git pull" not in calls


def test_the_usage_text_no_longer_offers_the_retired_commands(rig: dict[str, Path]) -> None:
    result = _run(rig, "not-a-command")

    first = result.stdout.splitlines()[0]
    assert "|start|" not in first
    assert "|cycle|" not in first
    assert "retired" in result.stdout
