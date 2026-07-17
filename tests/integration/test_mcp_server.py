"""The MCP tools are thin wrappers over the application layer — call them directly."""

import asyncio
from pathlib import Path

import pytest

from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.mcp_server import (
    assess_job,
    career_profile,
    evidence,
    ingest_resume_text,
    server,
    status,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "ws"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    from wingman.infrastructure.storage import Storage

    Storage(config.db_path).close()
    return data_dir


def test_all_tools_are_registered() -> None:
    tools = {tool.name for tool in asyncio.run(server.list_tools())}
    assert tools == {"status", "evidence", "career_profile", "assess_job", "ingest_resume_text"}


def test_tools_report_uninitialized_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "nowhere"))
    assert "not initialized" in status()
    assert "not initialized" in evidence("anything")
    assert "not initialized" in career_profile()


def test_status_and_evidence_roundtrip(workspace: Path, tmp_path: Path) -> None:
    from wingman.application.corpus import add_to_corpus
    from wingman.infrastructure.storage import Storage

    essay = tmp_path / "essay.md"
    essay.write_text("# Streaming\n\nWe moved billing to Apache Kafka.\n", encoding="utf-8")
    config = load_config()
    with Storage(config.db_path) as storage:
        add_to_corpus(essay, "writing", config, storage)

    assert "Corpus documents: 1" in status()
    found = evidence("kafka")
    assert "Streaming" in found
    assert "source: inbox/" in found
    assert "No corpus evidence found" in evidence("flamingo")
    assert "Search failed" in evidence('"unbalanced')


def test_ingest_and_profile_via_recorded_provider(workspace: Path) -> None:
    config = load_config()
    response = FIXTURES / "profile_extraction" / "case_001_basic" / "response.json"
    config.models_config_path.write_text(
        f'[models.extract_fast]\nprovider = "recorded"\npath = "{response}"\n',
        encoding="utf-8",
    )
    resume = (FIXTURES / "profile_extraction" / "case_001_basic" / "resume.md").read_text(
        encoding="utf-8"
    )
    result = ingest_resume_text(resume)
    assert "Accepted: 4" in result

    profile = career_profile()
    assert "# Career Profile" in profile
    assert "Apache Kafka" in profile


def test_assess_requires_profile_and_fails_visibly(workspace: Path) -> None:
    config = load_config()
    config.models_config_path.write_text(
        '[models.extract_fast]\nprovider = "recorded"\npath = "/nonexistent"\n',
        encoding="utf-8",
    )
    result = assess_job("# Some Role\n\n- Requirement one.\n")
    assert result.startswith("Assessment failed:")


def test_empty_inputs_do_nothing(workspace: Path) -> None:
    assert "nothing was assessed" in assess_job("   ")
    assert "nothing was ingested" in ingest_resume_text("   ")
