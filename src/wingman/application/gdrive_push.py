"""Push closed, fully-written artifacts to Google Drive — never the live
workspace (RFC-053, #205, the first slice of #151). Only two things ever
reach this module: a completed `wingman backup` tarball, and the digest
file `wingman overnight` writes. Everything else (export_pdf,
company_dossier, pack, ...) is explicit follow-up scope, not this slice.

Authorization itself is the explicit opt-in (resolving #205's
explicit-vs-automatic-egress question): before `wingman drive auth` has
produced a refresh token, every call here is a silent, visible no-op
("skipped"). Once authorized, `wingman backup` and the digest `wingman
overnight` writes push automatically — the one, deliberate `drive_auth`
action is what makes the ongoing pushes no longer need their own separate
confirmation, the same way `wingman keys set anthropic` makes every later
model call able to use that key without asking again.

A Drive push failing for any reason (expired token, network error, Drive
API error) must never break the underlying local write — this module never
raises. It logs and returns a "failed" result, exactly like the pipeline's
existing pov/brief skip pattern (`application/pipeline.py`'s `_step` calls
with status "skipped").
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from wingman.infrastructure import gdrive, gdrive_auth
from wingman.infrastructure.logs import get_logger

_logger = get_logger("application.gdrive_push")

# Wingman/backups/..., Wingman/reports/digests/... — the folder layout
# resolved in #205's design.
ROOT_FOLDER = "Wingman"
BACKUPS_FOLDER = "backups"
REPORTS_FOLDER = "reports"
DIGESTS_FOLDER = "digests"


class DrivePushResult(BaseModel):
    """What one push attempt did. 'skipped' means not authorized yet
    (visible, not an error); 'failed' means authorized but the push itself
    didn't make it — either way the local file is untouched."""

    status: str  # "pushed" | "skipped" | "failed"
    detail: str
    file_id: str | None = None


def _push(
    local_path: Path, folder_parts: list[str], mime_type: str, home: Path | None
) -> DrivePushResult:
    if not gdrive_auth.is_authorized(home):
        return DrivePushResult(
            status="skipped",
            detail="Drive: not authorized yet — run 'wingman drive auth' to enable this.",
        )
    try:
        token = gdrive_auth.access_token(home=home)
        parent_id: str | None = None
        for part in folder_parts:
            parent_id = gdrive.ensure_folder(token, part, parent_id=parent_id)
        assert parent_id is not None  # folder_parts is always non-empty
        file_id = gdrive.upload_file(token, local_path, parent_id, local_path.name, mime_type)
    except Exception as exc:  # noqa: BLE001 — the whole contract is "never raises"
        # Not just the two Drive error classes. upload_file() calls stat() and
        # read_bytes() (OSError), and any unexpected adapter failure would
        # otherwise escape into backup/overnight and fail a command whose real
        # work — the backup, the digest — had already completed successfully.
        # Graceful degradation is the established pattern for this whole path.
        _logger.warning("drive push failed path=%s error=%s", local_path, exc)
        return DrivePushResult(
            status="failed",
            detail=f"Drive: push failed ({exc}); the local file is unaffected.",
        )
    destination = "/".join(folder_parts)
    _logger.info(
        "drive push ok path=%s destination=%s file_id=%s", local_path, destination, file_id
    )
    return DrivePushResult(
        status="pushed",
        detail=f"Drive: pushed to {destination}/{local_path.name}",
        file_id=file_id,
    )


def push_backup(archive_path: Path, home: Path | None = None) -> DrivePushResult:
    """Push a just-written `wingman backup` tarball to Wingman/backups/."""
    return _push(archive_path, [ROOT_FOLDER, BACKUPS_FOLDER], "application/gzip", home)


def push_digest(digest_path: Path, home: Path | None = None) -> DrivePushResult:
    """Push a just-written overnight digest (markdown) to
    Wingman/reports/digests/. The HTML twin stays local for this slice —
    pushing it too is the same follow-up scope as wiring other report
    types, not done here."""
    return _push(digest_path, [ROOT_FOLDER, REPORTS_FOLDER, DIGESTS_FOLDER], "text/markdown", home)
