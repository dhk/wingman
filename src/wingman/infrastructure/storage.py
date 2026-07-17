"""SQLite persistence: one database file in the workspace (RFC-002)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from wingman.domain import SourceRecord
from wingman.domain.profile import ItemStatus, ProfileItem, ProfileItemKind

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_records (
    record_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_timestamp TEXT,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_items (
    item_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profile_items_key ON profile_items (kind, name_key);
"""


class DuplicateRecordError(Exception):
    """Raised when inserting a record whose ID already exists (records are immutable)."""


class Storage:
    """Persistence for Wingman records. Source records are insert-only, never updated."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def add_source_record(self, record: SourceRecord) -> None:
        try:
            self._conn.execute(
                "INSERT INTO source_records"
                " (record_id, source_type, source_locator, content_hash,"
                " source_timestamp, ingested_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.record_id,
                    record.source_type,
                    record.source_locator,
                    record.content_hash,
                    record.source_timestamp.isoformat() if record.source_timestamp else None,
                    record.ingested_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"source record {record.record_id} already exists; "
                "records are immutable — ingest a correction as a new record"
            ) from exc
        self._conn.commit()

    def get_source_record(self, record_id: str) -> SourceRecord | None:
        cursor = self._conn.execute(
            "SELECT record_id, source_type, source_locator, content_hash,"
            " source_timestamp, ingested_at FROM source_records WHERE record_id = ?",
            (record_id,),
        )
        row: tuple[str, str, str, str, str | None, str] | None = cursor.fetchone()
        if row is None:
            return None
        return SourceRecord(
            record_id=row[0],
            source_type=row[1],
            source_locator=row[2],
            content_hash=row[3],
            source_timestamp=datetime.fromisoformat(row[4]) if row[4] else None,
            ingested_at=datetime.fromisoformat(row[5]),
        )

    def get_source_record_by_hash(self, content_hash: str) -> SourceRecord | None:
        cursor = self._conn.execute(
            "SELECT record_id FROM source_records WHERE content_hash = ? ORDER BY ingested_at"
            " LIMIT 1",
            (content_hash,),
        )
        row: tuple[str] | None = cursor.fetchone()
        return self.get_source_record(row[0]) if row else None

    def count_source_records(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM source_records")
        count: int = cursor.fetchone()[0]
        return count

    def add_profile_item(self, item: ProfileItem) -> None:
        try:
            self._conn.execute(
                "INSERT INTO profile_items (item_id, kind, name_key, status, payload, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item.item_id,
                    item.kind.value,
                    item.name_key,
                    item.status.value,
                    item.model_dump_json(),
                    item.extracted_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"profile item {item.item_id} already exists; corrections create new items"
            ) from exc
        self._conn.commit()

    def find_active_item(self, kind: ProfileItemKind, name_key: str) -> ProfileItem | None:
        cursor = self._conn.execute(
            "SELECT payload FROM profile_items WHERE kind = ? AND name_key = ? AND status = ?"
            " ORDER BY created_at LIMIT 1",
            (kind.value, name_key, ItemStatus.ACTIVE.value),
        )
        row: tuple[str] | None = cursor.fetchone()
        return ProfileItem.model_validate_json(row[0]) if row else None

    def list_profile_items(self) -> list[ProfileItem]:
        cursor = self._conn.execute(
            "SELECT payload FROM profile_items ORDER BY created_at, item_id"
        )
        return [ProfileItem.model_validate_json(row[0]) for row in cursor.fetchall()]

    def count_profile_items(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM profile_items")
        count: int = cursor.fetchone()[0]
        return count
