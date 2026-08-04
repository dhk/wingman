"""'wingman telemetry summary' (issue #223), exercised through the real CLI."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.telemetry import record_event, set_enabled

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    return data_dir


def test_summary_with_no_events(workspace: Path) -> None:
    result = runner.invoke(app, ["telemetry", "summary"])
    assert result.exit_code == 0
    assert "Sessions: 0" in result.stdout


def test_summary_reports_sessions_and_dead_ends(workspace: Path) -> None:
    config = load_config()
    set_enabled(config, True)
    record_event(config, "cli", "search", {}, ts="2026-08-01T09:00:00+00:00")
    record_event(config, "cli", "status", {}, ts="2026-08-01T11:00:00+00:00")

    result = runner.invoke(app, ["telemetry", "summary", "--gap-minutes", "30", "--top", "5"])
    assert result.exit_code == 0
    assert "Sessions: 2" in result.stdout
    assert "search" in result.stdout


def test_summary_rejects_bad_gap(workspace: Path) -> None:
    result = runner.invoke(app, ["telemetry", "summary", "--gap-minutes", "0"])
    assert result.exit_code == 1
    assert "telemetry summary failed" in result.output
