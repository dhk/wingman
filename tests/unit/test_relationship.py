"""Relationship objectives (RFC-037): goal/thesis/next-move per person, interview protocol."""

from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.people import add_person
from wingman.application.relationship import (
    INTERVIEW_AREAS,
    LOG_SOURCE_TYPE,
    list_log,
    load_objective,
    log_interaction,
    render_interview,
    render_log,
    render_objective,
    render_relationship_context,
    save_objective,
)
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage

GOAL = "Build a peer relationship with a strong platform engineer."
THESIS = "He respects direct technical exchange more than networking small talk."
NEXT_MOVE = "Reply to his post about eval harnesses with a real technical take."


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    config.inbox_dir.mkdir(parents=True)
    return config


def test_save_and_load_roundtrip(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Brandon Galang", storage)
        who, objective = load_objective("Brandon Galang", storage)
        assert objective is None
        saved = save_objective("Brandon Galang", GOAL, THESIS, NEXT_MOVE, storage)
        assert saved.goal == GOAL and saved.thesis == THESIS and saved.next_move == NEXT_MOVE
        who, reloaded = load_objective("brandon galang", storage)  # case/whitespace-insensitive
        assert reloaded is not None
        assert reloaded.objective_id == saved.objective_id
        rendered = render_objective(who, reloaded)
        assert "Brandon Galang" in rendered and GOAL in rendered and NEXT_MOVE in rendered


def test_save_revises_in_place_not_versioned(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Ishana Gupta", storage)
        first = save_objective("Ishana Gupta", GOAL, THESIS, NEXT_MOVE, storage)
        second = save_objective(
            "Ishana Gupta", GOAL, THESIS, "Follow up after the Cursor thread resolves.", storage
        )
        assert second.objective_id == first.objective_id  # same record, revised
        assert second.created_at == first.created_at
        assert second.next_move != first.next_move
        assert len(storage.list_objectives()) == 1


def test_save_requires_all_three_fields(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Jane Doe", storage)
        with pytest.raises(IngestError, match="required"):
            save_objective("Jane Doe", "", THESIS, NEXT_MOVE, storage)
        with pytest.raises(IngestError, match="required"):
            save_objective("Jane Doe", GOAL, "  ", NEXT_MOVE, storage)


def test_save_requires_known_unambiguous_person(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="no person matching"):
            save_objective("Nobody Here", GOAL, THESIS, NEXT_MOVE, storage)
        add_person("Jane Doe", storage)
        add_person("Jane Smith", storage)
        with pytest.raises(IngestError, match="multiple people"):
            save_objective("Jane", GOAL, THESIS, NEXT_MOVE, storage)


def test_render_interview_seeding_then_revision(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Brandon Galang", storage)
        seeding = render_interview("Brandon Galang", storage)
        assert "SEEDING" in seeding
        for area, _ in INTERVIEW_AREAS:
            assert area in seeding
        save_objective("Brandon Galang", GOAL, THESIS, NEXT_MOVE, storage)
        review = render_interview("Brandon Galang", storage)
        assert "REVISE" in review
        assert GOAL in review and THESIS in review and NEXT_MOVE in review


def test_delete_person_cascades_objective(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Brandon Galang", storage)
        save_objective("Brandon Galang", GOAL, THESIS, NEXT_MOVE, storage)
        assert storage.get_objective(person.person_id) is not None
        storage.delete_person(person.person_id)
        assert storage.get_objective(person.person_id) is None


def test_merge_person_moves_objective(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        keep, _ = add_person("Brandon G", storage)
        absorb, _ = add_person("Brandon Galang", storage)
        save_objective("Brandon Galang", GOAL, THESIS, NEXT_MOVE, storage)
        storage.merge_person(keep.person_id, absorb.person_id)
        merged_objective = storage.get_objective(keep.person_id)
        assert merged_objective is not None and merged_objective.goal == GOAL


def test_mcp_relationship_objective_tool(workspace: Config) -> None:
    from wingman.mcp_server import relationship_objective

    with Storage(workspace.db_path) as storage:
        add_person("Brandon Galang", storage)
    absent = relationship_objective(action="show", person="Brandon Galang")
    assert "No relationship objective" in absent
    packet = relationship_objective(action="review", person="Brandon Galang")
    assert "SEEDING" in packet
    saved = relationship_objective(
        action="save", person="Brandon Galang", goal=GOAL, thesis=THESIS, next_move=NEXT_MOVE
    )
    assert "Saved" in saved and "Brandon Galang" in saved
    shown = relationship_objective(action="show", person="Brandon Galang")
    assert GOAL in shown and NEXT_MOVE in shown
    # protocol rides the docstring (RFC-025/030/031/035/036 convention)
    assert "AskUserQuestion" in (relationship_objective.__doc__ or "")
    assert "never save wording they have not seen" in (relationship_objective.__doc__ or "")
    assert "unknown action" in relationship_objective(action="bogus", person="Brandon Galang")


LOG_NOTE = "Coffee with Brandon, discussed the eval harness role — he's interviewing in Sept."


def test_log_interaction_creates_source_file_and_entry(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Brandon Galang", storage)
        report = log_interaction("Brandon Galang", LOG_NOTE, workspace, storage)
        assert report.person == "Brandon Galang"
        assert report.entry.note == LOG_NOTE
        record = storage.get_source_record(report.entry.source_record_id)
        assert record is not None and record.source_type == LOG_SOURCE_TYPE
        source = workspace.data_dir / record.source_locator
        assert LOG_NOTE in source.read_text(encoding="utf-8")  # the evidence quote, verbatim
        entries = storage.list_log_entries(person.person_id)
        assert len(entries) == 1 and entries[0].note == LOG_NOTE


def test_log_interaction_requires_note_and_known_person(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Brandon Galang", storage)
        with pytest.raises(IngestError, match="empty"):
            log_interaction("Brandon Galang", "   ", workspace, storage)
        with pytest.raises(IngestError, match="no person matching"):
            log_interaction("Nobody Here", LOG_NOTE, workspace, storage)


def test_list_log_and_render(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Brandon Galang", storage)
        who, empty = list_log("Brandon Galang", storage)
        assert empty == []
        assert "No interactions logged" in render_log(who, empty)
        log_interaction("Brandon Galang", LOG_NOTE, workspace, storage)
        log_interaction("Brandon Galang", "Second chat, follow-up.", workspace, storage)
        who, entries = list_log("Brandon Galang", storage)
        assert len(entries) == 2
        rendered = render_log(who, entries)
        assert LOG_NOTE in rendered and "Second chat" in rendered
        assert "2 entries" in rendered


def test_render_relationship_context_cites_objective_and_recent_log(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Brandon Galang", storage)
        assert render_relationship_context(person, storage) == ""  # nothing yet: no footer
        save_objective("Brandon Galang", GOAL, THESIS, NEXT_MOVE, storage)
        log_interaction("Brandon Galang", LOG_NOTE, workspace, storage)
        context = render_relationship_context(person, storage)
        assert GOAL in context and THESIS in context and NEXT_MOVE in context
        assert LOG_NOTE in context
        assert "cite this, don't invent" in context


def test_delete_person_cascades_log(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Brandon Galang", storage)
        log_interaction("Brandon Galang", LOG_NOTE, workspace, storage)
        storage.delete_person(person.person_id)
        assert storage.list_log_entries(person.person_id) == []


def test_mcp_relationship_log_tool(workspace: Config) -> None:
    from wingman.mcp_server import relationship_log

    with Storage(workspace.db_path) as storage:
        add_person("Brandon Galang", storage)
    added = relationship_log(person="Brandon Galang", note=LOG_NOTE, action="add")
    assert "Logged for Brandon Galang" in added and LOG_NOTE in added
    listed = relationship_log(person="Brandon Galang", action="list")
    assert LOG_NOTE in listed
    assert "unknown action" in relationship_log(person="Brandon Galang", action="bogus")
    # protocol rides the docstring (qa_capture convention)
    assert "OFFER to log it" in (relationship_log.__doc__ or "")
    assert "never paraphrase without confirmation" in (relationship_log.__doc__ or "")


def test_people_brief_footer_cites_relationship_context(workspace: Config) -> None:
    from datetime import UTC, datetime

    from wingman.domain.outreach import OutreachBrief
    from wingman.mcp_server import people_brief

    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Brandon Galang", storage)
        save_objective("Brandon Galang", GOAL, THESIS, NEXT_MOVE, storage)
        log_interaction("Brandon Galang", LOG_NOTE, workspace, storage)
        storage.save_outreach_brief(
            OutreachBrief(
                person_id=person.person_id,
                person_name=person.name,
                corpus_documents_used=0,
                pov_generated_at=datetime.now(UTC),
                provider="recorded",
                model="test",
                prompt_version="v1",
            )
        )
    result = people_brief(name="Brandon Galang")
    assert "stored brief" in result
    assert GOAL in result and THESIS in result and LOG_NOTE in result
    assert "cite this, don't invent" in result
