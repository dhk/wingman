"""'wg upgrade-all' upgrades EVERYTHING on the box (#375).

There are two things on this machine that run wingman code and neither
upgrades the other: each account's own CLI install (what an operator types
— 'motd', 'qotd', 'tenant urls') and the RFC-048 shared multi-tenant
process (what every tenant talks to). This command used to trigger the
cross-account systemd unit and stop, and that unit's user list describes
shape B, which is retired — so it upgraded the accounts serving nobody and
skipped the one serving everybody.

Driven as a subprocess against stub git/sudo/systemctl, like
test_wingman_ctl_redeploy.py.
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
    redeploy = repo / "scripts" / "wingman-redeploy-shared.sh"
    _exe(redeploy, f"#!/usr/bin/env bash\necho redeployed >> {log}\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(
        bin_dir / "git",
        f'#!/usr/bin/env bash\n[ "$1" = "pull" ] && echo "git pull" >> {log}\nexit 0\n',
    )
    _exe(bin_dir / "sudo", f'#!/usr/bin/env bash\necho "sudo $*" >> {log}\nexec "$@"\n')
    _exe(bin_dir / "wingman", "#!/usr/bin/env bash\nexit 0\n")
    # Installed, and starting it succeeds.
    _exe(
        bin_dir / "systemctl",
        f'#!/usr/bin/env bash\necho "systemctl $*" >> {log}\nexit 0\n',
    )
    _exe(bin_dir / "journalctl", "#!/usr/bin/env bash\nexit 0\n")

    return {
        "repo": repo,
        "ctl": ctl,
        "bin": bin_dir,
        "log": log,
        "redeploy": redeploy,
    }


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


def _calls(rig: dict[str, Path]) -> list[str]:
    log = rig["log"]
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_upgrade_all_upgrades_the_accounts_and_the_shared_process(rig: dict[str, Path]) -> None:
    """The whole point of the name. Before #375 the second half never ran,
    so the shared process could ship a command hours before the operator's
    own CLI could run it — and all you saw was 'No such command'."""
    result = _run(rig, "upgrade-all")

    assert result.returncode == 0, result.stderr
    calls = _calls(rig)
    assert any("systemctl start wingman-upgrade-all.service" in call for call in calls)
    assert "redeployed" in calls


def test_the_accounts_are_upgraded_before_the_process_that_serves_tenants(
    rig: dict[str, Path],
) -> None:
    """Deliberate order: the per-account installs are cheap and interrupt
    nobody, so they go first and prove the tree builds. The shared restart
    briefly cuts every tenant off, so it goes last."""
    _run(rig, "upgrade-all")

    calls = _calls(rig)
    started = next(i for i, c in enumerate(calls) if "systemctl start" in c)
    redeployed = calls.index("redeployed")
    assert started < redeployed


def test_a_missing_systemd_unit_still_redeploys_the_shared_process(rig: dict[str, Path]) -> None:
    """The two halves are independent. Refusing to update the process that
    serves every tenant because a personal CLI could not be reinstalled is
    the wrong trade — but the run is still reported as incomplete."""
    _exe(
        rig["bin"] / "systemctl",
        '#!/usr/bin/env bash\n[ "$1" = "list-unit-files" ] && exit 1\nexit 0\n',
    )

    result = _run(rig, "upgrade-all")

    assert "redeployed" in _calls(rig)
    assert "INCOMPLETE" in result.stdout
    assert "per-account CLI upgrade did not run" in result.stdout
    assert result.returncode != 0


def test_a_failed_account_upgrade_is_reported_never_silent(rig: dict[str, Path]) -> None:
    _exe(
        rig["bin"] / "systemctl",
        f'#!/usr/bin/env bash\necho "systemctl $*" >> {rig["log"]}\n'
        '[ "$1" = "start" ] && exit 1\nexit 0\n',
    )

    result = _run(rig, "upgrade-all")

    assert "per-account upgrade FAILED" in result.stdout
    assert "INCOMPLETE" in result.stdout
    assert result.returncode != 0


def test_a_failed_shared_redeploy_says_so_and_leaves_it_running(rig: dict[str, Path]) -> None:
    """The redeploy script's own failures leave the running process alone,
    so a partial result here means stale, not broken — and the message has
    to say which."""
    _exe(rig["redeploy"], "#!/usr/bin/env bash\nexit 1\n")

    result = _run(rig, "upgrade-all")

    assert "shared multi-tenant process was NOT redeployed" in result.stdout
    assert "left alone" in result.stdout
    assert result.returncode != 0


def test_a_clean_run_says_both_halves_are_current(rig: dict[str, Path]) -> None:
    result = _run(rig, "upgrade-all")

    assert "per-account CLIs and the shared process are both current" in result.stdout
    assert "INCOMPLETE" not in result.stdout


def test_the_usage_text_no_longer_describes_only_the_cross_account_unit(
    rig: dict[str, Path],
) -> None:
    """The old help said 'trigger the cross-account scheduled upgrade',
    which is exactly the half-truth that made the gap invisible."""
    result = _run(rig, "not-a-command")

    assert "EVERYTHING on this box" in result.stdout
    assert "shared multi-tenant process" in result.stdout
