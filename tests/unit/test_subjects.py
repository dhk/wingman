"""Relationships beyond people (RFC-085, #647): companies, opportunities,
initiatives and content pieces, linked across timelines."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.opportunity_lifecycle import current_stage
from wingman.application.people import add_person
from wingman.application.relationship import list_log
from wingman.application.subjects import (
    add_subject,
    aim_content,
    content_aimed_at,
    load_subject_objective,
    log_for_subject,
    render_subject,
    render_timeline,
    resolve_subject,
    save_subject_objective,
    timeline,
    unaim_content,
)
from wingman.domain.opportunity import (
    FitVerdict,
    Opportunity,
    OpportunityStatus,
    Requirement,
    RequirementAssessment,
    RequirementKind,
)
from wingman.domain.profile import EvidenceSpan
from wingman.domain.relationship import EvidenceTier
from wingman.domain.subject import SubjectKind
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage

TRENT_NOTE = (
    "Trent got me a hiring-manager interview at Sage and may introduce me "
    "to the first customer for my AI-leadership practice."
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _opportunity(storage: Storage, title: str = "EIR at PG&E") -> Opportunity:
    requirement = Requirement(
        name="SQL",
        kind=RequirementKind.REQUIRED,
        evidence=[EvidenceSpan(source_record_id="r", quote="asks for SQL")],
    )
    opportunity = Opportunity(
        title=title,
        source_record_id=f"src-{title}",
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


def _seed_trent(storage: Storage) -> None:
    add_person("Trent Lowe", storage)
    add_subject(SubjectKind.INITIATIVE, "AI-leadership practice", storage)


def test_one_event_appears_in_every_linked_timeline(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        _seed_trent(storage)
        report = log_for_subject(
            SubjectKind.PERSON,
            "Trent Lowe",
            TRENT_NOTE,
            workspace,
            storage,
            links=["company:Sage", "initiative:AI-leadership practice"],
        )
        # Still an ordinary RFC-037 person entry.
        _, person_entries = list_log("Trent Lowe", storage)
        assert [entry.entry_id for entry in person_entries] == [report.entry_id]

        sage = resolve_subject(SubjectKind.COMPANY, "Sage", storage)
        practice = resolve_subject(SubjectKind.INITIATIVE, "ai-leadership practice", storage)
        for subject in (sage, practice):
            [entry] = timeline(subject, storage)
            assert entry.entry_id == report.entry_id
            assert entry.text == TRENT_NOTE
            rendered = render_timeline(subject, [entry])
            assert "(logged against person Trent Lowe)" in rendered


def test_an_unknown_link_writes_nothing_at_all(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        _seed_trent(storage)
        records_before = storage.count_source_records()
        with pytest.raises(IngestError, match="no initiative named"):
            log_for_subject(
                SubjectKind.PERSON,
                "Trent Lowe",
                TRENT_NOTE,
                workspace,
                storage,
                links=["company:Sage", "initiative:Typo practice"],
            )
        with pytest.raises(IngestError, match="kind:name"):
            log_for_subject(
                SubjectKind.PERSON, "Trent Lowe", TRENT_NOTE, workspace, storage, links=["Sage"]
            )
        assert storage.count_source_records() == records_before
        assert storage.list_log_entries() == []
    assert list(workspace.inbox_dir.iterdir()) == []


def test_a_company_with_no_feed_logs_without_inventing_a_person(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Someone Else", storage)
        people_before = storage.list_people()
        report = log_for_subject(
            SubjectKind.COMPANY,
            "PG&E",
            "Waiting on PG&E.",
            workspace,
            storage,
            evidence_tier=EvidenceTier.ENDORSED,
            source_url="https://example.com/notes",
        )
        assert storage.list_people() == people_before  # no "(company)" anchor
        [entry] = storage.list_subject_log_entries(report.subject)
        assert entry.evidence_tier is EvidenceTier.ENDORSED
        assert entry.subject.subject_id == "pg&e" and entry.subject.label == "PG&E"
        evidence = (workspace.data_dir / report.source_path).read_text(encoding="utf-8")
        assert "Waiting on PG&E." in evidence and "Evidence: endorsed" in evidence
        assert "[endorsed]" in render_timeline(report.subject, timeline(report.subject, storage))


def test_no_new_kind_ever_appears_in_person_listings(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_subject(SubjectKind.INITIATIVE, "Career Circles class", storage)
        add_subject(SubjectKind.CONTENT, "AI in career transitions", storage)
        log_for_subject(SubjectKind.COMPANY, "Sage", "Interview booked.", workspace, storage)
        assert storage.list_people() == []


def test_an_opportunity_logs_into_its_journal_not_a_second_history(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        opportunity = _opportunity(storage)
        ref = opportunity.opportunity_id[:8]
        report = log_for_subject(
            SubjectKind.OPPORTUNITY, ref, "Recruiter said two weeks.", workspace, storage
        )
        [event] = storage.list_opportunity_events(opportunity.opportunity_id)
        assert event.event_id == report.entry_id and event.note == "Recruiter said two weeks."
        assert current_stage([event]) is OpportunityStatus.ASSESSED
        assert storage.list_subject_log_entries() == []
        with pytest.raises(IngestError, match="own words only"):
            log_for_subject(
                SubjectKind.OPPORTUNITY,
                ref,
                "A summary",
                workspace,
                storage,
                evidence_tier=EvidenceTier.ENDORSED,
            )


def test_content_targets_read_back_from_both_sides(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Mike Manoske", storage)
        add_subject(SubjectKind.CONTENT, "AI in career transitions", storage)
        piece, target, added = aim_content(
            "AI in career transitions", "person:Mike Manoske", storage
        )
        assert added is True
        _, _, again = aim_content("AI in career transitions", "company:Career Circles", storage)
        assert again is True
        assert aim_content("AI in career transitions", "person:Mike Manoske", storage)[2] is False

        assert [t.label for t in storage.list_content_targets(piece.subject_id)] == [
            "Mike Manoske",
            "Career Circles",
        ]
        assert [p.label for p in content_aimed_at(target, storage)] == ["AI in career transitions"]
        shown = render_subject(target, storage)
        assert "Content aimed here: AI in career transitions" in shown

        with pytest.raises(IngestError, match="person or a company"):
            add_subject(SubjectKind.INITIATIVE, "Practice", storage)
            aim_content("AI in career transitions", "initiative:Practice", storage)

        assert unaim_content("AI in career transitions", "person:Mike Manoske", storage)[2]
        assert content_aimed_at(target, storage) == []


def test_objectives_work_for_every_kind_and_replace_in_place(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Rana Kanaan", storage)
        add_subject(SubjectKind.INITIATIVE, "Career Circles class", storage)
        for kind, name in (
            (SubjectKind.COMPANY, "UC Berkeley"),
            (SubjectKind.INITIATIVE, "Career Circles class"),
            (SubjectKind.PERSON, "Rana Kanaan"),
        ):
            subject = resolve_subject(kind, name, storage)
            assert load_subject_objective(subject, storage) is None
            save_subject_objective(subject, "goal one", "thesis", "move", storage)
            save_subject_objective(subject, "goal two", "thesis", "move", storage)
            view = load_subject_objective(subject, storage)
            assert view is not None and view.goal == "goal two"
        # A person's objective is still the RFC-037 record.
        person = storage.list_people()[0]
        stored = storage.get_objective(person.person_id)
        assert stored is not None and stored.goal == "goal two"
        with pytest.raises(IngestError, match="all required"):
            save_subject_objective(
                resolve_subject(SubjectKind.COMPANY, "UC Berkeley", storage), "", "t", "m", storage
            )


def test_merging_a_person_carries_their_links_and_targets(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        keep, _ = add_person("Mike Manoske", storage)
        absorb, _ = add_person("Mike Manoske PCC", storage)
        add_subject(SubjectKind.CONTENT, "AI in career transitions", storage)
        aim_content("AI in career transitions", "person:Mike Manoske PCC", storage)
        log_for_subject(
            SubjectKind.COMPANY,
            "Career Circles",
            "Pitched the class.",
            workspace,
            storage,
            links=["person:Mike Manoske PCC"],
        )
        storage.merge_person(keep.person_id, absorb.person_id)
        kept = resolve_subject(SubjectKind.PERSON, "Mike Manoske", storage)
        assert [entry.text for entry in timeline(kept, storage)] == ["Pitched the class."]
        assert [p.label for p in content_aimed_at(kept, storage)] == ["AI in career transitions"]

        storage.delete_person(keep.person_id)
        assert storage.list_entries_linked_to(kept) == []
        assert storage.list_content_aimed_at(kept) == []


def test_mcp_tools_take_a_subject_kind_and_links(workspace: Config) -> None:
    from wingman.mcp_server import relationship_log, relationship_objective, subjects

    with Storage(workspace.db_path) as storage:
        add_person("Trent Lowe", storage)

    assert subjects("add", kind="initiative", name="AI-leadership practice") == (
        "Added initiative: AI-leadership practice"
    )
    assert "already exists" in subjects("add", kind="initiative", name="ai-leadership practice")
    assert "only initiative and content" in subjects("add", kind="company", name="Sage")

    logged = relationship_log(
        "Trent Lowe", TRENT_NOTE, links=["company:Sage", "initiative:AI-leadership practice"]
    )
    assert logged.startswith(f"Logged for Trent Lowe: {TRENT_NOTE}")
    assert "Also in the timeline of: company Sage, initiative AI-leadership practice" in logged

    company = relationship_log("Sage", "Thank-you note sent.", subject_kind="company")
    assert company.startswith("Logged for company Sage: Thank-you note sent.")

    sage = relationship_log("Sage", action="list", subject_kind="company")
    assert TRENT_NOTE in sage and "Thank-you note sent." in sage

    person_list = relationship_log("Trent Lowe", action="list")
    assert person_list.startswith("Interaction log — Trent Lowe")

    shown = subjects("show", kind="initiative", name="AI-leadership practice")
    assert "No objective set yet." in shown and TRENT_NOTE in shown

    saved = relationship_objective(
        "save",
        "Sage",
        goal="Land the role",
        thesis="Trent's intro carries weight",
        next_move="Send the follow-up",
        subject_kind="company",
    )
    assert "Goal: Land the role" in saved
    assert "Goal: Land the role" in relationship_objective("show", "Sage", subject_kind="company")
    assert relationship_log("Sage", "x", subject_kind="planet").startswith(
        "relationship log add failed: unknown subject kind"
    )
