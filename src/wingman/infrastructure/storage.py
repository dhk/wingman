"""SQLite persistence: one database file in the workspace (RFC-002)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from wingman.domain import SourceRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_records (
    record_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_timestamp TEXT,
    ingested_at TEXT NOT NULL
);
"""


class DuplicateRecordError(Exception):
    """Raised when inserting a record whose ID already exists (records are immutable)."""


class Storage:
    """Persistence for Wingman records. Source records are insert-only, never updated."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
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

    def count_source_records(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM source_records")
        count: int = cursor.fetchone()[0]
        return count
