"""Lightweight ledger I/O: bounded writes, genuinely read-only reports."""

from __future__ import annotations

import atexit
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import NamedTuple

from wingman.domain.model_usage import ModelUsage, Payer
from wingman.infrastructure.logs import get_logger

WRITE_TIMEOUT_SECONDS = 0.25
EXIT_FLUSH_TIMEOUT_SECONDS = 2.0
MAX_PENDING_ROWS = 1000

# The single definition of the ledger's shape. Storage's schema and this
# module's write path both use it, so the table never depends on which
# path happened to create it (#557 review).
MODEL_USAGE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS model_usage (
    usage_id TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    capability TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    payer TEXT NOT NULL CHECK (payer IN ('byok', 'funded', 'ambient', 'offline')),
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    search_result_count INTEGER,
    latency_ms INTEGER NOT NULL,
    caller_name TEXT
)"""
MODEL_USAGE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_model_usage_recorded_at ON model_usage (recorded_at)"
)
MODEL_USAGE_SCHEMA = f"{MODEL_USAGE_TABLE_SQL};\n{MODEL_USAGE_INDEX_SQL};\n"

_logger = get_logger("model_usage")

# Rows whose write hit a lock, kept in memory and retried (#557 review). The
# common cause is THIS process: a Storage connection with an uncommitted
# write or an unfinished read cursor when a model call returns. Waiting
# longer cannot help in that case — the lock holder is our own caller — so
# the row waits here instead and is written by the next recording, when a
# Storage closes, or at interpreter exit. A crash still loses pending rows,
# which is why reports call their totals a lower bound.
_pending: dict[Path, list[ModelUsage]] = {}
_pending_lock = threading.Lock()


def _is_lock_error(exc: sqlite3.OperationalError) -> bool:
    name = getattr(exc, "sqlite_errorname", "")
    return name in {"SQLITE_BUSY", "SQLITE_LOCKED"} or "locked" in str(exc).lower()


def _insert(path: Path, rows: list[ModelUsage], timeout: float) -> None:
    with closing(sqlite3.connect(path, timeout=timeout)) as conn, conn:
        conn.execute(MODEL_USAGE_TABLE_SQL)
        conn.execute(MODEL_USAGE_INDEX_SQL)
        for usage in rows:
            values = usage.model_dump(mode="json")
            columns = list(values)
            conn.execute(
                f"INSERT OR IGNORE INTO model_usage ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(values.values()),
            )


def record_usage(path: Path, usage: ModelUsage) -> bool:
    """Write one row (plus any earlier deferred rows for this ledger).

    Initializes only the ledger, never runs general workspace migrations.
    Returns False when the write was deferred because the database was
    locked; other failures raise and the caller logs them.
    """
    key = path.resolve()
    with _pending_lock:
        batch = [*_pending.pop(key, []), usage]
        try:
            _insert(path, batch, WRITE_TIMEOUT_SECONDS)
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                if batch[:-1]:
                    _pending[key] = batch[:-1]
                raise
            dropped = len(batch) - MAX_PENDING_ROWS
            if dropped > 0:
                _logger.error(
                    "model usage: %d deferred row(s) dropped (queue full); totals are a lower bound",
                    dropped,
                )
                batch = batch[dropped:]
            _pending[key] = batch
            return False
    return True


def flush_pending_usage(path: Path | None = None, *, at_exit: bool = False) -> int:
    """Retry deferred rows; returns how many are still pending afterwards.

    At exit, rows that still cannot be written are logged as lost rather
    than disappearing silently.
    """
    timeout = EXIT_FLUSH_TIMEOUT_SECONDS if at_exit else WRITE_TIMEOUT_SECONDS
    with _pending_lock:
        keys = [path.resolve()] if path is not None else list(_pending)
        for key in keys:
            batch = _pending.pop(key, [])
            if not batch:
                continue
            try:
                _insert(key, batch, timeout)
            except sqlite3.Error as exc:
                if at_exit:
                    _logger.error(
                        "model usage: %d deferred row(s) lost for %s (%s); totals are a lower bound",
                        len(batch),
                        key,
                        exc,
                    )
                else:
                    _pending[key] = batch
        return sum(len(batch) for batch in _pending.values())


atexit.register(flush_pending_usage, at_exit=True)


class LedgerRead(NamedTuple):
    """Newest billable rows, the full billable count, and offline calls apart."""

    rows: list[ModelUsage]
    total: int
    present: bool
    offline_count: int = 0


def read_usage(path: Path, limit: int) -> LedgerRead:
    """Billable rows and counts, without creating or migrating a DB.

    The newest-N window applies to billable rows only: offline calls are
    counted separately, so a burst of recorded/hashed calls can never push
    a paid call out of the report (owner decision 2026-10-16, option b).
    """
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
            return LedgerRead([], 0, False)
        # A single read transaction keeps the counts and selected rows consistent.
        conn.execute("BEGIN")
        offline = Payer.OFFLINE.value
        count = int(
            conn.execute(
                "SELECT count(*) FROM model_usage WHERE payer != ?", (offline,)
            ).fetchone()[0]
        )
        offline_count = int(
            conn.execute("SELECT count(*) FROM model_usage WHERE payer = ?", (offline,)).fetchone()[
                0
            ]
        )
        rows = conn.execute(
            "SELECT * FROM model_usage WHERE payer != ? "
            "ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
            (offline, max(1, limit)),
        ).fetchall()
        conn.execute("COMMIT")
        return LedgerRead(
            [ModelUsage.model_validate(dict(row)) for row in rows], count, True, offline_count
        )
