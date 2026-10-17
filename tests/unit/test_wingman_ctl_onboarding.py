"""Operator wrappers resolve references and never display full subjects."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

CTL = Path(__file__).resolve().parents[2] / "scripts/wingman-ctl"


@pytest.mark.parametrize("command", ["bind", "approve"])
@pytest.mark.parametrize("answer", ["\n", "y\n"])
@pytest.mark.parametrize("rows", [0, 1, 2])
def test_bind_reference_preflight_confirmation_and_redaction(
    tmp_path: Path, rows: int, answer: str, command: str
) -> None:
    etc = tmp_path / "etc/wingman"
    etc.mkdir(parents=True)
    shared = tmp_path / "shared"
    binary = shared / ".local/bin/wingman"
    binary.parent.mkdir(parents=True)
    (etc / "oauth.env").write_text("WINGMAN_OAUTH_ARGS='--oauth-identities \"/test/map.toml\"'\n")
    log = tmp_path / "calls"
    binary.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        'case "$*" in *oauth-pending*)\n'
        + "\n".join(
            'echo "issuer=https://issuer.test subject=synthetic-full-subject-9876 ref=ABCD-1234"'
            for _ in range(rows)
        )
        + '\n;; *) echo "subject=synthetic-full-subject-9876";; esac\n'
    )
    binary.chmod(0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "getent": f'echo "wingman-shared:x:1:1::{shared}:/bin/bash"',
        "sudo": 'while [ "$1" = "-H" ] || [ "$1" = "-u" ]; do if [ "$1" = "-u" ]; then shift 2; else shift; fi; done; exec "$@"',
    }.items():
        path = bindir / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    result = subprocess.run(
        ["bash", str(CTL), command, "ABCD-1234", "person"],
        input=answer,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "WINGMAN_HOST_ROOT": str(tmp_path),
        },
    )
    assert "synthetic-full-subject" not in result.stdout + result.stderr
    if rows == 1:
        assert result.returncode == 0, result.stderr
        assert "9876" in result.stdout
        assert "[y/N]" in result.stdout
        assert "--preflight" in log.read_text()
        assert sum(
            ("oauth-bind" in line or "oauth-approve" in line)
            for line in log.read_text().splitlines()
        ) == (2 if answer.startswith("y") else 1)
        assert all(
            "oauth-pending" in line or "oauth-bind" in line or "oauth-approve" in line
            for line in log.read_text().splitlines()
        )
    else:
        assert result.returncode != 0
        assert f"{rows} pending rows" in result.stdout + result.stderr


def test_onboarding_help() -> None:
    result = subprocess.run(
        ["bash", str(CTL), "invite", "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "--csv" in result.stdout


def test_error_subjects_are_redacted_even_without_subject_label() -> None:
    import runpy

    safe = runpy.run_path(str(CTL.with_name("wingman-oauth-operator.py")))["safe"]
    output = safe("duplicate identity ('https://issuer.test', 'user_SYNTHETICFULLSUBJECT1234')")
    assert "user_SYNTHETICFULLSUBJECT1234" not in output
    assert "1234" in output
    issuer = "https://api.workos.com/user_management/client_synthetic"
    assert safe(issuer) == issuer
