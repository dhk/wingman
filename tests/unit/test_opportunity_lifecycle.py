"""The opportunity lifecycle (RFC-076, #496): an explicit, append-only journal."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.opportunities import list_opportunity_summaries, render_opportunity_listing
from wingman.application.opportunity_lifecycle import (
    current_stage,
    render_journal,
    resolve_opportunity,
    update_opportunity,
)
from wingman.application.pack import build_application_pack
from wingman.domain.opportunity import (
    FitVerdict,
    Opportunity,
    OpportunityStatus,
    Requirement,
    RequirementAssessment,
    RequirementKind,
)
from wingman.domain.profile import EvidenceSpan
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.storage import Storage

SRC = Path(__file__).resolve().parents[2] / "src" / "wingman"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _seed(storage: Storage, title: str, source_record_id: str) -> Opportunity:
    requirement = Requirement(
        name="SQL",
        kind=RequirementKind.REQUIRED,
        evidence=[EvidenceSpan(source_record_id="r", quote="asks for SQL")],
    )
    opportunity = Opportunity(
        title=title,
        source_record_id=source_record_id,
        next_action="decide",
        requirements=[requirement],
        assessments=[
            RequirementAssessment(
                requirement_id=requirement.requirement_id,
                verdict=FitVerdict.MET,
                rationale="because",
                confidence=0.5,
            )
        ],
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    storage.save_opportunity(opportunity)
    return opportunity


def test_a_new_opportunity_is_assessed_until_someone_says_otherwise(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        opportunity = _seed(storage, "EIR at PG&E", "r1")
        [summary] = list_opportunity_summaries(storage, workspace)
        assert summary.stage == "assessed"
        assert summary.stage_set_at is None
        assert current_stage(storage.list_opportunity_events(opportunity.opportunity_id)) is (
            OpportunityStatus.ASSESSED
        )


def test_stage_changes_append_and_a_revival_keeps_both_events(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        opportunity = _seed(storage, "Data Lead at Google", "r1")
        ref = opportunity.opportunity_id[:8]
        update_opportunity(ref, storage, stage="applied")
        update_opportunity(ref, storage, stage="rejected")
        update_opportunity(ref, storage, stage="interviewing")
        events = storage.list_opportunity_events(opportunity.opportunity_id)
        [summary] = list_opportunity_summaries(storage, workspace)

    assert [event.stage for event in events] == [
        OpportunityStatus.APPLIED,
        OpportunityStatus.REJECTED,
        OpportunityStatus.INTERVIEWING,
    ]
    assert summary.stage == "interviewing"
    assert "stage: interviewing since" in render_opportunity_listing([summary])
    journal = render_journal(opportunity, events)
    assert "stage -> rejected" in journal and journal.endswith("Current stage: interviewing")


def test_an_unconfirmed_note_saves_nothing_not_even_its_stage(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        opportunity = _seed(storage, "EIR at PG&E", "r1")
        outcome = update_opportunity(
            opportunity.opportunity_id, storage, stage="applied", note="Sent via the portal."
        )
        assert outcome.saved is False
        assert outcome.note == "Sent via the portal."
        assert storage.list_opportunity_events(opportunity.opportunity_id) == []


def test_a_confirmed_note_is_verbatim_and_never_becomes_evidence(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        opportunity = _seed(storage, "EIR at PG&E", "r1")
        records_before = storage.count_source_records()
        profile_before = len(storage.list_profile_items())
        outcome = update_opportunity(
            opportunity.opportunity_id,
            storage,
            note="Waiting on PG&E;   recruiter said two weeks.",
            note_confirmed=True,
        )
        assert outcome.saved is True
        [event] = storage.list_opportunity_events(opportunity.opportunity_id)
        assert event.note == "Waiting on PG&E; recruiter said two weeks."
        assert event.stage is None
        # A note-only event leaves the stage where it was.
        assert current_stage([event]) is OpportunityStatus.ASSESSED
        # Not a source record, not a profile item: nothing can cite it.
        assert storage.count_source_records() == records_before
        assert len(storage.list_profile_items()) == profile_before


def test_moving_to_the_current_stage_records_nothing(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        opportunity = _seed(storage, "EIR at PG&E", "r1")
        update_opportunity(opportunity.opportunity_id, storage, stage="applied")
        with pytest.raises(IngestError, match="already at 'applied'"):
            update_opportunity(opportunity.opportunity_id, storage, stage="applied")
        with pytest.raises(IngestError, match="give a stage"):
            update_opportunity(opportunity.opportunity_id, storage)
        with pytest.raises(IngestError, match="unknown stage"):
            update_opportunity(opportunity.opportunity_id, storage, stage="ghosted")
        assert len(storage.list_opportunity_events(opportunity.opportunity_id)) == 1


def test_resolution_by_prefix_or_title_and_ambiguity_is_refused(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        first = _seed(storage, "Data Lead at Google", "r1")
        _seed(storage, "Data Lead at Google", "r2")  # the same role, assessed twice
        _seed(storage, "EIR at PG&E", "r3")
        assert resolve_opportunity(first.opportunity_id[:8], storage) == first
        assert resolve_opportunity("pg&e", storage).title == "EIR at PG&E"
        with pytest.raises(IngestError, match="matches 2 opportunities"):
            resolve_opportunity("google", storage)
        with pytest.raises(IngestError, match="no opportunity matching"):
            resolve_opportunity("Sage", storage)


def test_reassessing_or_composing_a_pack_never_moves_the_stage(workspace: Config) -> None:
    """No transition is inferred: re-running assess for the same source and
    composing a pack both leave the journal exactly as the user left it."""
    with Storage(workspace.db_path) as storage:
        opportunity = _seed(storage, "Staff MLE at Acme", "r1")
        update_opportunity(opportunity.opportunity_id, storage, stage="shortlisted")
        reassessed = opportunity.model_copy(update={"next_action": "re-run"})
        storage.save_opportunity(reassessed)
        build_application_pack("staff mle", workspace, storage)
        [summary] = list_opportunity_summaries(storage, workspace)
        events = storage.list_opportunity_events(opportunity.opportunity_id)
    assert summary.stage == "shortlisted"
    assert len(events) == 1


def test_the_lifecycle_module_is_the_journals_only_writer() -> None:
    """RFC-076: stages are set by one explicit path. Any other module that
    appends journal events would be a way to infer a stage."""
    writers = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "add_opportunity_event(" in path.read_text(encoding="utf-8")
    )
    assert writers == ["application/opportunity_lifecycle.py", "infrastructure/storage.py"]


def test_mcp_tool_gates_the_note_but_not_the_stage(workspace: Config) -> None:
    from wingman.mcp_server import opportunity_update

    with Storage(workspace.db_path) as storage:
        opportunity = _seed(storage, "Hiring manager role at Sage", "r1")
    ref = opportunity.opportunity_id[:8]

    staged = opportunity_update(ref, stage="interviewing")
    assert "assessed -> interviewing" in staged

    pending = opportunity_update(ref, note="Thank Trent.")
    assert pending.startswith("Nothing saved")
    assert '"Thank Trent."' in pending

    saved = opportunity_update(ref, note="Thank Trent.", confirmed=True)
    assert 'note: "Thank Trent."' in saved

    history = opportunity_update(ref, action="history")
    assert "stage -> interviewing" in history and '"Thank Trent."' in history
    assert opportunity_update("nothing-like-it").startswith("opportunity update failed")
