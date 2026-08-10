"""Google Drive OAuth: the device-code flow, and the per-account credentials
it produces (RFC-053, #205, resolving #151's storage-backend question for
backups + digests only — the live workspace itself is never in scope here).

Lobster is headless, so this is Google's OAuth-for-limited-input-devices
flow, not the browser-redirect "installed app" flow: wingman prints a short
code and a URL, the person opens that URL on *any* device and approves with
their own Google login. `drive_auth` is dual-surfaced (CLI `wingman drive
auth`, MCP tool `drive_auth`) as two stateless calls rather than one
long-blocking one:

1. No pending authorization on disk -> this call is *start*: it calls
   Google's device-authorization endpoint, stashes the pending
   device_code/expiry in `~/.config/wingman/gdrive-auth-pending.json` (a
   small pointer file, the same shape as Coaching Mode's
   `active-persona.json`, `infrastructure.coach_state`), and returns the
   code + URL to show the caller.
2. A pending authorization already on disk -> this call is *finish*: one
   poll against Google's token endpoint. "Still waiting" until the person
   approves; on success the refresh token is written to
   `~/.config/wingman/gdrive-credentials.json` (mode 600) and the pending
   file is removed.

Both files are scoped under the caller's home directory exactly like
RFC-046's `secrets.env`/`wingman.env` split, so isolation between accounts
(dhk, trent, ...) on a shared box is free — each account's own
`~/.config/wingman/` only ever holds its own tokens.

One shared OAuth client (client ID + "secret" — not really secret for an
installed/limited-input-device app, the same reasoning `gh`/`rclone` ship
their own public client identifiers) identifies the wingman *app*, not any
one server or user; `resolve_client_id`/`resolve_client_secret` read it with
a small ladder (environment variable, then the `wingman.env` host setting,
then a placeholder compiled in — no real Google Cloud project exists yet).

`drive.file` scope only: this app can only see/write files it creates
itself, never anything else in anyone's Drive.

No SDK dependency — plain `urllib.request`, matching this codebase's
existing house style (`infrastructure/fetch.py`,
`providers/embeddings.py`).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from wingman.infrastructure import host_config
from wingman.infrastructure.logs import get_logger

_logger = get_logger("infrastructure.gdrive_auth")

CONFIG_SUBDIR = os.path.join(".config", "wingman")
CREDENTIALS_FILENAME = "gdrive-credentials.json"
PENDING_FILENAME = "gdrive-auth-pending.json"

DEVICE_AUTHORIZATION_ENDPOINT = "https://oauth2.googleapis.com/device/code"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_REFRESH_GRANT_TYPE = "refresh_token"

# No real Google Cloud project exists yet (#205's explicit prerequisite,
# a manual Console step) — these are placeholders, overridable via
# WINGMAN_GDRIVE_CLIENT_ID / WINGMAN_GDRIVE_CLIENT_SECRET (env, then
# wingman.env) the moment a real client is created.
DEFAULT_CLIENT_ID = "REPLACE-WITH-REAL-CLIENT-ID.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "REPLACE-WITH-REAL-CLIENT-SECRET"

_TIMEOUT_SECONDS = 30
_DEFAULT_EXPIRES_IN = 1800  # Google's own device-code default, used only if a response omits it
_DEFAULT_INTERVAL = 5


class GDriveAuthError(Exception):
    """A device-flow or token-refresh call to Google failed."""


class DeviceAuthResult(BaseModel):
    """What one `drive_auth()` call did — CLI/MCP render this directly."""

    status: str  # "started" | "pending" | "authorized" | "expired" | "denied"
    detail: str
    user_code: str | None = None
    verification_url: str | None = None
    expires_in: int | None = None


# (url, form data) -> (http status, parsed JSON body). Injectable so tests
# never touch the network (matches keys.py's Runner / embeddings.py's
# urllib-direct precedent).
Poster = Callable[[str, dict[str, str]], tuple[int, dict[str, Any]]]


def _default_poster(url: str, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 — fixed https endpoints, no user input in the URL
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
            return int(response.status), body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            body = {}
        return exc.code, body if isinstance(body, dict) else {}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise GDriveAuthError(f"could not reach Google's OAuth endpoint ({exc})") from exc


def _config_dir(home: Path | None) -> Path:
    return (home if home is not None else Path.home()) / CONFIG_SUBDIR


def credentials_path(home: Path | None = None) -> Path:
    return _config_dir(home) / CREDENTIALS_FILENAME


def pending_path(home: Path | None = None) -> Path:
    return _config_dir(home) / PENDING_FILENAME


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write 0600, atomically (temp file + rename) — mirrors
    host_config._atomic_write; kept local since that helper is private."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def is_authorized(home: Path | None = None) -> bool:
    """Whether a refresh token has already been stored for this account —
    the signal `application.gdrive_push` uses to decide push happens at
    all (authorization itself is the explicit opt-in, #205)."""
    return credentials_path(home).exists()


def read_credentials(home: Path | None = None) -> dict[str, Any] | None:
    path = credentials_path(home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_credentials(refresh_token: str, scope: str, home: Path | None) -> None:
    _atomic_write_json(
        credentials_path(home),
        {"refresh_token": refresh_token, "scope": scope, "obtained_at": time.time()},
    )
    _logger.info("gdrive credentials stored home=%s", home or Path.home())


def clear_credentials(home: Path | None = None) -> None:
    credentials_path(home).unlink(missing_ok=True)


def _read_pending(home: Path | None) -> dict[str, Any] | None:
    path = pending_path(home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_pending(pending: dict[str, Any], home: Path | None) -> None:
    _atomic_write_json(pending_path(home), pending)


def _clear_pending(home: Path | None) -> None:
    pending_path(home).unlink(missing_ok=True)


def resolve_client_id(home: Path | None = None) -> str:
    """WINGMAN_GDRIVE_CLIENT_ID: environment, then wingman.env, then the
    placeholder compiled into this module (#205 — no real Google Cloud
    project exists yet)."""
    env_value = os.environ.get("WINGMAN_GDRIVE_CLIENT_ID", "").strip()
    if env_value:
        return env_value
    host_value = host_config.read_host_settings(home).get("WINGMAN_GDRIVE_CLIENT_ID", "").strip()
    return host_value or DEFAULT_CLIENT_ID


def resolve_client_secret(home: Path | None = None) -> str:
    """WINGMAN_GDRIVE_CLIENT_SECRET, same ladder as resolve_client_id."""
    env_value = os.environ.get("WINGMAN_GDRIVE_CLIENT_SECRET", "").strip()
    if env_value:
        return env_value
    host_value = (
        host_config.read_host_settings(home).get("WINGMAN_GDRIVE_CLIENT_SECRET", "").strip()
    )
    return host_value or DEFAULT_CLIENT_SECRET


def _start(client_id: str, poster: Poster, home: Path | None) -> DeviceAuthResult:
    status, body = poster(
        DEVICE_AUTHORIZATION_ENDPOINT, {"client_id": client_id, "scope": DRIVE_FILE_SCOPE}
    )
    device_code = body.get("device_code")
    user_code = body.get("user_code")
    verification_url = body.get("verification_url") or body.get("verification_uri")
    if status != 200 or not device_code or not user_code or not verification_url:
        detail = body.get("error_description") or body.get("error") or f"HTTP {status}"
        raise GDriveAuthError(f"could not start Drive device authorization ({detail})")
    expires_in = int(body.get("expires_in", _DEFAULT_EXPIRES_IN))
    interval = int(body.get("interval", _DEFAULT_INTERVAL))
    now = time.time()
    _write_pending(
        {
            "device_code": str(device_code),
            "user_code": str(user_code),
            "verification_url": str(verification_url),
            "interval": interval,
            # Deliberately NOT setting poll_not_before here. The device-flow
            # interval governs an automated polling loop; each poll here is a
            # separate command a person ran, and the wait that matters is
            # them approving in a browser. Gating the first one would tell
            # somebody who approved quickly to come back in five seconds.
            # An explicit slow_down from Google is different, and is honoured
            # in _finish.
            "requested_at": now,
            "expires_at": now + expires_in,
        },
        home,
    )
    _logger.info("gdrive device auth started expires_in=%d", expires_in)
    return DeviceAuthResult(
        status="started",
        detail=(
            f"Open {verification_url} and enter code {user_code}. "
            f"Expires in {max(expires_in // 60, 1)} minute(s) — after approving, "
            "call drive_auth (or run 'wingman drive auth') again to finish."
        ),
        user_code=str(user_code),
        verification_url=str(verification_url),
        expires_in=expires_in,
    )


def _finish(
    pending: dict[str, Any], client_id: str, client_secret: str, poster: Poster, home: Path | None
) -> DeviceAuthResult:
    if time.time() > float(pending.get("expires_at", 0)):
        _clear_pending(home)
        return DeviceAuthResult(
            status="expired",
            detail="the code expired before it was approved. Call drive_auth to start again.",
        )
    # Honour the provider's own slow_down, and the interval it set at the
    # start. Returning locally is the point: another immediate request is
    # what earned the slow_down in the first place.
    not_before = float(pending.get("poll_not_before", 0))
    if time.time() < not_before:
        wait = max(1, int(not_before - time.time()))
        return DeviceAuthResult(
            status="pending",
            detail=f"Still waiting — Google asked us to poll less often. Try again in {wait}s.",
            user_code=str(pending.get("user_code", "")),
            verification_url=str(pending.get("verification_url", "")),
        )
    status, body = poster(
        TOKEN_ENDPOINT,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "device_code": str(pending["device_code"]),
            "grant_type": _DEVICE_GRANT_TYPE,
        },
    )
    refresh_token = body.get("refresh_token")
    if status == 200 and refresh_token:
        scope = str(body.get("scope", DRIVE_FILE_SCOPE))
        _write_credentials(str(refresh_token), scope, home)
        _clear_pending(home)
        return DeviceAuthResult(
            status="authorized",
            detail="Connected — future backups and digests push to your Drive automatically "
            "(pass drive=false / --no-drive on any one run to skip it).",
        )
    error = str(body.get("error", ""))
    if error == "authorization_pending":
        return DeviceAuthResult(
            status="pending",
            detail="Still waiting for approval — open the URL from before and approve it, "
            "then call drive_auth again.",
            user_code=str(pending.get("user_code", "")),
            verification_url=str(pending.get("verification_url", "")),
        )
    if error == "slow_down":
        pending = dict(pending)
        interval = int(pending.get("interval", _DEFAULT_INTERVAL)) + _DEFAULT_INTERVAL
        pending["interval"] = interval
        # A stored interval nothing reads is not a backoff. The next allowed
        # poll is a timestamp because that is the form the check at the top
        # of _finish can act on — the caller is a person re-running a
        # command, not a loop we control, so "wait n seconds" has to survive
        # until the next process.
        pending["poll_not_before"] = time.time() + interval
        _write_pending(pending, home)
        return DeviceAuthResult(
            status="pending",
            detail="Still waiting (polling too fast — slowed down). Call drive_auth again shortly.",
            user_code=str(pending.get("user_code", "")),
            verification_url=str(pending.get("verification_url", "")),
        )
    if error in ("access_denied", "expired_token"):
        _clear_pending(home)
        if error == "access_denied":
            return DeviceAuthResult(
                status="denied",
                detail="the request was denied. Call drive_auth to start over.",
            )
        return DeviceAuthResult(
            status="expired",
            detail="the code expired before it was approved. Call drive_auth to start again.",
        )
    _clear_pending(home)
    detail = body.get("error_description") or error or f"HTTP {status}"
    raise GDriveAuthError(f"Drive device token exchange failed ({detail})")


def drive_auth(
    home: Path | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    poster: Poster | None = None,
) -> DeviceAuthResult:
    """The one entrypoint CLI/MCP both call. No pending authorization on
    disk starts a new one; a pending authorization polls once to finish it
    (see module docstring for the full two-call shape)."""
    poster = poster if poster is not None else _default_poster
    client_id = client_id if client_id is not None else resolve_client_id(home)
    client_secret = client_secret if client_secret is not None else resolve_client_secret(home)
    pending = _read_pending(home)
    if pending is None:
        return _start(client_id, poster, home)
    return _finish(pending, client_id, client_secret, poster, home)


def access_token(
    home: Path | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    poster: Poster | None = None,
) -> str:
    """Exchange the stored refresh token for a fresh access token. Raises
    GDriveAuthError when unauthorized or the refresh fails — callers that
    must never break a local write (application.gdrive_push) catch this
    and skip instead of propagating it."""
    creds = read_credentials(home)
    if creds is None or not creds.get("refresh_token"):
        raise GDriveAuthError(
            "Drive is not authorized yet — run 'wingman drive auth' (or the drive_auth MCP tool)."
        )
    poster = poster if poster is not None else _default_poster
    client_id = client_id if client_id is not None else resolve_client_id(home)
    client_secret = client_secret if client_secret is not None else resolve_client_secret(home)
    status, body = poster(
        TOKEN_ENDPOINT,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": str(creds["refresh_token"]),
            "grant_type": _REFRESH_GRANT_TYPE,
        },
    )
    token = body.get("access_token")
    if status != 200 or not token:
        detail = body.get("error_description") or body.get("error") or f"HTTP {status}"
        raise GDriveAuthError(f"could not refresh the Drive access token ({detail})")
    return str(token)
