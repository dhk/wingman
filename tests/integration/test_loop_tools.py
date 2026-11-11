"""Exercise the actual MCP and CLI entry points, including telemetry wrapping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage
from wingman.mcp_server import loop_answer, loop_list, loop_start


def test_mcp_empty_workspace_preview_confirm_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "empty"
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(ws))
    assert json.loads(loop_list())["loops"][0]["id"] == "goals-friction"
    result = json.loads(loop_start())
    assert result["card"]["id"] == "care"
    sid = result["session_id"]
    preview = json.loads(loop_answer(sid, "Keep the exact wording"))
    with Storage(Config(data_dir=ws, data_dir_source="test").db_path) as storage:
        assert not storage.list_goals_plan("self")
    result = json.loads(loop_answer(sid, action="confirm", confirmation=preview["confirmation"]))
    assert result["saved"][0]["text"] == "Keep the exact wording"
    assert json.loads(loop_start())["card"]["id"] == "hard"
    assert json.loads(loop_answer("nonexistent"))["status"] == "error"


def test_cli_same_three_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "cli"))
    runner = CliRunner()
    listing = runner.invoke(app, ["loop", "list"])
    assert listing.exit_code == 0, listing.output
    assert json.loads(listing.output)["loops"][0]["id"] == "goals-friction"
    first = runner.invoke(app, ["loop", "start"])
    assert first.exit_code == 0, first.output
    sid = json.loads(first.output)["session_id"]
    preview = runner.invoke(app, ["loop", "answer", sid, "--answer", "Make useful things"])
    assert preview.exit_code == 0, preview.output
    token = json.loads(preview.output)["confirmation"]
    result = runner.invoke(
        app, ["loop", "answer", sid, "--action", "confirm", "--confirmation", token]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["card"]["id"] == "hard"
    invalid = runner.invoke(
        app, ["loop", "answer", sid, "--action", "confirm", "--confirmation", "bad"]
    )
    assert invalid.exit_code == 1
