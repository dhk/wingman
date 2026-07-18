"""Workspace backup and restore: dated tarballs of everything that matters.

The whole workspace is one SQLite file plus the inbox archive and reports —
and a live SQLite file does not sync safely through iCloud/Dropbox, while a
closed tarball does. `wingman backup` snapshots the database with SQLite's
online-backup API (consistent even mid-write), packs it with the inbox,
reports, and models.toml into a dated .tar.gz at a destination of your
choice, and prunes old backups past a retention count. `wingman restore`
is the inverse, and refuses to overwrite a live workspace without --force.

Local files only: nothing here touches the network.
"""

from __future__ import annotations

import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from wingman.application.ingest import IngestError
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger

_logger = get_logger("application.backup")

_ARCHIVE_PREFIX = "wingman-backup-"
# Directories under the workspace that are derived or self-referential and
# never belong inside a backup.
_EXCLUDED_DIRS = {"backups", "demo"}


class BackupReport(BaseModel):
    path: str
    size_bytes: int
    files: int
    pruned: list[str] = Field(default_factory=list)


class RestoreReport(BaseModel):
    archive: str
    files: int


def _snapshot_database(db_path: Path, target: Path) -> None:
    """A consistent copy of the SQLite database via the online-backup API."""
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def create_backup(config: Config, dest: Path | None = None, keep: int = 10) -> BackupReport:
    """Write a dated workspace tarball; prune to the newest `keep` (0 keeps all)."""
    if not config.db_path.exists():
        raise IngestError(
            "the workspace has no database — nothing to back up. Run 'wingman init' first."
        )
    destination = (dest.expanduser() if dest else config.data_dir / "backups").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = destination / f"{_ARCHIVE_PREFIX}{stamp}.tar.gz"
    counter = 1
    while archive_path.exists():  # two backups in the same second must not clobber
        counter += 1
        archive_path = destination / f"{_ARCHIVE_PREFIX}{stamp}-{counter}.tar.gz"

    files = 0
    with tempfile.TemporaryDirectory() as scratch:
        db_snapshot = Path(scratch) / "wingman.db"
        _snapshot_database(config.db_path, db_snapshot)
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(db_snapshot, arcname="wingman/wingman.db")
            files += 1
            if config.models_config_path.exists():
                archive.add(config.models_config_path, arcname="wingman/models.toml")
                files += 1
            for directory in (config.inbox_dir, config.reports_dir):
                if not directory.exists():
                    continue
                for path in sorted(directory.rglob("*")):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(config.data_dir)
                    if _EXCLUDED_DIRS.intersection(relative.parts):
                        continue
                    archive.add(path, arcname=str(Path("wingman") / relative))
                    files += 1

    pruned: list[str] = []
    if keep > 0:
        # oldest-first by mtime: the -N same-second suffix breaks name ordering
        existing = sorted(
            destination.glob(f"{_ARCHIVE_PREFIX}*.tar.gz"),
            key=lambda path: path.stat().st_mtime,
        )
        for stale in existing[:-keep]:
            stale.unlink()
            pruned.append(stale.name)

    size = archive_path.stat().st_size
    _logger.info(
        "backup path=%s files=%d bytes=%d pruned=%d", archive_path, files, size, len(pruned)
    )
    return BackupReport(path=str(archive_path), size_bytes=size, files=files, pruned=pruned)


def restore_backup(archive: Path, config: Config, force: bool = False) -> RestoreReport:
    """Restore a backup tarball into the workspace.

    Refuses to overwrite an existing database without force=True. Extraction
    uses the stdlib 'data' filter, which rejects absolute paths and
    path-traversal members outright.
    """
    archive = archive.expanduser().resolve()
    if not archive.exists():
        raise IngestError(f"backup archive {archive} does not exist. Nothing was restored.")
    if config.db_path.exists() and not force:
        raise IngestError(
            f"the workspace at {config.data_dir} already has a database. "
            "Nothing was restored; pass --force to overwrite it with the backup."
        )
    files = 0
    with tempfile.TemporaryDirectory() as scratch:
        try:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(scratch, filter="data")
        except (tarfile.TarError, OSError) as exc:
            raise IngestError(
                f"could not read {archive.name} ({exc}). Nothing was restored."
            ) from exc
        root = Path(scratch) / "wingman"
        if not (root / "wingman.db").exists():
            raise IngestError(
                f"{archive.name} does not look like a wingman backup "
                "(no wingman/wingman.db inside). Nothing was restored."
            )
        config.data_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            target = config.data_dir / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            files += 1
    _logger.info("restore archive=%s files=%d", archive, files)
    return RestoreReport(archive=str(archive), files=files)
