"""Google Drive API v3: folder lookup/creation and file upload, over plain
`urllib.request` — no SDK dependency, matching this codebase's existing
house style (`infrastructure/fetch.py`, `providers/embeddings.py`).

Scope is always `drive.file` (see `infrastructure.gdrive_auth`): every call
here only ever touches files/folders this app itself created, never
anything else in the account's Drive. `application.gdrive_push` is the only
caller — it resolves an access token via `gdrive_auth.access_token` first.

Simple (non-resumable) multipart upload only, capped at 5 MB — Google's own
guidance for when a resumable upload isn't required. A workspace backup or
digest larger than that fails the push (caught and skipped by
`application.gdrive_push`, never breaking the local write); resumable
upload for larger archives is explicit follow-up scope, matching how
wiring every report type into Drive push is (#205's own scoping).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

DRIVE_FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

_TIMEOUT_SECONDS = 60
# Google's own guidance: multipart (non-resumable) upload is fine up to 5 MB.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class GDriveApiError(Exception):
    """A Drive API v3 call failed."""


# request -> (http status, raw response body). Injectable so tests never
# touch the network (mirrors keys.py's Runner / gdrive_auth.py's Poster).
HttpCall = Callable[[urllib.request.Request], tuple[int, bytes]]


def _default_http(request: urllib.request.Request) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GDriveApiError(f"could not reach the Drive API ({exc})") from exc


def _parse_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error_detail(parsed: dict[str, Any], body: bytes) -> str:
    error = parsed.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    if isinstance(error, str) and error:
        return error
    return body[:200].decode("utf-8", errors="replace") or "no response body"


def _escape_query_value(value: str) -> str:
    # Drive's search query is a small expression language; folder names we
    # create ourselves are fixed constants ("Wingman", "backups", ...), but
    # escape defensively rather than assume that never changes.
    return value.replace("\\", "\\\\").replace("'", "\\'")


def ensure_folder(
    access_token: str,
    name: str,
    parent_id: str | None = None,
    http: HttpCall | None = None,
) -> str:
    """The id of a folder named `name` directly under `parent_id` (or Drive
    root when None) — reused if it already exists (idempotent across
    repeated pushes), created otherwise."""
    http = http if http is not None else _default_http
    query = f"name = '{_escape_query_value(name)}' and mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
    query += f" and '{parent_id}' in parents" if parent_id else " and 'root' in parents"
    url = f"{DRIVE_FILES_ENDPOINT}?q={urllib.parse.quote(query)}&fields=files(id,name)"
    request = urllib.request.Request(  # noqa: S310
        url, headers={"Authorization": f"Bearer {access_token}"}
    )
    status, body = http(request)
    parsed = _parse_json(body)
    if status != 200:
        raise GDriveApiError(
            f"Drive folder lookup for {name!r} failed ({_error_detail(parsed, body)})"
        )
    existing = parsed.get("files")
    if isinstance(existing, list) and existing:
        first = existing[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])

    metadata: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME_TYPE}
    if parent_id:
        metadata["parents"] = [parent_id]
    create_request = urllib.request.Request(  # noqa: S310
        DRIVE_FILES_ENDPOINT,
        data=json.dumps(metadata).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    status, body = http(create_request)
    parsed = _parse_json(body)
    if status not in (200, 201) or not parsed.get("id"):
        raise GDriveApiError(
            f"Drive folder creation for {name!r} failed ({_error_detail(parsed, body)})"
        )
    return str(parsed["id"])


def upload_file(
    access_token: str,
    local_path: Path,
    folder_id: str,
    filename: str,
    mime_type: str,
    http: HttpCall | None = None,
) -> str:
    """Upload `local_path` into `folder_id`, named `filename`. Returns the
    new Drive file id. Raises GDriveApiError over the size cap or on any
    API failure — callers must catch this, never let it break the local
    file it describes (see module docstring)."""
    http = http if http is not None else _default_http
    data = local_path.read_bytes()
    if len(data) > MAX_UPLOAD_BYTES:
        raise GDriveApiError(
            f"{local_path.name} is {len(data)} bytes, over the {MAX_UPLOAD_BYTES}-byte "
            "simple-upload limit (resumable upload for larger files is follow-up scope, #205)"
        )
    boundary = uuid.uuid4().hex
    metadata = json.dumps({"name": filename, "parents": [folder_id]}).encode("utf-8")
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        + metadata
        + f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n".encode()
        + data
        + f"\r\n--{boundary}--".encode()
    )
    request = urllib.request.Request(  # noqa: S310
        f"{DRIVE_UPLOAD_ENDPOINT}?uploadType=multipart&fields=id",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
    )
    status, response_body = http(request)
    parsed = _parse_json(response_body)
    if status not in (200, 201) or not parsed.get("id"):
        raise GDriveApiError(
            f"Drive upload of {filename!r} failed ({_error_detail(parsed, response_body)})"
        )
    return str(parsed["id"])
