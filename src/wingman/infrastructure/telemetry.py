"""Local usage telemetry: an opt-in journal of how Wingman is being used (RFC-023).

A conscious, explicit opt-in by the workspace owner ('wingman telemetry on'),
default off, and local by construction: events are rows in the workspace's
own SQLite database, they never leave the machine, and Wingman ships no
transmitter for them. What gets recorded when enabled: every CLI invocation
(argv, outcome, duration), every MCP tool call (arguments, result text,
outcome, duration — the conversation-adjacent trace of how a model client
drives the workspace), and anything imported by the transcript harvester.

One deliberate exception to "record everything": the values of
'wingman keys set' are credentials, not usage, and are never recorded.

Recording never breaks the command being recorded: every failure inside
this module is swallowed into a log line.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Self
from uuid import uuid4

from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger

_logger = get_logger("infrastructure.telemetry")

_MARKER = "telemetry.on"
# Per-value cap: enough to understand usage, not enough to duplicate the store.
MAX_TEXT_CHARS = 4_000

# Credentials are never usage (RFC-019/023). Scrubbed at the single write
# choke point so every capture surface — live CLI, MCP results, harvested
# transcripts (#69) — is covered without each caller remembering to.
_SECRET_PATTERNS = [
    re.compile(r"(sk-ant-[A-Za-z0-9_\-]{4,})"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"\b(pa-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"(--value[ =]+)(\S+)"),
    re.compile(r"(add-generic-password.*?-w[ =]+)(\S+)"),
    re.compile(r"((?:ANTHROPIC|VOYAGE)_API_KEY[ =:\"']+)([^\s\"']+)"),
]


def scrub_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 1:
            text = pattern.sub("[redacted]", text)
        else:
            text = pattern.sub(lambda m: m.group(1) + "[redacted]", text)
    return text


_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    surface TEXT NOT NULL,
    name TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_ms INTEGER,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry_events (ts);
"""


def is_enabled(config: Config) -> bool:
    return (config.data_dir / _MARKER).exists()


def set_enabled(config: Config, on: bool) -> None:
    marker = config.data_dir / _MARKER
    if on:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "Telemetry is ON: local usage journal in wingman.db (RFC-023). "
            "Delete this file or run 'wingman telemetry off' to stop.\n",
            encoding="utf-8",
        )
    else:
        marker.unlink(missing_ok=True)


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        value = scrub_secrets(value)
        return value if len(value) <= MAX_TEXT_CHARS else value[: MAX_TEXT_CHARS - 1] + "…"
    if isinstance(value, dict):
        return {key: _clip(entry) for key, entry in value.items()}
    if isinstance(value, list):
        return [_clip(entry) for entry in value]
    return value


def record_event(
    config: Config,
    surface: str,
    name: str,
    payload: dict[str, Any],
    outcome: str = "ok",
    duration_ms: int | None = None,
    ts: str | None = None,
) -> bool:
    """Append one event when telemetry is enabled; never raises."""
    try:
        if not is_enabled(config) or not config.db_path.exists():
            return False
        connection = sqlite3.connect(config.db_path)
        try:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT INTO telemetry_events"
                " (event_id, ts, surface, name, outcome, duration_ms, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    ts or datetime.now(UTC).isoformat(),
                    surface,
                    name,
                    outcome,
                    duration_ms,
                    json.dumps(_clip(payload), ensure_ascii=False, default=str),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return True
    except Exception as exc:  # noqa: BLE001 — telemetry must never break the command
        _logger.warning("telemetry record failed: %s", exc)
        return False


def list_events(config: Config, limit: int = 50) -> list[dict[str, Any]]:
    """Newest-first events; empty when the table doesn't exist yet."""
    if not config.db_path.exists():
        return []
    connection = sqlite3.connect(config.db_path)
    try:
        connection.executescript(_SCHEMA)
        cursor = connection.execute(
            "SELECT ts, surface, name, outcome, duration_ms, payload"
            " FROM telemetry_events ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                "ts": row[0],
                "surface": row[1],
                "name": row[2],
                "outcome": row[3],
                "duration_ms": row[4],
                "payload": json.loads(row[5]),
            }
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def iter_events(config: Config) -> Iterator[dict[str, Any]]:
    """Every event, oldest first — the export order."""
    if not config.db_path.exists():
        return
    connection = sqlite3.connect(config.db_path)
    try:
        connection.executescript(_SCHEMA)
        cursor = connection.execute(
            "SELECT ts, surface, name, outcome, duration_ms, payload"
            " FROM telemetry_events ORDER BY ts, event_id"
        )
        for row in cursor:
            yield {
                "ts": row[0],
                "surface": row[1],
                "name": row[2],
                "outcome": row[3],
                "duration_ms": row[4],
                "payload": json.loads(row[5]),
            }
    finally:
        connection.close()


def count_events(config: Config) -> int:
    if not config.db_path.exists():
        return 0
    connection = sqlite3.connect(config.db_path)
    try:
        connection.executescript(_SCHEMA)
        count: int = connection.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
        return count
    finally:
        connection.close()


def redact_argv(argv: list[str]) -> list[str]:
    """CLI argv safe to journal: 'keys set' values are credentials, never usage."""
    if len(argv) >= 2 and argv[0] == "keys" and argv[1] == "set":
        return argv[:3] + (["[value withheld]"] if len(argv) > 3 else [])
    return argv


class _Timer:
    def __enter__(self) -> Self:
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.ms = int((time.monotonic() - self._start) * 1000)


def timer() -> _Timer:
    return _Timer()
