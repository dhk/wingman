"""The commentary corpus (#339): save a reading, attributed, reviewable, deletable."""

from pathlib import Path

import pytest

from wingman.application.commentary import (
    find_commentary,
    get_commentary,
    list_commentary,
    remove_commentary,
    render_commentary,
    render_commentary_entry,
    save_commentary,
)
from wingman.application.corpus import add_to_corpus
from wingman.application.ingest import IngestError
from wingman.domain.commentary import COMMENTARY_BANNER
from wingman.domain.profile import (
    ClaimClassification,
    EvidenceSpan,
    ProfileItem,
    ProfileItemKind,
)
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage

READING = (
    "Your three con nominations are one claim, not three: each one is about "
    "someone who knew and did nothing."
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _capture(storage: Storage) -> ProfileItem:
    """One interview-style capture to cite."""
    item = ProfileItem(
        kind=ProfileItemKind.INTERVIEW,
        subtype="values_con",
        name="values_con: a public figure",
        detail="Knew things were wrong and let them happen anyway.",
        classification=ClaimClassification.FACT,
        confidence=1.0,
        evidence=[
            EvidenceSpan(
                source_record_id="rec-1",
                quote="Knew things were wrong and let them happen anyway.",
            )
        ],
        prompt_version="none",
        extracted_by="user",
    )
    storage.add_profile_item(item)
    return item


def test_saved_reading_carries_model_prompt_version_and_date(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        entry = save_commentary(
            READING, storage, model="claude-opus-4", prompt_version="none", topic="values"
        )
        stored = storage.get_commentary_entry(entry.entry_id)
        assert stored is not None
        assert stored.text == READING  # verbatim, no tidying
        assert stored.author_model == "claude-opus-4"
        assert stored.prompt_version == "none"
        assert stored.topic == "values"
        assert stored.created_at.date().isoformat() in stored.attribution()


def test_missing_authorship_still_reads_as_a_model_never_as_the_user(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        entry = save_commentary(READING, storage)
        assert entry.author_model == "unnamed model"
        assert entry.prompt_version == "none"


def test_empty_text_is_refused(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="empty"):
            save_commentary("   ", storage)


def test_references_resolve_to_the_material_they_were_drawn_from(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        item = _capture(storage)
        essay = workspace.data_dir / "essay.md"
        essay.write_text("Denying people information denies them choice.", encoding="utf-8")
        add_to_corpus(essay, "writing", workspace, storage)
        doc_id = storage.list_corpus_documents()[0].doc_id

        entry = save_commentary(
            READING, storage, model="claude-opus-4", drawn_from=[item.item_id[:8], doc_id]
        )
        assert [reference.kind for reference in entry.drawn_from] == [
            "profile-item",
            "corpus-document",
        ]
        assert entry.drawn_from[0].ref_id == item.item_id
        assert entry.drawn_from[0].label == item.name
        rendered = render_commentary_entry(entry, storage)
        assert "drawn from [profile-item" in rendered and item.name in rendered


def test_a_reference_to_nothing_is_refused(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="nothing in this workspace has id"):
            save_commentary(READING, storage, drawn_from=["item-that-never-existed"])
        assert storage.count_commentary_entries() == 0


def test_deleted_material_is_flagged_rather_than_silently_dropped(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        item = _capture(storage)
        entry = save_commentary(READING, storage, drawn_from=[item.item_id])
        storage.delete_profile_item(item.item_id)
        rendered = render_commentary_entry(entry, storage)
        assert "no longer in the workspace" in rendered
        assert item.name in rendered  # the label was frozen at save time


def test_list_is_newest_first_and_find_matches_text_and_topic(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        first = save_commentary("An older reading about dashboards.", storage, topic="analytics")
        second = save_commentary(READING, storage, topic="values")
        assert [entry.entry_id for entry in list_commentary(storage)] == [
            second.entry_id,
            first.entry_id,
        ]
        assert [entry.entry_id for entry in find_commentary("nominations", storage)] == [
            second.entry_id
        ]
        assert [entry.entry_id for entry in find_commentary("analytics", storage)] == [
            first.entry_id
        ]
        assert find_commentary("nothing matches this", storage) == []
        with pytest.raises(IngestError, match="no searchable words"):
            find_commentary("   ", storage)


def test_show_and_remove_by_id_prefix(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        entry = save_commentary(READING, storage)
        assert get_commentary(entry.entry_id[:8], storage).entry_id == entry.entry_id
        removed = remove_commentary(entry.entry_id[:8], storage)
        assert removed.entry_id == entry.entry_id
        assert storage.count_commentary_entries() == 0
        with pytest.raises(IngestError, match="no commentary entry"):
            get_commentary("deadbeef", storage)
        with pytest.raises(IngestError, match="empty"):
            get_commentary("  ", storage)


def test_rendering_always_says_it_is_not_the_users_words(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        assert "No commentary saved yet" in render_commentary([], storage)
        save_commentary(READING, storage, model="claude-opus-4")
        rendered = render_commentary(list_commentary(storage), storage)
        assert COMMENTARY_BANNER in rendered
        assert "not your own words" in rendered
        assert "claude-opus-4" in rendered
        assert "1 entry." in rendered


def test_a_database_created_before_the_store_existed_gains_it(workspace: Config) -> None:
    """Consistent with how storage.py migrates: a NEW TABLE arrives through
    the CREATE TABLE IF NOT EXISTS schema replayed on every open, so an
    existing workspace needs no ALTER and no version stamp (only added
    COLUMNS need _migrate_*, as document_key did)."""
    with Storage(workspace.db_path) as storage:
        storage._conn.execute("DROP TABLE commentary_entries")
        storage._conn.commit()
    with Storage(workspace.db_path) as reopened:
        assert reopened.count_commentary_entries() == 0
        save_commentary(READING, reopened)
        assert reopened.count_commentary_entries() == 1
