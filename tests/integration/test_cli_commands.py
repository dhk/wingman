from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    return data_dir


def test_init_creates_workspace(workspace: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert str(workspace) in result.stdout
    assert (workspace / "inbox").is_dir()
    assert (workspace / "reports").is_dir()
    assert (workspace / "wingman.db").is_file()


def test_init_is_idempotent(workspace: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["init"]).exit_code == 0


def test_status_before_init(workspace: Path) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "not initialized" in result.stdout
    assert "wingman init" in result.stdout


def test_status_after_init(workspace: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert str(workspace) in result.stdout
    assert "Source records: 0" in result.stdout


def test_doctor_before_init_fails_with_guidance(workspace: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "wingman init" in result.stdout


def test_doctor_after_init_passes(workspace: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "All checks passed." in result.stdout
    assert "[ok] database" in result.stdout
