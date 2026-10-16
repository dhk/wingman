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
#
# `_pending_lock` guards only the two dicts below and is never held across
# database I/O: one tenant's locked ledger must not delay another tenant's
# paid call. At most one thread writes a given ledger at a time (`_in_flight`);
# others append to its queue and return, and the writer drains what arrived.
_pending: dict[Path, list[ModelUsage]] = {}
_in_flight: set[Path] = set()
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


def _cap_locked(key: Path) -> None:
    """Bound one ledger's queue, oldest rows first; caller holds `_pending_lock`."""
    queue = _pending.get(key, [])
    dropped = len(queue) - MAX_PENDING_ROWS
    if dropped > 0:
        _logger.error(
            "model usage: %d deferred row(s) dropped (queue full); totals are a lower bound",
            dropped,
        )
        _pending[key] = queue[dropped:]


def _drain(key: Path, timeout: float) -> None:
    """Write `key`'s queue until it is empty; the caller owns `key` in `_in_flight`.

    Any failure puts the unwritten batch back ahead of rows queued meanwhile,
    releases ownership, and re-raises, so no deferred row is ever discarded
    by a failed retry.
    """
    while True:
        with _pending_lock:
            batch = _pending.pop(key, [])
            if not batch:
                _in_flight.discard(key)
                return
        try:
            _insert(key, batch, timeout)
        except BaseException:
            with _pending_lock:
                _pending[key] = [*batch, *_pending.get(key, [])]
                _cap_locked(key)
                _in_flight.discard(key)
            raise


def record_usage(path: Path, usage: ModelUsage) -> bool:
    """Write one row (plus any earlier deferred rows for this ledger).

    Initializes only the ledger, never runs general workspace migrations.
    Returns False when the write was deferred because the database was
    locked. Other failures raise and the caller logs them; the row stays
    queued for a later retry either way. When another thread is already
    writing this ledger, the row joins its queue and is written by that
    thread (True: nothing went wrong here).
    """
    key = path.resolve()
    with _pending_lock:
        _pending.setdefault(key, []).append(usage)
        _cap_locked(key)
        if key in _in_flight:
            return True
        _in_flight.add(key)
    try:
        _drain(key, WRITE_TIMEOUT_SECONDS)
    except sqlite3.OperationalError as exc:
        if _is_lock_error(exc):
            return False
        raise
    return True


def flush_pending_usage(path: Path | None = None, *, at_exit: bool = False) -> int:
    """Retry deferred rows; returns how many are still pending afterwards.

    At exit, rows that still cannot be written are logged as lost rather
    than disappearing silently. A ledger another thread is writing is left
    to that thread.
    """
    timeout = EXIT_FLUSH_TIMEOUT_SECONDS if at_exit else WRITE_TIMEOUT_SECONDS
    with _pending_lock:
        keys = [path.resolve()] if path is not None else list(_pending)
    for key in keys:
        with _pending_lock:
            if key in _in_flight or not _pending.get(key):
                continue
            _in_flight.add(key)
        try:
            _drain(key, timeout)
        except Exception as exc:  # noqa: BLE001 — a flush must never raise into Storage.close
            if at_exit:
                with _pending_lock:
                    lost = _pending.pop(key, [])
                _logger.error(
                    "model usage: %d deferred row(s) lost for %s (%s); totals are a lower bound",
                    len(lost),
                    key,
                    type(exc).__name__,
                )
    with _pending_lock:
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
