"""Claim an email-bound invite on a verified sign-in (#585, RFC-081 amendment).

The operator's `oauth-invite --email` prepares an empty, unbound tenant and
stores a keyed hash of the address. When someone signs in on either surface
and WorkOS says their address is verified, this binds that identity to the
prepared tenant with no operator step:

- Browser: the code exchange's `user` object (id, email, email_verified),
  checked against the verified session-token subject.
- MCP: the issuer's own `/oauth2/userinfo`, called with the bearer token that
  has already passed validation, checked against the verified subject.

#582 established (live, 2026-10-10) that one WorkOS user has the same full
subject on the browser session token and the AuthKit MCP token, so a claim
binds that subject under both configured trusted issuers at once: the second
surface then works without another sign-in step. Only the two issuers this
process was configured with; never an issuer taken from a token.

Everything fails closed: no verified address, no match, an expired or busy
invite, a tenant that is not exactly the prepared one, or any lookup or write
error leaves the person on today's pending path. Logs carry the pending
reference and the outcome, never the address, subject, token or response.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlparse

from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.oauth_bearer import (
    IdentityMap,
    IdentityMapError,
    _identity_map_lock,
    _identity_map_or_empty,
    _read_reservation,
    write_identity_map_locked,
)
from wingman.infrastructure.oauth_onboarding import (
    INVITE_MARKER,
    OAuthOnboardingError,
    OAuthOnboardingStore,
    pending_reference,
)
from wingman.infrastructure.tenants import TenantIndex

_logger = get_logger("oauth_invite_claim")

USERINFO_PATH = "/oauth2/userinfo"
_USERINFO_TIMEOUT_SECONDS = 5.0
_USERINFO_MAX_BYTES = 65536
#: A waiting person costs one userinfo lookup per window, not one per request.
_MISS_TTL_SECONDS = 60.0
_MISS_LIMIT = 4096

#: (url, method, bearer token) -> (HTTP status, body)
Fetch = Callable[[str, str, str], tuple[int, bytes]]


def _urllib_fetch(url: str, method: str, token: str) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=b"" if method == "POST" else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 — https issuer checked by userinfo_url
            request, timeout=_USERINFO_TIMEOUT_SECONDS
        ) as response:
            return int(response.status), response.read(_USERINFO_MAX_BYTES)
    except urllib.error.HTTPError as exc:
        return int(exc.code), b""


def userinfo_url(issuer: str) -> str:
    """The configured issuer's own userinfo endpoint; https only."""
    parsed = urlparse(issuer)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("userinfo is only fetched from an https issuer")
    return issuer.rstrip("/") + USERINFO_PATH


def verified_email_from(user: object, expected_subject: str, *, id_field: str) -> str | None:
    """The address, only if `user` is this exact subject and WorkOS verified it."""
    if not isinstance(user, dict):
        return None
    if user.get(id_field) != expected_subject:
        return None
    if user.get("email_verified") is not True:
        return None
    email = user.get("email")
    return email if isinstance(email, str) and email else None


def verified_userinfo_email(
    issuer: str, token: str, expected_subject: str, *, fetch: Fetch = _urllib_fetch
) -> str | None:
    """Ask the issuer which verified address this already-validated token has."""
    reference = pending_reference(issuer, expected_subject)
    try:
        url = userinfo_url(issuer)
    except ValueError:
        _logger.warning("userinfo lookup refused for a non-https issuer: ref=%s", reference)
        return None
    try:
        status, body = fetch(url, "GET", token)
        if status == 405:
            status, body = fetch(url, "POST", token)
    except Exception as exc:  # noqa: BLE001 — any failure is a miss, never an admission
        _logger.info("userinfo lookup failed: ref=%s error=%s", reference, type(exc).__name__)
        return None
    if status != 200:
        _logger.info("userinfo lookup failed: ref=%s status=%s", reference, status)
        return None
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        _logger.info("userinfo lookup failed: ref=%s error=unreadable-body", reference)
        return None
    return verified_email_from(data, expected_subject, id_field="sub")


