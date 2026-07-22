"""RFC-036 (#96/#98): Q&A capture as evidence, and recall-before-asking."""

from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.qa_capture import QA_SOURCE_TYPE, capture_qa, qa_document_key
from wingman.domain.profile import ItemStatus, ProfileItemKind
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage

QUESTION = "What is your commitment to safe AI development?"
ANSWER = "I led the safety review board at Acme and shipped the eval gating pipeline."


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    config.inbox_dir.mkdir(parents=True)
    Storage(config.db_path).close()
    return config


def test_capture_creates_source_file_record_and_item(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        report = capture_qa(QUESTION, ANSWER, workspace, storage)
        assert report.outcome == "saved"
        items = [i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE]
        assert len(items) == 1
        item = items[0]
        assert item.name == QUESTION and item.detail == ANSWER
        assert item.kind is ProfileItemKind.ACHIEVEMENT
        assert item.extracted_by == "user" and item.confidence == 1.0
        assert item.evidence[0].quote == ANSWER  # the answer IS the evidence
        record = storage.get_source_record(item.evidence[0].source_record_id)
        assert record is not None and record.source_type == QA_SOURCE_TYPE
        assert record.document_key == qa_document_key(QUESTION)
        # the evidence file exists and contains the quote verbatim
        source = workspace.data_dir / record.source_locator
        assert ANSWER in source.read_text(encoding="utf-8")


def test_reanswer_supersedes_instead_of_conflicting(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        capture_qa(QUESTION, ANSWER, workspace, storage)
        report = capture_qa(QUESTION, "A better, current answer.", workspace, storage)
        assert "superseded" in report.outcome
        active = [i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE]
        superseded = [i for i in storage.list_profile_items() if i.status is ItemStatus.SUPERSEDED]
        assert [i.detail for i in active] == ["A better, current answer."]
        assert [i.detail for i in superseded] == [ANSWER]  # kept for provenance
        # identical re-capture changes nothing
        again = capture_qa(QUESTION, "A better, current answer.", workspace, storage)
        assert "already captured" in again.outcome


def test_capture_validates_inputs(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="required"):
            capture_qa("  ", ANSWER, workspace, storage)
        with pytest.raises(IngestError, match="unknown kind"):
            capture_qa(QUESTION, ANSWER, workspace, storage, kind="vibe")
        with pytest.raises(IngestError, match="unknown classification"):
            capture_qa(QUESTION, ANSWER, workspace, storage, classification="guess")


def test_mcp_qa_capture_and_resolve_requirement(workspace: Config) -> None:
    from wingman.application.answers import save_answer
    from wingman.mcp_server import qa_capture as qa_capture_tool
    from wingman.mcp_server import resolve_requirement

    result = qa_capture_tool(QUESTION, ANSWER)
    assert "saved" in result and QUESTION in result
    with Storage(workspace.db_path) as storage:
        save_answer(
            "Why do you care about AI safety?",
            "Because deployment safety gated every launch I ran.",
            storage,
            company="Acme",
        )
    recall = resolve_requirement("deployment safety experience")
    assert "Banked answers" in recall  # RFC-030 recall surfaced
    assert "deployment safety" in recall
    assert "failed" not in recall.splitlines()[0]
    # protocol rides the docstrings (RFC-025/030/031/035 convention)
    assert "AskUserQuestion" in (resolve_requirement.__doc__ or "")
    assert "raw bullets" in (resolve_requirement.__doc__ or "")
    assert "Never capture silently" in (qa_capture_tool.__doc__ or "")
    assert "failed" in qa_capture_tool("", "")
