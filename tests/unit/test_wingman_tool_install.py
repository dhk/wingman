"""scripts/wingman-tool-install.sh — installs pinned to the lockfile (#302, #319).

Driven as a subprocess against a stub ``uv`` that records its argv and a fake
tool venv reporting whichever versions a test wants. What matters is not that
``--constraints`` was passed but that the result actually matches the lock: on
one account here it was passed, changed nothing, and reported success.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wingman-tool-install.sh"

# What the stub `uv export` emits, and therefore what "the lock says".
EXPORTED = "mcp==1.28.1\npydantic-settings==2.14.2\n"
LOCKED = {"mcp": "1.28.1", "pydantic-settings": "2.14.2"}
DRIFTED = {"mcp": "1.29.0", "pydantic-settings": "2.15.0"}


def _fake_tool_venv(root: Path, versions: dict[str, str]) -> Path:
    """A stand-in `uv tool dir` whose wingman venv reports `versions`."""
    tool_dir = root / ("uvtools-" + "-".join(sorted(versions.values())))
    venv_bin = tool_dir / "wingman" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    python = venv_bin / "python3"
    python.write_text(
        f"#!/usr/bin/env python3\nimport json\nprint(json.dumps({versions!r}))\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    return tool_dir


def _stub_uv(
    bin_dir: Path,
    log: Path,
    *,
    tool_dir: Path,
    export_rc: int = 0,
    export_out: str = EXPORTED,
) -> None:
    stub = bin_dir / "uv"
    stub.write_text(
        f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

argv = sys.argv[1:]
with Path({str(log)!r}).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\\n")
if argv[:2] == ["tool", "dir"]:
    print({str(tool_dir)!r})
    raise SystemExit(0)
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
    return {
        "repo": repo,
        "bin": bin_dir,
        "log": tmp_path / "uv.log",
        "tmp": tmp_path,
        "honest": _fake_tool_venv(tmp_path, LOCKED),
    }


def _stub(rig: dict[str, Path], *, tool_dir: Path | None = None, **kwargs: object) -> None:
    _stub_uv(rig["bin"], rig["log"], tool_dir=tool_dir or rig["honest"], **kwargs)  # type: ignore[arg-type]


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


def _install_call(rig: dict[str, Path]) -> list[str]:
    return next(call for call in _calls(rig) if call[:2] == ["tool", "install"])


def _constraints_path(rig: dict[str, Path]) -> Path:
    call = _install_call(rig)
    return Path(call[call.index("--constraints") + 1])


def _lock(rig: dict[str, Path]) -> None:
    (rig["repo"] / "uv.lock").write_text("# lock\n", encoding="utf-8")


def test_a_checkout_with_a_lockfile_installs_pinned_to_it(rig: dict[str, Path]) -> None:
    _lock(rig)
    _stub(rig)

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    assert "--constraints" in _install_call(rig)
    assert "--frozen" in next(call for call in _calls(rig) if call[0] == "export")


def test_the_install_is_verified_against_the_lock_afterwards(rig: dict[str, Path]) -> None:
    """Passing --constraints is not the same as having been pinned (#319)."""
    _lock(rig)
    _stub(rig)

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    assert "no mismatches" in result.stdout


def test_an_install_that_did_not_honour_the_lock_fails_loudly(
    rig: dict[str, Path], tmp_path: Path
) -> None:
    """The wingman-shared case: constraints passed, nothing pinned, success reported."""
    _lock(rig)
    _stub(rig, tool_dir=_fake_tool_venv(tmp_path, DRIFTED))

    result = _run(rig)

    assert result.returncode != 0
    assert "did not honour uv.lock" in result.stderr
    assert "mcp: lock says 1.28.1, installed 1.29.0" in result.stderr
    assert "pydantic-settings: lock says 2.14.2, installed 2.15.0" in result.stderr
    assert "not what was tested" in result.stderr


def test_the_constraints_file_is_written_inside_the_checkout(rig: dict[str, Path]) -> None:
    """A snap-confined uv has a private /tmp and cannot see a mktemp file (#319)."""
    _lock(rig)
    _stub(rig)

    assert _run(rig).returncode == 0

    used = _constraints_path(rig)
    assert used.parent == rig["repo"]
    assert used.name == ".tool-constraints.txt"


def test_the_constraints_file_does_not_outlive_the_install(rig: dict[str, Path]) -> None:
    _lock(rig)
    _stub(rig)

    assert _run(rig).returncode == 0

    assert not _constraints_path(rig).exists()


def test_a_checkout_without_a_lockfile_still_installs_and_says_so(rig: dict[str, Path]) -> None:
    """Nothing to pin to is not a failure — and must not be verified against."""
    _stub(rig, tool_dir=_fake_tool_venv(rig["tmp"], DRIFTED))

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    assert "--constraints" not in _install_call(rig)
    assert "no uv.lock" in result.stdout
    assert "may differ from tested ones" in result.stdout
    assert "did not honour" not in result.stderr


def test_a_failed_export_falls_back_rather_than_blocking(rig: dict[str, Path]) -> None:
    _lock(rig)
    _stub(rig, export_rc=1, export_out="")

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    assert "--constraints" not in _install_call(rig)
    assert "could not be exported" in result.stdout


def test_an_empty_export_is_treated_as_a_failure(rig: dict[str, Path]) -> None:
    _lock(rig)
    _stub(rig, export_rc=0, export_out="")

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    assert "--constraints" not in _install_call(rig)


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


def test_a_locked_package_that_is_not_installed_at_all_is_a_mismatch(
    rig: dict[str, Path], tmp_path: Path
) -> None:
    """Copilot review on #321: a partial install must not pass verification."""
    _lock(rig)
    # mcp is pinned by EXPORTED but absent from the venv entirely.
    _stub(rig, tool_dir=_fake_tool_venv(tmp_path, {"pydantic-settings": "2.14.2"}))

    result = _run(rig)

    assert result.returncode != 0
    assert "mcp: locked but not installed at all" in result.stderr


def test_a_conditional_pin_may_be_absent_without_failing(
    rig: dict[str, Path], tmp_path: Path
) -> None:
    """A marker-guarded pin can legitimately not apply to this platform."""
    _lock(rig)
    _stub(
        rig,
        tool_dir=_fake_tool_venv(tmp_path, {"mcp": "1.28.1"}),
        export_out='mcp==1.28.1\npywin32==306 ; sys_platform == "win32"\n',
    )

    result = _run(rig)

    assert result.returncode == 0, result.stderr
    assert "no mismatches" in result.stdout


def test_a_constraints_file_with_no_pins_fails_rather_than_reporting_zero(
    rig: dict[str, Path],
) -> None:
    """Copilot review on #321: zero pins must not read as 'nothing differs'.

    The first version re-ran `uv export` into the same path during verification,
    so a failed export truncated the file and produced a cheerful
    "pinned: 0 locked packages, no mismatches".
    """
    _lock(rig)
    # Non-empty (so the export is accepted) but containing no `name==version`.
    _stub(rig, export_out="# generated by uv\n--hash=sha256:abc\n")

    result = _run(rig)

    assert result.returncode != 0
    assert "holds no pins" in result.stderr
    assert "no mismatches" not in result.stdout
