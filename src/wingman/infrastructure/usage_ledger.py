"""Lightweight ledger I/O: bounded writes, genuinely read-only reports."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from wingman.domain.model_usage import ModelUsage

WRITE_TIMEOUT_SECONDS = 0.25
_SCHEMA = """CREATE TABLE IF NOT EXISTS model_usage (
usage_id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, capability TEXT NOT NULL,
provider TEXT NOT NULL, model TEXT NOT NULL, payer TEXT NOT NULL,
input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
cache_write_tokens INTEGER, search_result_count INTEGER, latency_ms INTEGER NOT NULL,
caller_name TEXT)"""


def record_usage(path: Path, usage: ModelUsage) -> None:
    """Initialize only the ledger, never run general workspace migrations."""
    values = usage.model_dump(mode="json")
    columns = list(values)
    with closing(sqlite3.connect(path, timeout=WRITE_TIMEOUT_SECONDS)) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute(
            f"INSERT INTO model_usage ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(values.values()),
        )


def read_usage(path: Path, limit: int) -> tuple[list[ModelUsage], int, bool]:
    """Rows, full count, and table presence, without creating or migrating a DB."""
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)
    ) as conn:
        conn.row_factory = sqlite3.Row
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_usage'"
            ).fetchone()
            is None
        ):
            return [], 0, False
        # A single read transaction keeps the count and selected rows consistent.
        conn.execute("BEGIN")
        count = int(conn.execute("SELECT count(*) FROM model_usage").fetchone()[0])
        rows = conn.execute(
            "SELECT * FROM model_usage ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [ModelUsage.model_validate(dict(row)) for row in rows], count, True
