"""Correcting your own writing, on both surfaces RFC-008 requires (RFC-078).

The corpus holds the user's own material, and a person's own writing
evolves. Until this change every correction could only be an addition:
`corpus add` had no lineage and there was no `remove`, so an essay rewritten
twice sat in the pool three times over, each version quotable against the
others. These tests hold the drain open on the CLI and over MCP.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR

runner = CliRunner()

DRAFT = "# On leverage\n\nWarm introductions beat cold applications, usually.\n"
REVISED = "# On leverage\n\nWarm introductions beat cold applications, measurably.\n"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    runner.invoke(app, ["init"])
    return data_dir


def test_re_adding_an_edited_essay_replaces_it_rather_than_duplicating_it(
    workspace: Path, tmp_path: Path
) -> None:
    essay = tmp_path / "on-leverage.md"
    essay.write_text(DRAFT, encoding="utf-8")
    assert runner.invoke(app, ["corpus", "add", str(essay)]).exit_code == 0

    essay.write_text(REVISED, encoding="utf-8")
    result = runner.invoke(app, ["corpus", "add", str(essay)])
    assert result.exit_code == 0
    assert "Replaced: 1" in result.stdout
    assert "their source records and archived files stay" in result.stdout

    listed = runner.invoke(app, ["corpus", "list"])
    assert listed.stdout.count("On leverage") == 1
    assert "measurably" in runner.invoke(app, ["evidence", "measurably"]).stdout
    assert "No corpus evidence" in runner.invoke(app, ["evidence", "usually"]).stdout


def test_remove_and_its_refusals(workspace: Path, tmp_path: Path) -> None:
    essay = tmp_path / "on-leverage.md"
    essay.write_text(DRAFT, encoding="utf-8")
    runner.invoke(app, ["corpus", "add", str(essay)])
    doc_id = runner.invoke(app, ["corpus", "list"]).stdout.split()[0]

    removed = runner.invoke(app, ["corpus", "remove", doc_id[:8]])
    assert removed.exit_code == 0
    assert "Removed [" in removed.stdout and "On leverage" in removed.stdout
    assert "the ingest still happened" in removed.stdout
    assert "Corpus is empty" in runner.invoke(app, ["corpus", "list"]).stdout

    missing = runner.invoke(app, ["corpus", "remove", doc_id[:8]])
    assert missing.exit_code == 1
    assert "no corpus document" in missing.output


def test_mcp_tool_lists_and_removes_the_same_store(workspace: Path, tmp_path: Path) -> None:
    from wingman.mcp_server import corpus

    essay = tmp_path / "on-leverage.md"
    essay.write_text(DRAFT, encoding="utf-8")
    runner.invoke(app, ["corpus", "add", str(essay)])

    listed = corpus(action="list")
    assert "On leverage" in listed
    doc_id = listed.split("[")[1].split("]")[0]

    removed = corpus(action="remove", doc_id=doc_id)
    assert "Removed [" in removed and "the ingest still happened" in removed
    assert "Corpus is empty" in corpus(action="list")


def test_mcp_tool_reports_failures_instead_of_raising(workspace: Path) -> None:
    from wingman.mcp_server import corpus

    assert "corpus remove failed" in corpus(action="remove", doc_id="deadbeef")
    assert "unknown action" in corpus(action="add")


def test_the_removal_protocol_is_in_the_docstring(workspace: Path) -> None:
    """The gate is a protocol the calling model follows, not a flag it can pass."""
    from wingman.mcp_server import corpus

    doc = corpus.__doc__ or ""
    assert "confirm before removing" in doc
    assert "only after the user says yes" in doc
