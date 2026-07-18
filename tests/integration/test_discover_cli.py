"""The discover command surfaces ranked suggestions and never adds anyone."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import wingman.application.people as people_module
from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    runner.invoke(app, ["init"])
    runner.invoke(app, ["people", "add", "Alpha", "--substack", "https://alpha.substack.com"])

    def fake_fetch(url: str) -> bytes:
        return b'<html><body><a href="https://fresh.substack.com/">fresh</a></body></html>'

    monkeypatch.setattr(people_module, "fetch_url", fake_fetch)
    return data_dir


def test_discover_suggests_without_adding(workspace: Path) -> None:
    result = runner.invoke(app, ["people", "discover"])
    assert result.exit_code == 0, result.output
    assert "https://fresh.substack.com" in result.output
    assert "recommended by 1: Alpha" in result.output
    assert "wingman people add" in result.output
    # suggestion only — the watchlist is unchanged
    status = runner.invoke(app, ["status"])
    assert "People: 1" in status.output


def test_discover_all_pages_failing_exits_nonzero(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.fetch import FetchError

    def down(url: str) -> bytes:
        raise FetchError("network unreachable")

    monkeypatch.setattr(people_module, "fetch_url", down)
    result = runner.invoke(app, ["people", "discover"])
    assert result.exit_code == 1
    assert "Every recommendations page failed" in result.output
