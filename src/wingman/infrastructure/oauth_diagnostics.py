"""Temporary #582 diagnostics. Never used for authorization or admission."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Literal
from urllib.parse import urlparse

from wingman.infrastructure.logs import get_logger

_NAMES = frozenset(
    [
        "iss",
        "aud",
        "sub",
        "client_id",
        "org_id",
        "sid",
        "scope",
        "scp",
        "jti",
        "exp",
        "iat",
        "nbf",
        "auth_time",
        "act",
        "role",
        "roles",
        "permissions",
        "entitlements",
        "feature_flags",
        "email",
        "email_verified",
        "urn:wingman:email",
        "urn:wingman:email_verified",
        "urn:myapp:email",
    ]
)
_USER_NAMES = frozenset(
    [
        "object",
        "id",
        "email",
        "email_verified",
        "first_name",
        "last_name",
        "name",
        "profile_picture_url",
        "external_id",
        "metadata",
        "last_sign_in_at",
        "locale",
        "created_at",
        "updated_at",
    ]
)
_USERINFO_NAMES = frozenset(
    [
        "sub",
        "email",
        "email_verified",
        "name",
        "given_name",
        "family_name",
        "picture",
        "updated_at",
    ]
)
_USERINFO_TIMEOUT = 5.0
_logger = get_logger("oauth_diagnostics")
_lock = threading.Lock()
_salt = secrets.token_bytes(32)
_seen: dict[tuple[str, bytes], float] = {}
_TTL = 600.0
_LIMIT = 64


Request = Callable[[str, str, dict[str, str], float], tuple[int, bytes]]
Runner = Callable[[Callable[[], None]], None]


def _urllib_request(
    method: str, url: str, headers: dict[str, str], timeout: float
) -> tuple[int, bytes]:
    data = b"" if method == "POST" else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — https checked by caller
            return int(response.status), response.read(65536)
    except urllib.error.HTTPError as exc:
        return int(exc.code), b""


def _in_background(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="wingman-oauth-diagnostic", daemon=True).start()


# Module-level so tests can substitute them; never configurable at runtime.
_request: Request = _urllib_request
_run: Runner = _in_background


def enabled() -> bool:
    return os.environ.get("WINGMAN_OAUTH_IDENTITY_DIAGNOSTICS") == "1"


def _flag(value: object) -> bool | None:
    return value if type(value) is bool else None


def observe_verified(
    surface: Literal["mcp", "browser"],
    claims: Mapping[str, object],
    *,
    token: str | None = None,
    issuer: str | None = None,
) -> None:
    """Call only AFTER the normal signature and trust checks succeed.

    For the MCP surface, ``token`` and ``issuer`` also schedule one userinfo
    probe per subject per window, off the request path.

    Equality means this subject was also seen on the opposite configured
    surface in this process in the last ten minutes. Only keyed digests are
    retained, bounded to 64 observations; they are never emitted.
    """
    if not enabled():
        return
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        return
    digest = hmac.digest(_salt, subject.encode(), hashlib.sha256)
    opposite = "browser" if surface == "mcp" else "mcp"
    now = time.monotonic()
    with _lock:
        for key, expiry in list(_seen.items()):
            if expiry <= now:
                del _seen[key]
        match = (opposite, digest) in _seen
        if (surface, digest) in _seen:
            return
        if len(_seen) >= _LIMIT:
            return
        _seen[surface, digest] = now + _TTL
    _logger.info(
        "oauth_identity_diagnostic %s",
        json.dumps(
            {
                "surface": surface,
                "claim_names": sorted(_NAMES.intersection(claims)),
                "other_claim_count": len(claims.keys() - _NAMES),
                "email_verified": _flag(claims.get("email_verified")),
                "custom_email_verified": _flag(claims.get("urn:wingman:email_verified")),
                "same_subject_seen_on_other_surface": match,
            },
            sort_keys=True,
        ),
    )
    if surface == "mcp" and token and issuer:
        _run(lambda: _probe_userinfo(token, issuer, subject))


def _log_userinfo(status: int | str, fields: Mapping[str, object], subject: str) -> None:
    _logger.info(
        "oauth_identity_diagnostic %s",
        json.dumps(
            {
                "surface": "mcp_userinfo",
                "status": status,
                "field_names": sorted(_USERINFO_NAMES.intersection(fields)),
                "other_field_count": len(fields.keys() - _USERINFO_NAMES),
                "email_verified": _flag(fields.get("email_verified")),
                "sub_matches_verified_sub": fields.get("sub") == subject,
            },
            sort_keys=True,
        ),
    )


def _probe_userinfo(token: str, issuer: str, subject: str) -> None:
    """Ask the issuer's own userinfo endpoint what this token can see.

    Only names, the strict verification flag, and a subject-equality boolean
    are logged. The token goes nowhere but the configured issuer over https.
    """
    url = issuer.rstrip("/") + "/oauth2/userinfo"
    target, origin = urlparse(url), urlparse(issuer)
    if target.scheme != "https" or origin.scheme != "https" or target.netloc != origin.netloc:
        _log_userinfo("refused-issuer", {}, subject)
        return
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        status, body = _request("GET", url, headers, _USERINFO_TIMEOUT)
        if status == 405:
            status, body = _request("POST", url, headers, _USERINFO_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — diagnostic only; never affects admission
        _log_userinfo(type(exc).__name__, {}, subject)
        return
    fields: Mapping[str, object] = {}
    if status == 200:
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            fields = parsed
    _log_userinfo(status, fields, subject)


def observe_exchange_user(payload: Mapping[str, object], verified_subject: str) -> None:
    """Only pass a response whose access token has already been verified."""
    if not enabled():
        return
    user = payload.get("user")
    fields = user if isinstance(user, dict) else {}
    _logger.info(
        "oauth_identity_diagnostic %s",
        json.dumps(
            {
                "surface": "browser_exchange",
                "user_object_present": isinstance(user, dict),
                "user_field_names": sorted(_USER_NAMES.intersection(fields)),
                "other_user_field_count": len(fields.keys() - _USER_NAMES),
                "email_verified": _flag(fields.get("email_verified")),
                "user_id_matches_verified_sub": fields.get("id") == verified_subject,
            },
            sort_keys=True,
        ),
    )
