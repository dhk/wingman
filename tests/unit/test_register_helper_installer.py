"""The installer for the registration helper (RFC-086, #658): a dry run shows
exactly what would be written, and refuses anything it would have to escape."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "wingman-install-register-helper.sh"
HELPER = Path(__file__).parents[2] / "scripts" / "wingman-register-tenant.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def test_a_dry_run_shows_the_one_sudoers_rule_and_writes_nothing() -> None:
    result = _run(
        "--service-user",
        "wingman-shared",
        "--data-root",
        "/home/wingman-shared/tenants",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "wingman-shared ALL=(root) NOPASSWD: /usr/local/libexec/wingman/register-tenant" in out
    assert out.count("NOPASSWD") == 1
    assert "DATA_ROOT=/home/wingman-shared/tenants" in out
    assert "REGISTRY=/etc/wingman/tenants.toml" in out
    assert not os.path.exists("/etc/sudoers.d/wingman-register-tenant")


@pytest.mark.parametrize(
    "args",
    [
        ["--service-user", "bad user", "--data-root", "/d"],
        ["--service-user", "x\nroot ALL=(ALL) NOPASSWD: ALL", "--data-root", "/d"],
        ["--service-user", "wingman-shared", "--data-root", "relative/dir"],
        ["--service-user", "wingman-shared", "--data-root", "/d; rm -rf /"],
        ["--service-user", "wingman-shared", "--data-root", "/d", "--registry", "/r\nREGISTRY=/x"],
        ["--service-user", "wingman-shared"],
        ["--data-root", "/d"],
    ],
)
def test_it_refuses_anything_it_would_have_to_escape(args: list[str]) -> None:
    result = _run(*args, "--dry-run")
    assert result.returncode != 0
    assert "NOPASSWD" not in result.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="the real run is only refused for non-root")
def test_a_real_run_needs_root() -> None:
    result = _run("--service-user", "wingman-shared", "--data-root", "/tmp")
    assert result.returncode == 1 and "run as root" in result.stderr


def test_the_installed_files_are_what_the_runbook_says() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'HELPER_DIR="/usr/local/libexec/wingman"' in text
    assert 'HELPER_DEST="$HELPER_DIR/register-tenant"' in text
    assert HELPER.name in text
    assert text.count("NOPASSWD") >= 1 and "ALL=(root) NOPASSWD: ALL" not in text
