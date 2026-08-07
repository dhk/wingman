"""scripts/wingman-tool-install.sh — installs pinned to the lockfile (#302).

Driven as a subprocess against a stub ``uv`` that records its argv, because
what matters is the command that ends up being run: an unconstrained
``uv tool install`` deploys versions nothing tested, and nothing reports it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wingman-tool-install.sh"

EXPORTED = "mcp==1.28.1\npydantic-settings==2.14.2\n"


def _stub_uv(bin_dir: Path, log: Path, *, export_rc: int = 0, export_out: str = EXPORTED) -> None:
    stub = bin_dir / "uv"
    stub.write_text(
        f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

argv = sys.argv[1:]
with Path({str(log)!r}).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\\n")
if argv and argv[0] == "export":
    sys.stdout.write({export_out!r})
    raise SystemExit({export_rc})
raise SystemExit(0)
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)


@pytest.fixture
def rig(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "wingman"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return {"repo": repo, "bin": bin_dir, "log": tmp_path / "uv.log"}


def _run(rig: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{rig['bin']}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPT), str(rig["repo"])],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _calls(rig: dict[str, Path]) -> list[list[str]]:
    log = rig["log"]
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_a_checkout_with_a_lockfile_installs_pinned_to_it(rig: dict[str, Path]) -> None:
    (rig["repo"] / "uv.lock").write_text("# lock\n", encoding="utf-8")
    _stub_uv(rig["bin"], rig["log"])

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    install = next(call for call in _calls(rig) if call[:2] == ["tool", "install"])
    assert "--constraints" in install
    constraints = Path(install[install.index("--constraints") + 1])
    # The file is a temporary that the script cleans up, so its content is
    # captured by the export call rather than read back here.
    assert constraints.name
    export = next(call for call in _calls(rig) if call[0] == "export")
    assert "--frozen" in export


def test_the_constraints_file_does_not_outlive_the_install(rig: dict[str, Path]) -> None:
    (rig["repo"] / "uv.lock").write_text("# lock\n", encoding="utf-8")
    _stub_uv(rig["bin"], rig["log"])

    assert _run(rig).returncode == 0

    install = next(call for call in _calls(rig) if call[:2] == ["tool", "install"])
    leftover = Path(install[install.index("--constraints") + 1])
    assert not leftover.exists()


def test_a_checkout_without_a_lockfile_still_installs_and_says_so(rig: dict[str, Path]) -> None:
    _stub_uv(rig["bin"], rig["log"])

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    install = next(call for call in _calls(rig) if call[:2] == ["tool", "install"])
    assert "--constraints" not in install
    assert "no uv.lock" in result.stdout
    # Silence here would be the defect: an unpinned install that looks pinned.
    assert "may differ from tested ones" in result.stdout


def test_a_failed_export_falls_back_rather_than_blocking(rig: dict[str, Path]) -> None:
    (rig["repo"] / "uv.lock").write_text("# lock\n", encoding="utf-8")
    _stub_uv(rig["bin"], rig["log"], export_rc=1, export_out="")

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    install = next(call for call in _calls(rig) if call[:2] == ["tool", "install"])
    assert "--constraints" not in install
    assert "could not be exported" in result.stdout


def test_an_empty_export_is_treated_as_a_failure(rig: dict[str, Path]) -> None:
    (rig["repo"] / "uv.lock").write_text("# lock\n", encoding="utf-8")
    _stub_uv(rig["bin"], rig["log"], export_rc=0, export_out="")

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    install = next(call for call in _calls(rig) if call[:2] == ["tool", "install"])
    assert "--constraints" not in install


def test_every_install_path_in_the_repo_goes_through_this_script() -> None:
    """A fix applied to two of three paths is the same defect, smaller."""
    scripts = SCRIPT.parent
    offenders = []
    for path in list(scripts.glob("*.sh")) + [scripts / "wingman-ctl"]:
        if path == SCRIPT or not path.is_file():
            continue
        code = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        if any("uv tool install" in line for line in code):
            offenders.append(path.name)

    assert offenders == [], f"unconstrained 'uv tool install' remains in: {offenders}"
