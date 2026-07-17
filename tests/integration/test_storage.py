from datetime import UTC, datetime
from pathlib import Path

import pytest

from wingman.domain import SourceRecord
from wingman.infrastructure.storage import DuplicateRecordError, Storage


def _record() -> SourceRecord:
    return SourceRecord(
        source_type="resume",
        source_locator="inbox/resume.md",
        content_hash="abc123",
        source_timestamp=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    record = _record()
    with Storage(tmp_path / "wingman.db") as storage:
        storage.add_source_record(record)
        loaded = storage.get_source_record(record.record_id)
    assert loaded == record


def test_round_trip_without_source_timestamp(tmp_path: Path) -> None:
    record = SourceRecord(source_type="note", source_locator="inbox/note.md", content_hash="ff")
    with Storage(tmp_path / "wingman.db") as storage:
        storage.add_source_record(record)
        loaded = storage.get_source_record(record.record_id)
    assert loaded is not None
    assert loaded.source_timestamp is None


def test_duplicate_insert_is_rejected(tmp_path: Path) -> None:
    record = _record()
    with Storage(tmp_path / "wingman.db") as storage:
        storage.add_source_record(record)
        with pytest.raises(DuplicateRecordError):
            storage.add_source_record(record)
        assert storage.count_source_records() == 1


def test_records_persist_across_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "wingman.db"
    record = _record()
    with Storage(db_path) as storage:
        storage.add_source_record(record)
    with Storage(db_path) as storage:
        assert storage.count_source_records() == 1
        assert storage.get_source_record(record.record_id) == record


def test_missing_record_returns_none(tmp_path: Path) -> None:
    with Storage(tmp_path / "wingman.db") as storage:
        assert storage.get_source_record("nope") is None
