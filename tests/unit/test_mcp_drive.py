"""RFC-053 (#205): the MCP surface — 'drive_auth' tool, and the 'drive'
param wired into the 'backup' tool. Device-flow/push mechanics themselves
are covered by test_gdrive_auth.py/test_gdrive_push.py; these confirm the
MCP tool functions call through and render results correctly."""

from __future__ import annotations

from pathlib import Path

import pytest

from wingman import mcp_server
from wingman.application.gdrive_push import DrivePushResult
from wingman.infrastructure.gdrive_auth import DeviceAuthResult, GDriveAuthError
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = mcp_server.load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    config.models_config_path.write_text('[models.embed_semantic]\nprovider = "hashed"\n')
    return tmp_path


def test_drive_auth_tool_returns_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_drive_auth() -> DeviceAuthResult:
        return DeviceAuthResult(
            status="started",
            detail="Open https://www.google.com/device and enter code WXYZ-1234.",
            user_code="WXYZ-1234",
        )

    monkeypatch.setattr(mcp_server, "run_drive_auth", fake_drive_auth)

    assert "WXYZ-1234" in mcp_server.drive_auth()


def test_drive_auth_tool_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_drive_auth() -> DeviceAuthResult:
        raise GDriveAuthError("device token exchange failed")

    monkeypatch.setattr(mcp_server, "run_drive_auth", fake_drive_auth)

    assert "drive auth failed" in mcp_server.drive_auth()


def test_backup_tool_includes_drive_line_by_default(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def fake_push_backup(archive_path: Path, home: Path | None = None) -> DrivePushResult:
        calls.append(archive_path)
        return DrivePushResult(status="skipped", detail="Drive: not authorized yet.")

    monkeypatch.setattr(mcp_server, "push_backup", fake_push_backup)

    output = mcp_server.backup()

    assert len(calls) == 1
    assert "Drive: not authorized yet." in output


def test_backup_tool_drive_false_skips_push_entirely(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def fake_push_backup(archive_path: Path, home: Path | None = None) -> DrivePushResult:
        calls.append(archive_path)
        return DrivePushResult(status="pushed", detail="Drive: pushed.")

    monkeypatch.setattr(mcp_server, "push_backup", fake_push_backup)

    output = mcp_server.backup(drive=False)

    assert calls == []
    assert "Drive:" not in output
