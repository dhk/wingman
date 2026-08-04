"""RFC-053 (#205): the Drive API v3 client — folder lookup/creation and
simple multipart upload — against a scripted HTTP layer, no real network
calls."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from wingman.infrastructure.gdrive import (
    MAX_UPLOAD_BYTES,
    GDriveApiError,
    ensure_folder,
    upload_file,
)


class ScriptedHttp:
    """Returns canned (status, body_bytes) responses in call order; records
    every request it was given."""

    def __init__(self, responses: list[tuple[int, dict[str, object]]]) -> None:
        self._responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request) -> tuple[int, bytes]:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("ScriptedHttp ran out of scripted responses")
        status, body = self._responses.pop(0)
        return status, json.dumps(body).encode("utf-8")


def test_ensure_folder_reuses_an_existing_folder() -> None:
    http = ScriptedHttp([(200, {"files": [{"id": "folder-123", "name": "Wingman"}]})])

    folder_id = ensure_folder("tok", "Wingman", http=http)

    assert folder_id == "folder-123"
    assert len(http.requests) == 1  # lookup only — never created a duplicate
    assert http.requests[0].get_method() == "GET"
    assert "Bearer tok" == http.requests[0].get_header("Authorization")


def test_ensure_folder_creates_when_missing() -> None:
    http = ScriptedHttp(
        [
            (200, {"files": []}),
            (200, {"id": "folder-new"}),
        ]
    )

    folder_id = ensure_folder("tok", "backups", parent_id="parent-1", http=http)

    assert folder_id == "folder-new"
    assert len(http.requests) == 2
    create_request = http.requests[1]
    assert create_request.get_method() == "POST"
    body = json.loads(create_request.data.decode("utf-8"))
    assert body == {
        "name": "backups",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["parent-1"],
    }


def test_ensure_folder_lookup_scopes_to_root_when_no_parent() -> None:
    http = ScriptedHttp([(200, {"files": [{"id": "root-folder"}]})])

    ensure_folder("tok", "Wingman", http=http)

    query = urllib.parse.unquote(http.requests[0].full_url.split("q=", 1)[1].split("&")[0])
    assert "'root' in parents" in query


def test_ensure_folder_raises_on_lookup_failure() -> None:
    http = ScriptedHttp([(403, {"error": {"message": "insufficient permission"}})])

    with pytest.raises(GDriveApiError, match="insufficient permission"):
        ensure_folder("tok", "Wingman", http=http)


def test_ensure_folder_raises_on_creation_failure() -> None:
    http = ScriptedHttp([(200, {"files": []}), (500, {"error": {"message": "server error"}})])

    with pytest.raises(GDriveApiError, match="server error"):
        ensure_folder("tok", "Wingman", http=http)


def test_upload_file_builds_multipart_body_and_returns_file_id(tmp_path: Path) -> None:
    local = tmp_path / "wingman-backup-x.tar.gz"
    local.write_bytes(b"fake tarball bytes")
    http = ScriptedHttp([(200, {"id": "file-abc"})])

    file_id = upload_file(
        "tok", local, "folder-1", "wingman-backup-x.tar.gz", "application/gzip", http=http
    )

    assert file_id == "file-abc"
    request = http.requests[0]
    assert request.get_method() == "POST"
    assert "uploadType=multipart" in request.full_url
    content_type = request.get_header("Content-type") or request.get_header("Content-Type")
    assert content_type is not None
    assert content_type.startswith("multipart/related; boundary=")
    body = request.data
    assert b"fake tarball bytes" in body
    assert b'"name": "wingman-backup-x.tar.gz"' in body
    assert b'"parents": ["folder-1"]' in body


def test_upload_file_raises_over_size_cap(tmp_path: Path) -> None:
    local = tmp_path / "huge.tar.gz"
    local.write_bytes(b"x" * (MAX_UPLOAD_BYTES + 1))
    http = ScriptedHttp([])  # must never be called

    with pytest.raises(GDriveApiError, match="over the"):
        upload_file("tok", local, "folder-1", "huge.tar.gz", "application/gzip", http=http)
    assert http.requests == []


def test_upload_file_raises_on_api_failure(tmp_path: Path) -> None:
    local = tmp_path / "small.md"
    local.write_text("digest content", encoding="utf-8")
    http = ScriptedHttp([(400, {"error": {"message": "bad request"}})])

    with pytest.raises(GDriveApiError, match="bad request"):
        upload_file("tok", local, "folder-1", "small.md", "text/markdown", http=http)


def test_default_http_wraps_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    from wingman.infrastructure import gdrive as gdrive_module

    def boom(*args: object, **kwargs: object) -> object:
        raise urllib.error.URLError("simulated network failure")

    monkeypatch.setattr(gdrive_module.urllib.request, "urlopen", boom)

    with pytest.raises(GDriveApiError, match="simulated network failure"):
        gdrive_module._default_http(
            urllib.request.Request("https://www.googleapis.com/drive/v3/files")
        )
