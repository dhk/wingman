"""Version surfacing: git-derived build stamp, visible at every first touch."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.version import wingman_version


def test_version_is_a_nonempty_pep440_string() -> None:
    version = wingman_version()
    assert version and version[0].isdigit()


def test_cli_version_flag() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"wingman {wingman_version()}" in result.output


def test_status_and_doctor_show_the_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()

    from wingman.mcp_server import status as mcp_status

    assert f"Wingman: {wingman_version()}" in mcp_status()
    result = CliRunner().invoke(app, ["status"])
    assert f"Wingman: {wingman_version()}" in result.output
    doctor = CliRunner().invoke(app, ["doctor"])
    assert f"[ok] version: {wingman_version()}" in doctor.output
