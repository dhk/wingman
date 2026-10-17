"""'wg upgrade-all' upgrades EVERYTHING on the box (#375, #617).

Two things on this machine run wingman code and neither upgrades the other:
the operator's own CLI install (what an operator types — 'motd', 'qotd',
'keys') and the RFC-048 shared multi-tenant process (what every tenant talks
to). The cross-account systemd sweep that kept per-account (shape B) installs
current is gone with shape B (#617), so upgrade-all is now this account's CLI
refresh followed by 'redeploy-shared'.

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
    _exe(
        repo / "scripts" / "wingman-tool-install.sh",
        f'#!/usr/bin/env bash\necho "wingman-tool-install.sh $*" >> {log}\nexit 0\n',
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(
        bin_dir / "git",
        f'#!/usr/bin/env bash\n[ "$1" = "pull" ] && echo "git pull" >> {log}\nexit 0\n',
    )
    _exe(bin_dir / "sudo", f'#!/usr/bin/env bash\necho "sudo $*" >> {log}\nexec "$@"\n')
    _exe(bin_dir / "wingman", "#!/usr/bin/env bash\nexit 0\n")
    _exe(
        bin_dir / "systemctl",
        f'#!/usr/bin/env bash\necho "systemctl $*" >> {log}\nexit 0\n',
    )
    _exe(bin_dir / "journalctl", "#!/usr/bin/env bash\nexit 0\n")
    _exe(bin_dir / "uv", f'#!/usr/bin/env bash\necho "uv $*" >> {log}\nexit 0\n')
    # No stdio servers running: 'upgrade' stops them, and must find none here.
    _exe(bin_dir / "pgrep", "#!/usr/bin/env bash\nexit 1\n")

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


def _install_index(calls: list[str]) -> int:
    return next(i for i, c in enumerate(calls) if c.startswith("wingman-tool-install.sh"))


def test_upgrade_all_upgrades_this_cli_and_the_shared_process(rig: dict[str, Path]) -> None:
    """The whole point of the name. Before #375 the second half never ran,
    so the shared process could ship a command hours before the operator's
    own CLI could run it — and all you saw was 'No such command'."""
    result = _run(rig, "upgrade-all")

    assert result.returncode == 0, result.stdout
    calls = _calls(rig)
    assert any(c.startswith("wingman-tool-install.sh") for c in calls)
    assert "redeployed" in calls


def test_the_retired_cross_account_sweep_is_never_triggered(rig: dict[str, Path]) -> None:
    """#617: wingman-upgrade-all.service kept shape-B accounts current. Shape
    B is retired, so upgrade-all must not start (or even look for) it, and
    the CLI refresh runs as the caller, not through root's install."""
    _run(rig, "upgrade-all")

    calls = _calls(rig)
    assert not any("wingman-upgrade-all" in c for c in calls)
    assert not any(c.startswith("systemctl") for c in calls)
    assert not any(c.startswith("sudo") and "wingman-tool-install.sh" in c for c in calls)


def test_the_cli_is_refreshed_before_the_process_that_serves_tenants(
    rig: dict[str, Path],
) -> None:
    """Deliberate order: the CLI refresh is cheap and interrupts nobody, so
    it goes first and proves the tree builds. The shared restart briefly
    cuts every tenant off, so it goes last."""
    _run(rig, "upgrade-all")

    calls = _calls(rig)
    assert _install_index(calls) < calls.index("redeployed")


def test_a_failed_cli_refresh_still_redeploys_the_shared_process(rig: dict[str, Path]) -> None:
    """The two halves are independent. Refusing to update the process that
    serves every tenant because the operator's CLI could not be reinstalled
    is the wrong trade — but the run is still reported as incomplete."""
    _exe(rig["repo"] / "scripts" / "wingman-tool-install.sh", "#!/usr/bin/env bash\nexit 1\n")

    result = _run(rig, "upgrade-all")

    assert "redeployed" in _calls(rig)
    assert "this account's CLI upgrade FAILED" in result.stdout
    assert "INCOMPLETE" in result.stdout
    assert "stale, not broken" in result.stdout
    assert result.returncode != 0


def test_a_failed_shared_redeploy_is_reported_as_a_possible_outage(rig: dict[str, Path]) -> None:
    """This assertion used to demand the OPPOSITE, and it was wrong (#415).

    It required the wording "anything already running was left alone",
    which is true of a failed CLI install and false of a failed shared
    redeploy: by then the old process may already have been stopped, so
    every tenant is DOWN rather than stale. That sentence is the one an
    operator acts on at 3am, and it sent them back to bed.
    """
    _exe(rig["redeploy"], "#!/usr/bin/env bash\nexit 1\n")

    result = _run(rig, "upgrade-all")

    assert "did not come up cleanly" in result.stdout
    assert "NO TENANT IS SERVED" in result.stdout
    assert "left alone" not in result.stdout
    assert result.returncode != 0


def test_a_registry_only_failure_is_never_reported_as_an_outage(rig: dict[str, Path]) -> None:
    """2026-10-09: the shared process was healthy and serving, only the host
    registry declaration failed, and upgrade-all announced NO TENANT IS SERVED.
    The redeploy script now exits 3 for exactly that outcome."""
    _exe(rig["redeploy"], "#!/usr/bin/env bash\nexit 3\n")

    result = _run(rig, "upgrade-all")

    assert "NO TENANT IS SERVED" not in result.stdout
    assert "did not come up cleanly" not in result.stdout
    assert "registry entry was not updated" in result.stdout
    assert "Nothing is down" in result.stdout
    assert result.returncode == 3


def test_a_clean_run_says_both_halves_are_current(rig: dict[str, Path]) -> None:
    result = _run(rig, "upgrade-all")

    assert "this account's CLI and the shared process are both current" in result.stdout
    assert "INCOMPLETE" not in result.stdout


def test_the_usage_text_describes_both_halves(rig: dict[str, Path]) -> None:
    result = _run(rig, "not-a-command")

    assert "EVERYTHING on this box" in result.stdout
    assert "shared multi-tenant process" in result.stdout


def test_plain_upgrade_never_touches_the_shared_service(rig: dict[str, Path]) -> None:
    """'upgrade' is this account's CLI only (#292): it must not redeploy the
    process every tenant talks to, and it says where that lives instead."""
    result = _run(rig, "upgrade")

    assert result.returncode == 0, result.stdout
    assert "redeployed" not in _calls(rig)
    assert "redeploy-shared" in result.stdout
