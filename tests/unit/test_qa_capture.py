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


def test_resolve_requirement_reaches_job_criteria(workspace: Config) -> None:
    """A requirement resolvable ONLY via job-criteria.md — nothing in the
    profile, nothing in the answer bank — still surfaces through
    resolve_requirement's recall (#489). Before the fix, search_workspace
    never consulted the criteria doc at all, so this requirement would
    come back with no mention of the location language whatsoever."""
    from wingman.application.job_scoring import save_criteria
    from wingman.mcp_server import resolve_requirement

    save_criteria(
        workspace,
        "## Hard filters\n"
        "Location is deliberately not a filter: remote-first, hybrid up to "
        "~25%, and 2-3 days a week in an office are all acceptable.\n\n"
        "## Wants\n- Staff-level scope\n",
    )
    recall = resolve_requirement("remote hybrid office days")
    assert "Location is deliberately not a filter" in recall
    assert "job-criteria.md" in recall
    assert "Hard filters" in recall


SCREENING = "Have you shipped an AI/LLM product?"
SHIPPED = "Shipping Praxis, Wingman, Skill-Map and Tricorder."


def test_screening_question_goes_to_the_answer_bank_not_the_profile(workspace: Config) -> None:
    """A question an employer asked is not a claim about a career. Stored in
    the profile it makes a QUESTION the name of an achievement (#283)."""
    from wingman.application.answers import find_similar

    with Storage(workspace.db_path) as storage:
        report = capture_qa(SCREENING, SHIPPED, workspace, storage, destination="answers")

        assert report.kind == "answer"
        assert report.outcome == "saved"
        assert "answer bank" in report.source_path
        # Nothing entered the profile.
        assert storage.list_profile_items() == []
        # And it is findable where it belongs.
        assert find_similar(SCREENING, storage)


def test_resaving_a_screening_answer_revises_the_bank_entry(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        capture_qa(SCREENING, SHIPPED, workspace, storage, destination="answers")
        again = capture_qa(
            SCREENING, SHIPPED + " Also Alexandria.", workspace, storage, destination="answers"
        )
        assert again.kind == "answer"
        assert storage.list_profile_items() == []


def test_profile_remains_the_default_destination(workspace: Config) -> None:
    """Today's behavior is unchanged for anyone who does not ask."""
    with Storage(workspace.db_path) as storage:
        report = capture_qa(QUESTION, ANSWER, workspace, storage)
        assert report.kind == ProfileItemKind.ACHIEVEMENT.value
        assert len(storage.list_profile_items()) == 1


def test_unknown_destination_is_refused_and_names_where_preferences_go(
    workspace: Config,
) -> None:
    """Preferences are deliberately not a destination: RFC-035 says criteria
    are the user's own words and are never written unilaterally."""
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError) as excinfo:
            capture_qa(QUESTION, ANSWER, workspace, storage, destination="criteria")
        message = str(excinfo.value)
        assert "profile, answers" in message
        assert "job-criteria" in message
        assert "score nothing here" in message
        assert storage.list_profile_items() == []