class EmailInviteClaimer:
    """Binds a verified identity to the tenant prepared for its address."""

    def __init__(
        self,
        identity_path: Path,
        store: OAuthOnboardingStore,
        index: TenantIndex,
        identities: IdentityMap,
        trusted_issuers: Iterable[str],
        *,
        fetch: Fetch = _urllib_fetch,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._identity_path = identity_path
        self._store = store
        self._index = index
        self._identities = identities
        self._issuers = tuple(dict.fromkeys(trusted_issuers))
        self._fetch = fetch
        self._monotonic = monotonic
        self._misses: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.time()

    def claim_with_userinfo(self, issuer: str, subject: str, token: str) -> str | None:
        """MCP: look up the token's verified address (rate-limited), then claim."""
        key = (issuer, subject)
        now = self._monotonic()
        with self._lock:
            expiry = self._misses.get(key)
            if expiry is not None and expiry > now:
                return None
        email = verified_userinfo_email(issuer, token, subject, fetch=self._fetch)
        slug = self.claim(issuer, subject, email) if email is not None else None
        if slug is None:
            with self._lock:
                self._misses = {k: v for k, v in self._misses.items() if v > now}
                if len(self._misses) < _MISS_LIMIT:
                    self._misses[key] = now + _MISS_TTL_SECONDS
        return slug

    def claim(self, issuer: str, subject: str, verified_email: str) -> str | None:
        """Bind (both trusted issuers, subject) to the invited tenant, or None."""
        reference = pending_reference(issuer, subject)
        if issuer not in self._issuers:
            return None
        try:
            slug = self._claim_locked(issuer, subject, verified_email)
        except (OAuthOnboardingError, IdentityMapError) as exc:
            # The class only: these messages can name the subject.
            _logger.error(
                "email invite could not be claimed: ref=%s error=%s", reference, type(exc).__name__
            )
            return None
        if slug is None:
            _logger.info("no claimable email invite: ref=%s", reference)
            return None
        try:
            self._identities.replace_with(IdentityMap.from_toml(self._identity_path))
        except IdentityMapError as exc:
            _logger.error(
                "email invite claimed but the live identity map was not refreshed: ref=%s error=%s",
                reference,
                type(exc).__name__,
            )
        tenant = self._index.by_slug(slug)
        if tenant is not None:
            try:
                (tenant.data_dir / INVITE_MARKER).unlink(missing_ok=True)
            except OSError:
                pass
        _logger.info("email invite claimed: ref=%s slug=%s", reference, slug)
        return slug

    def _claim_locked(self, issuer: str, subject: str, verified_email: str) -> str | None:
        path = self._identity_path
        keys = [(trusted, subject) for trusted in self._issuers]
        with _identity_map_lock(path):
            current = _identity_map_or_empty(path)
            bound = current.slug_for(issuer, subject)
            if bound is not None:
                # A concurrent claim from the other surface got here first.
                return bound if self._index.by_slug(bound) is not None else None
            wall = time.time()
            for key in keys:
                if current.slug_for(*key) is not None:
                    # The other surface already belongs to a tenant: an
                    # operator decision, not an automatic one.
                    return None
                if _read_reservation(path, *key, now=wall) is not None:
                    return None
            taken = current.slugs()

            def ready(slug: str) -> bool:
                tenant = self._index.by_slug(slug)
                return (
                    tenant is not None
                    and slug not in taken
                    and not tenant.privileged
                    and not tenant.funded
                    and (tenant.data_dir / INVITE_MARKER).is_file()
                )

            def commit(slug: str) -> None:
                entries = current.entries()
                for key in keys:
                    entries[key] = slug
                write_identity_map_locked(path, entries)

            return self._store.claim_email_invite(
                verified_email,
                subject=subject,
                issuers=self._issuers,
                ready=ready,
                commit=commit,
                now=self._now(),
            )
