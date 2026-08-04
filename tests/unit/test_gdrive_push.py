"""RFC-053 (#205): push_backup/push_digest — authorization is the explicit
opt-in (skip before authorized), and a push failure at any layer is caught
and reported, never raised — the local file is never affected."""

from __future__ import annotations

from pathlib import Path

import pytest

from wingman.application.gdrive_push import push_backup, push_digest
from wingman.infrastructure import gdrive, gdrive_auth
from wingman.infrastructure.gdrive import GDriveApiError
from wingman.infrastructure.gdrive_auth import GDriveAuthError


def _authorize(home: Path) -> None:
    """Write a credentials file directly — the device flow itself is
    covered by test_gdrive_auth.py; this test module only needs
    'already authorized' as a starting state."""
    path = gdrive_auth.credentials_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"refresh_token": "rt-1", "scope": "drive.file"}', encoding="utf-8")
    path.chmod(0o600)


def test_push_backup_skips_when_not_authorized(tmp_path: Path) -> None:
    archive = tmp_path / "wingman-backup-x.tar.gz"
    archive.write_bytes(b"tarball")

    result = push_backup(archive, home=tmp_path / "home")

    assert result.status == "skipped"
    assert "not authorized" in result.detail
    assert result.file_id is None


def test_push_digest_skips_when_not_authorized(tmp_path: Path) -> None:
    digest = tmp_path / "overnight-x.md"
    digest.write_text("# digest", encoding="utf-8")

    result = push_digest(digest, home=tmp_path / "home")

    assert result.status == "skipped"
    assert "not authorized" in result.detail


def test_push_backup_succeeds_when_authorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _authorize(home)
    archive = tmp_path / "wingman-backup-x.tar.gz"
    archive.write_bytes(b"tarball bytes")

    monkeypatch.setattr(gdrive_auth, "access_token", lambda **kwargs: "at-1")

    folder_calls: list[tuple[str, str | None]] = []

    def fake_ensure_folder(
        token: str, name: str, parent_id: str | None = None, http: object = None
    ) -> str:
        folder_calls.append((name, parent_id))
        return f"id-{name}"

    def fake_upload_file(
        token: str,
        local_path: Path,
        folder_id: str,
        filename: str,
        mime_type: str,
        http: object = None,
    ) -> str:
        assert token == "at-1"
        assert folder_id == "id-backups"
        assert filename == "wingman-backup-x.tar.gz"
        assert mime_type == "application/gzip"
        return "file-999"

    monkeypatch.setattr(gdrive, "ensure_folder", fake_ensure_folder)
    monkeypatch.setattr(gdrive, "upload_file", fake_upload_file)

    result = push_backup(archive, home=home)

    assert result.status == "pushed"
    assert result.file_id == "file-999"
    assert folder_calls == [("Wingman", None), ("backups", "id-Wingman")]
    assert "Wingman/backups/wingman-backup-x.tar.gz" in result.detail


def test_push_digest_succeeds_when_authorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _authorize(home)
    digest = tmp_path / "overnight-x.md"
    digest.write_text("# digest", encoding="utf-8")

    monkeypatch.setattr(gdrive_auth, "access_token", lambda **kwargs: "at-1")

    folder_calls: list[tuple[str, str | None]] = []

    def fake_ensure_folder(
        token: str, name: str, parent_id: str | None = None, http: object = None
    ) -> str:
        folder_calls.append((name, parent_id))
        return f"id-{name}"

    def fake_upload_file(
        token: str,
        local_path: Path,
        folder_id: str,
        filename: str,
        mime_type: str,
        http: object = None,
    ) -> str:
        assert mime_type == "text/markdown"
        return "file-777"

    monkeypatch.setattr(gdrive, "ensure_folder", fake_ensure_folder)
    monkeypatch.setattr(gdrive, "upload_file", fake_upload_file)

    result = push_digest(digest, home=home)

    assert result.status == "pushed"
    assert folder_calls == [
        ("Wingman", None),
        ("reports", "id-Wingman"),
        ("digests", "id-reports"),
    ]
    assert "Wingman/reports/digests/overnight-x.md" in result.detail


def test_push_backup_fails_gracefully_on_expired_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _authorize(home)
    archive = tmp_path / "wingman-backup-x.tar.gz"
    archive.write_bytes(b"tarball bytes")

    def boom(**kwargs: object) -> str:
        raise GDriveAuthError("refresh token expired")

    monkeypatch.setattr(gdrive_auth, "access_token", boom)

    result = push_backup(archive, home=home)

    assert result.status == "failed"
    assert "refresh token expired" in result.detail
    # the local backup file is completely untouched
    assert archive.read_bytes() == b"tarball bytes"


def test_push_digest_fails_gracefully_on_drive_api_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _authorize(home)
    digest = tmp_path / "overnight-x.md"
    digest.write_text("# digest", encoding="utf-8")

    monkeypatch.setattr(gdrive_auth, "access_token", lambda **kwargs: "at-1")

    def fake_ensure_folder(
        token: str, name: str, parent_id: str | None = None, http: object = None
    ) -> str:
        return f"id-{name}"

    def boom_upload(
        token: str,
        local_path: Path,
        folder_id: str,
        filename: str,
        mime_type: str,
        http: object = None,
    ) -> str:
        raise GDriveApiError("Drive API returned 500")

    monkeypatch.setattr(gdrive, "ensure_folder", fake_ensure_folder)
    monkeypatch.setattr(gdrive, "upload_file", boom_upload)

    result = push_digest(digest, home=home)

    assert result.status == "failed"
    assert "Drive API returned 500" in result.detail
    assert digest.read_text(encoding="utf-8") == "# digest"


def test_push_never_raises_even_on_unexpected_folder_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one invariant #205 is explicit about: a Drive push failure must
    never break the underlying local write it describes."""
    home = tmp_path / "home"
    _authorize(home)
    archive = tmp_path / "wingman-backup-x.tar.gz"
    archive.write_bytes(b"tarball bytes")

    monkeypatch.setattr(gdrive_auth, "access_token", lambda **kwargs: "at-1")

    def boom_ensure_folder(
        token: str, name: str, parent_id: str | None = None, http: object = None
    ) -> str:
        raise GDriveApiError("network error mid-folder-lookup")

    monkeypatch.setattr(gdrive, "ensure_folder", boom_ensure_folder)

    # Must not raise.
    result = push_backup(archive, home=home)
    assert result.status == "failed"
