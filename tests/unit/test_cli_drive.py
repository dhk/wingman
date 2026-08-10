"""RFC-053 (#205): the CLI surface — 'wingman drive auth', and the
'--drive/--no-drive' flag wired into 'wingman backup'. The device-flow
state machine itself is covered by test_gdrive_auth.py; these tests only
confirm the CLI calls through to it correctly and renders the result."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.gdrive_push import DrivePushResult
from wingman.cli import main as cli_main
from wingman.cli.main import app
from wingman.infrastructure.gdrive_auth import DeviceAuthResult, GDriveAuthError
from wingman.infrastructure.storage import Storage

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = cli_main.load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    config.models_config_path.write_text('[models.embed_semantic]\nprovider = "hashed"\n')
    return tmp_path


def test_drive_auth_started_prints_code_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_drive_auth() -> DeviceAuthResult:
        return DeviceAuthResult(
            status="started",
            detail="Open https://www.google.com/device and enter code ABCD-EFGH.",
            user_code="ABCD-EFGH",
            verification_url="https://www.google.com/device",
            expires_in=1800,
        )

    monkeypatch.setattr(cli_main, "run_drive_auth", fake_drive_auth)

    result = runner.invoke(app, ["drive", "auth"])

    assert result.exit_code == 0
    assert "ABCD-EFGH" in result.output


def test_drive_auth_authorized_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_drive_auth() -> DeviceAuthResult:
        return DeviceAuthResult(status="authorized", detail="Connected.")

    monkeypatch.setattr(cli_main, "run_drive_auth", fake_drive_auth)

    result = runner.invoke(app, ["drive", "auth"])

    assert result.exit_code == 0
    assert "Connected." in result.output


def test_drive_auth_pending_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_drive_auth() -> DeviceAuthResult:
        return DeviceAuthResult(status="pending", detail="Still waiting for approval.")

    monkeypatch.setattr(cli_main, "run_drive_auth", fake_drive_auth)

    result = runner.invoke(app, ["drive", "auth"])

    assert result.exit_code == 1
    assert "Still waiting" in result.output


def test_drive_auth_error_surfaces_and_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_drive_auth() -> DeviceAuthResult:
        raise GDriveAuthError("could not reach Google's OAuth endpoint")

    monkeypatch.setattr(cli_main, "run_drive_auth", fake_drive_auth)

    result = runner.invoke(app, ["drive", "auth"])

    assert result.exit_code == 1
    assert "drive auth failed" in result.output


def test_backup_pushes_to_drive_by_default(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def fake_push_backup(archive_path: Path, home: Path | None = None) -> DrivePushResult:
        calls.append(archive_path)
        return DrivePushResult(status="skipped", detail="Drive: not authorized yet.")

    monkeypatch.setattr(cli_main, "push_backup", fake_push_backup)

    result = runner.invoke(app, ["backup", str(workspace / "out")])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "Drive: not authorized yet." in result.output


def test_backup_no_drive_skips_the_push_call_entirely(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def fake_push_backup(archive_path: Path, home: Path | None = None) -> DrivePushResult:
        calls.append(archive_path)
        return DrivePushResult(status="pushed", detail="Drive: pushed.")

    monkeypatch.setattr(cli_main, "push_backup", fake_push_backup)

    result = runner.invoke(app, ["backup", str(workspace / "out"), "--no-drive"])

    assert result.exit_code == 0
    assert calls == []
    assert "Drive:" not in result.output


def test_overnight_pushes_the_digest_when_drive_is_on(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI overnight branch had no coverage at all — only backup did — so
    the digest path handed to push_digest could regress unnoticed (#231 review)."""
    from wingman.application.focus import OvernightReport

    digest = workspace / "digest-2026-08-10.md"
    digest.write_text("# digest", encoding="utf-8")
    pushed: list[Path] = []

    monkeypatch.setattr(
        cli_main,
        "overnight_run",
        lambda config, storage, out_dir=None: OvernightReport(
            targets=[], processed=0, failed=0, actions=[], digest_path=str(digest)
        ),
    )
    monkeypatch.setattr(
        cli_main,
        "push_digest",
        lambda path: (
            pushed.append(path),
            DrivePushResult(status="pushed", detail="Drive: pushed to Wingman/digests/x.md"),
        )[1],
    )

    result = runner.invoke(app, ["overnight"])

    assert result.exit_code == 0, result.output
    assert pushed == [digest]
    assert "Drive: pushed to" in result.output


def test_overnight_leaves_drive_alone_when_asked(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.application.focus import OvernightReport

    digest = workspace / "digest-2026-08-10.md"
    digest.write_text("# digest", encoding="utf-8")
    pushed: list[Path] = []

    monkeypatch.setattr(
        cli_main,
        "overnight_run",
        lambda config, storage, out_dir=None: OvernightReport(
            targets=[], processed=0, failed=0, actions=[], digest_path=str(digest)
        ),
    )
    monkeypatch.setattr(
        cli_main,
        "push_digest",
        lambda path: pushed.append(path),  # must never be called
    )

    result = runner.invoke(app, ["overnight", "--no-drive"])

    assert result.exit_code == 0, result.output
    assert pushed == []
    assert "Drive" not in result.output
