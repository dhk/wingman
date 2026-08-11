"""The commentary corpus end to end (#339), on both surfaces RFC-008 requires.

CLI and MCP tool, same store, same refusals — the parity rule is the point:
a capture that only exists in conversation is exactly what this issue was
filed about, and one that only exists in a terminal is no better.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR

runner = CliRunner()

READING = "Zarquon: your con nominations and your job criteria are one argument."


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    runner.invoke(app, ["init"])
    return data_dir


def test_save_list_show_find_remove(workspace: Path) -> None:
    saved = runner.invoke(
        app,
        [
            "commentary",
            "save",
            READING,
            "--topic",
            "values",
            "--model",
            "claude-opus-4",
            "--prompt-version",
            "none",
        ],
    )
    assert saved.exit_code == 0
    assert "Saved commentary [" in saved.stdout
    assert "claude-opus-4 · prompt none" in saved.stdout

    listed = runner.invoke(app, ["commentary", "list", "--full"])
    assert listed.exit_code == 0
    assert "not your own words" in listed.stdout
    assert READING in listed.stdout
    assert "1 entry." in listed.stdout

    entry_id = listed.stdout.split("[")[1].split("]")[0]
    shown = runner.invoke(app, ["commentary", "show", entry_id])
    assert shown.exit_code == 0 and READING in shown.stdout

    found = runner.invoke(app, ["commentary", "find", "Zarquon"])
    assert found.exit_code == 0 and READING in found.stdout
    missed = runner.invoke(app, ["commentary", "find", "dashboards"])
    assert missed.exit_code == 0 and "No commentary saved yet" in missed.stdout

    removed = runner.invoke(app, ["commentary", "remove", entry_id])
    assert removed.exit_code == 0 and "Removed commentary" in removed.stdout
    assert "No commentary saved yet" in runner.invoke(app, ["commentary", "list"]).stdout


def test_status_names_the_store_so_it_is_findable(workspace: Path) -> None:
    runner.invoke(app, ["commentary", "save", READING, "--model", "claude-opus-4"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Commentary entries: 1 (the assistant's readings — never evidence)" in result.stdout


def test_a_citation_that_points_at_nothing_is_refused(workspace: Path) -> None:
    result = runner.invoke(
        app, ["commentary", "save", READING, "--from", "no-such-id", "--model", "m"]
    )
    assert result.exit_code == 1
    assert "nothing in this workspace has id" in result.output


def test_mcp_tool_saves_lists_and_removes_the_same_store(workspace: Path) -> None:
    from wingman.mcp_server import commentary, status

    saved = commentary(action="save", text=READING, model="claude-opus-4", topic="values")
    assert "Saved commentary [" in saved
    assert "never as the user's evidence" in saved

    listed = commentary(action="list")
    assert READING in listed and "not your own words" in listed
    assert "Commentary entries: 1" in status()

    entry_id = listed.split("[")[1].split("]")[0]
    assert READING in commentary(action="show", entry_id=entry_id)
    assert READING in commentary(action="find", query="Zarquon")
    assert "Removed commentary" in commentary(action="remove", entry_id=entry_id)
    assert "No commentary saved yet" in commentary(action="list")


def test_mcp_tool_reports_failures_instead_of_raising(workspace: Path) -> None:
    from wingman.mcp_server import commentary

    assert "commentary save failed" in commentary(action="save", text="   ")
    assert "commentary show failed" in commentary(action="show", entry_id="deadbeef")
    assert "unknown action" in commentary(action="sort")
    assert "nothing in this workspace has id" in commentary(
        action="save", text=READING, drawn_from=["no-such-id"]
    )
