"""Who a tenant workspace belongs to, stored inside the workspace itself.

A tenant's identity lives in the operator's registry and in the path of its
data directory, and nowhere in the database. A copied `wingman.db` therefore
cannot say whose it is, which is what a cross-workspace rollup of spend or
usage (#554) has to know before it can attribute anything.

One row, written by the shared process from the registry entry, holds the
tenant slug and an optional contact email. It carries no secret and is never
written per record: the identity is a label on the workspace, not a column on
every row, so stamping it does not make pooling two workspaces' content any
easier (docs/MULTI-INSTANCE-DESIGN.md keeps that a non-goal).

The registry is the source of truth. The row is a projection of it, refreshed
whenever the registry is loaded, so editing an entry's email and signalling
the shared process converges the database on the next reload.

Stamping never breaks the caller: every failure degrades to a log line, the
same discipline as infrastructure.telemetry.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from wingman.infrastructure.logs import get_logger

_logger = get_logger("infrastructure.workspace_identity")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    slug TEXT NOT NULL,
    email TEXT,
    stamped_at TEXT NOT NULL
);
"""


class WorkspaceIdentity(NamedTuple):
    slug: str
    email: str | None
    stamped_at: str


def read_identity(db_path: Path) -> WorkspaceIdentity | None:
    """The stamped identity, or None when the database or row does not exist.

    Read-only: it never creates a database file or the table.
    """
    if not db_path.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(
            "SELECT slug, email, stamped_at FROM workspace_identity WHERE singleton = 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    return WorkspaceIdentity(slug=row[0], email=row[1], stamped_at=row[2])


def stamp_identity(db_path: Path, slug: str, email: str | None) -> bool:
    """Record this workspace's slug and email; True only when a write happened.

    Does nothing when the database does not exist yet: a workspace that has
    never been initialised has no schema, and writing a bare file here would
    leave a database that later looks populated (the same trap
    infrastructure.broadcast guards against). An unchanged identity is not
    rewritten, so a reload does not churn the file's modification time.
    """
    try:
        if not db_path.exists():
            return False
        connection = sqlite3.connect(db_path)
        try:
            connection.executescript(_SCHEMA)
            current = connection.execute(
                "SELECT slug, email FROM workspace_identity WHERE singleton = 1"
            ).fetchone()
            if current is not None and tuple(current) == (slug, email):
                return False
            connection.execute(
                "INSERT INTO workspace_identity (singleton, slug, email, stamped_at)"
                " VALUES (1, ?, ?, ?)"
                " ON CONFLICT (singleton) DO UPDATE SET"
                " slug = excluded.slug, email = excluded.email, stamped_at = excluded.stamped_at",
                (slug, email, datetime.now(UTC).isoformat()),
            )
            connection.commit()
        finally:
            connection.close()
        return True
    except Exception as exc:  # noqa: BLE001 — stamping must never break the caller
        _logger.warning("workspace identity stamp failed for %r: %s", slug, exc)
        return False
