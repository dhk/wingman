"""Status describes the configured shared host without exposing tenant details."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

CTL = Path(__file__).resolve().parents[2] / "scripts" / "wingman-ctl"


@pytest.mark.parametrize("personal", [False, True])
def test_shared_status_without_personal_server(tmp_path: Path, personal: bool) -> None:
    etc = tmp_path / "etc" / "wingman"
    etc.mkdir(parents=True)
    (etc / "tenants.toml").write_text(
        '[[tenant]]\nslug="private-slug"\ndata_dir="/private/workspace"\n'
    )
    (etc / "oauth-config-path").write_text(str(etc / "oauth.env"))
    (etc / "oauth.env").write_text("WINGMAN_OAUTH_ARGS=--oauth-identities /absent/map.toml\n")
    secret = tmp_path / "shared/.config/wingman/secrets.env"
    secret.parent.mkdir(parents=True)
    secret.write_text("WINGMAN_OPERATOR_TODOIST_TOKEN=never-show-this\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "wingman": 'echo "The HTTP MCP server is not running"',
        "getent": f'echo "wingman-shared:x:1:1::{tmp_path}/shared:/bin/bash"',
        "systemctl": 'echo "ActiveState=active"; echo "ActiveEnterTimestamp=Fri 2026-10-16"',
        "curl": 'case "$*" in *well-known*) printf 200;; *) printf 401;; esac',
        "pgrep": "exit 1",
    }.items():
        path = bindir / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    if personal:
        unit = tmp_path / ".config/systemd/user/wingman-mcp.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\n")
    result = subprocess.run(
        ["bash", str(CTL), "status"],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "WINGMAN_HOST_ROOT": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "Shared HTTP service" in result.stdout
    assert ("not running" in result.stdout) is personal
    assert "Tenants: 1" in result.stdout
    assert "Operator notifications: on" in result.stdout
    assert "never-show-this" not in result.stdout
    assert "private-slug" not in result.stdout
    assert "PRM: 200" in result.stdout
    assert "/mcp: 401" in result.stdout


def test_unreadable_shared_config_needs_sudo(tmp_path: Path) -> None:
    etc = tmp_path / "etc/wingman"
    etc.mkdir(parents=True)
    registry = etc / "tenants.toml"
    registry.write_text("[[tenant]]\nslug='hidden'\ndata_dir='/hidden'\n")
    registry.chmod(0)
    try:
        result = subprocess.run(
            ["python3", str(CTL.with_name("wingman-shared-status.py")), "--home", str(tmp_path)],
            env={**os.environ, "WINGMAN_HOST_ROOT": str(tmp_path)},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        registry.chmod(0o600)
    assert result.returncode == 0
    assert "Tenants: (needs sudo)" in result.stdout
    assert "hidden" not in result.stdout
