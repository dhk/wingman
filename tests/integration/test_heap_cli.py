"""The heap CLI end to end (#113): capture, list, remove."""

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
    runner.invoke(app, ["init"])
    return data_dir


def test_heap_add_show_remove(workspace: Path) -> None:
    added = runner.invoke(
        app,
        [
            "heap",
            "add",
            "https://x.example/job",
            "https://x.example/profile",
            "--heat",
            "hot",
            "--note",
            "event tomorrow",
        ],
    )
    assert added.exit_code == 0
    assert "Captured 2 item(s) at heat=hot" in added.stdout

    shown = runner.invoke(app, ["heap", "show"])
    assert shown.exit_code == 0
    assert "https://x.example/job" in shown.stdout
    assert "https://x.example/profile" in shown.stdout
    assert "[hot]" in shown.stdout
    assert "event tomorrow" in shown.stdout

    item_id = shown.stdout.splitlines()[0].split()[0]
    removed = runner.invoke(app, ["heap", "remove", item_id])
    assert removed.exit_code == 0
    assert "Removed:" in removed.stdout

    after = runner.invoke(app, ["heap", "show"])
    assert "1 item(s)" in after.stdout


def test_heap_add_rejects_unknown_heat(workspace: Path) -> None:
    result = runner.invoke(app, ["heap", "add", "https://x.example", "--heat", "scorching"])
    assert result.exit_code == 1
    assert "unknown heat" in result.output


def test_heap_remove_unknown_id_fails_visibly(workspace: Path) -> None:
    result = runner.invoke(app, ["heap", "remove", "deadbeef"])
    assert result.exit_code == 1
    assert "no heap item" in result.output
