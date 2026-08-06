"""'wg redeploy-shared' and the post-pull re-exec guard (#292).

wingman-ctl is driven as a subprocess against stub 'git' and 'sudo'
executables. The script under test is a *copy*, so the re-exec case can
simulate a pull that rewrites wingman-ctl without touching the real one.
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
    """A fake checkout, a copy of wingman-ctl, and stub git/sudo/wingman."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    ctl = repo / "scripts" / "wingman-ctl"
    shutil.copy2(CTL, ctl)

    redeploy = repo / "scripts" / "wingman-redeploy-shared.sh"
    _exe(redeploy, f"#!/usr/bin/env bash\necho redeployed >> {tmp_path / 'calls.log'}\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    # 'git pull' records itself; anything else is a harmless no-op.
    _exe(
        bin_dir / "git",
        f'#!/usr/bin/env bash\n[ "$1" = "pull" ] && echo "git pull" >> {log}\nexit 0\n',
    )
    _exe(bin_dir / "sudo", f'#!/usr/bin/env bash\necho "sudo $*" >> {log}\nexec "$@"\n')
    # wingman-ctl calls this at startup paths we do not exercise here.
    _exe(bin_dir / "wingman", "#!/usr/bin/env bash\nexit 0\n")

    return {"repo": repo, "ctl": ctl, "bin": bin_dir, "log": log, "redeploy": redeploy}


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


def test_redeploy_shared_pulls_then_runs_the_script_under_sudo(rig: dict[str, Path]) -> None:
    result = _run(rig, "redeploy-shared")

    assert result.returncode == 0, result.stderr
    calls = _calls(rig)
    assert calls[0] == "git pull"
    # The pull must come first: the script it then invokes is the one the pull
    # may have just updated.
    assert any(call.startswith("sudo ") and "wingman-redeploy-shared.sh" in call for call in calls)
    assert "redeployed" in calls


def test_redeploy_shared_warns_that_tenants_are_interrupted(rig: dict[str, Path]) -> None:
    result = _run(rig, "redeploy-shared")

    assert "briefly interrupted" in result.stdout


def test_redeploy_shared_stops_when_the_pull_fails(rig: dict[str, Path]) -> None:
    _exe(rig["bin"] / "git", '#!/usr/bin/env bash\n[ "$1" = "pull" ] && exit 1\nexit 0\n')

    result = _run(rig, "redeploy-shared")

    assert result.returncode != 0
    assert "nothing was touched" in result.stdout
    assert "redeployed" not in _calls(rig)


def test_redeploy_shared_reports_a_checkout_without_the_script(rig: dict[str, Path]) -> None:
    rig["redeploy"].unlink()

    result = _run(rig, "redeploy-shared")

    assert result.returncode != 0
    assert "missing or not executable" in result.stdout
    assert not any(call.startswith("sudo ") for call in _calls(rig))


def test_a_pull_that_updates_wingman_ctl_restarts_the_run(rig: dict[str, Path]) -> None:
    # The pull rewrites wingman-ctl, exactly as a real one would when the
    # script itself changed upstream.
    _exe(
        rig["bin"] / "git",
        f"""#!/usr/bin/env bash
if [ "$1" = "pull" ]; then
  echo "git pull" >> {rig["log"]}
  printf '\\n# changed by the pull\\n' >> {rig["ctl"]}
fi
exit 0
""",
    )

    result = _run(rig, "redeploy-shared")

    assert result.returncode == 0, result.stderr
    assert "restarting so the rest of this run uses it" in result.stdout
    # Restarting re-runs from the top, so the pull happens twice; the second
    # one leaves the file alone, so exactly one restart.
    assert _calls(rig).count("git pull") == 2
    assert result.stdout.count("restarting so the rest of this run uses it") == 1
    assert "redeployed" in _calls(rig)


def test_an_unchanged_pull_does_not_restart(rig: dict[str, Path]) -> None:
    result = _run(rig, "redeploy-shared")

    assert "restarting so the rest of this run uses it" not in result.stdout
    assert _calls(rig).count("git pull") == 1


def test_a_script_that_keeps_changing_gives_up_rather_than_looping(rig: dict[str, Path]) -> None:
    # Pathological: every pull rewrites the script. Without the guard this
    # re-execs forever.
    _exe(
        rig["bin"] / "git",
        f"""#!/usr/bin/env bash
if [ "$1" = "pull" ]; then
  echo "git pull" >> {rig["log"]}
  printf '\\n# changed again %s\\n' "$RANDOM$$" >> {rig["ctl"]}
fi
exit 0
""",
    )

    result = _run(rig, "redeploy-shared")

    assert result.returncode == 0, result.stderr
    assert "changed again after restarting once" in result.stdout
    assert _calls(rig).count("git pull") == 2
    assert "redeployed" in _calls(rig)


def test_redeploy_shared_is_listed_in_usage(rig: dict[str, Path]) -> None:
    result = _run(rig)

    assert result.returncode == 2
    assert "redeploy-shared" in result.stdout


def test_upgrade_also_restarts_when_the_pull_updates_wingman_ctl(rig: dict[str, Path]) -> None:
    """The guard's original motivation: 'upgrade' had this hazard silently."""
    _exe(rig["bin"] / "uv", "#!/usr/bin/env bash\nexit 0\n")
    _exe(rig["bin"] / "pgrep", "#!/usr/bin/env bash\nexit 1\n")
    _exe(
        rig["bin"] / "git",
        f"""#!/usr/bin/env bash
if [ "$1" = "pull" ]; then
  echo "git pull" >> {rig["log"]}
  printf '\\n# changed by the pull\\n' >> {rig["ctl"]}
fi
exit 0
""",
    )

    result = _run(rig, "upgrade")

    assert "restarting so the rest of this run uses it" in result.stdout
    assert result.stdout.count("restarting so the rest of this run uses it") == 1
    assert _calls(rig).count("git pull") == 2
