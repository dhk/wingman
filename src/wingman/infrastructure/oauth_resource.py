"""Resource-server primitives for OAuth 2.1 bearer tokens (RFC-081).

The generic half of what used to live in ``oauth_bearer``: everything needed to
decide "is this access token acceptable to this server?" and to tell a client
where to authenticate. Nothing here knows about tenants, identity maps, or
onboarding, so another service can reuse it as-is (the skill-telemetry server
does, as a copy).

Wingman is a pure *resource server*: it never issues tokens. An external
authorization server does that; this module only checks that a presented
access token is one it may accept.

  * an asymmetric signature (RS256/ES256 only; 'none' and the HMAC family are
    refused by construction) by a key the issuer publishes;
  * 'iss' equal to the configured issuer, 'aud' containing this server's
    canonical resource URI (RFC 8707), and 'exp', 'sub' present and valid.

Failure answers carry 'WWW-Authenticate: Bearer resource_metadata=...'
(RFC 9728); the response never says *why* a token was refused beyond RFC 6750's
'invalid_token'.

Mapping a verified identity to a tenant, and the ASGI wrapper that does it, stay
in ``oauth_bearer``. That module re-exports every name below, so existing
imports keep working.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import jwt
from starlette.responses import PlainTextResponse
from starlette.types import Scope

#: Asymmetric only. Deliberately not configurable: allowing HS*
#: with a public key as the "secret" is the classic algorithm-confusion hole.
ALLOWED_ALGORITHMS = ("RS256", "ES256")

#: Seconds of clock skew tolerated on 'exp'.
_LEEWAY_SECONDS = 30

# An attacker controls ``kid``. Without a shared miss cooldown, every novel
# value makes PyJWKClient refresh the issuer synchronously, turning this
# endpoint into an outbound-request amplifier and consuming the worker pool.
_UNKNOWN_KEY_REFRESH_COOLDOWN_SECONDS = 30.0

# WorkOS key removal must take effect without waiting for a process restart.
# Expired keys fail closed if the issuer is unavailable.
_JWKS_CACHE_TTL_SECONDS = 300.0

_WELL_KNOWN = "/.well-known/oauth-protected-resource"

#: Raw token -> the key that should have signed it. In production this is a
#: JWKS lookup; tests inject a fixed key.
KeyResolver = Callable[[str], Any]


class OAuthConfigError(ValueError):
    """The operator's --oauth-* settings cannot be served safely."""


class BearerError(Exception):
    """A presented bearer credential is unacceptable.

    'reason' is a short code for the operator's log. It never reaches the
    caller and never contains the token.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class OAuthSettings:
    """What this server tells clients about itself, and what it will accept."""

    issuer: str
    #: The canonical URI of THIS MCP server (RFC 8707). Tokens minted for any
    #: other resource are refused, which is what stops a token issued for a
    #: different service from working here.
    audience: str
    jwks_uri: str
    scopes: tuple[str, ...] = ()

    def resource_metadata_url(self, local_mcp_path: str = "/mcp") -> str:
        """The public metadata URL a client can actually reach.

        A prefix-stripping front such as ``tailscale funnel --set-path``
        removes the public mount before the request reaches this app.  Keep
        the explicit metadata link inside that public mount, while naming the
        local route served by ``_metadata_paths`` after the strip.
        """
        parsed = urlparse(self.audience)
        path = parsed.path.rstrip("/")
        local_path = local_mcp_path.rstrip("/")
        public_mount = path[: -len(local_path)] if local_path and path.endswith(local_path) else ""
        return f"{parsed.scheme}://{parsed.netloc}{public_mount}{_WELL_KNOWN}{local_path}"


def _https_or_loopback(url: str, label: str) -> None:
    parsed = urlparse(url)
    if parsed.fragment:
        raise OAuthConfigError(f"{label} must not contain a fragment: {url!r}")
    if not parsed.netloc:
        raise OAuthConfigError(f"{label} must be an absolute URL: {url!r}")
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise OAuthConfigError(f"{label} must be https (http only for loopback): {url!r}")


def build_oauth_settings(
    issuer: str, audience: str, jwks_uri: str, scopes: tuple[str, ...] = ()
) -> OAuthSettings:
    """Validate the operator's flags into settings, or refuse to start."""
    _https_or_loopback(issuer, "--oauth-issuer")
    _https_or_loopback(audience, "--oauth-audience")
    _https_or_loopback(jwks_uri, "--oauth-jwks-uri")
    return OAuthSettings(issuer=issuer, audience=audience, jwks_uri=jwks_uri, scopes=scopes)


def jwks_key_resolver(
    jwks_uri: str,
    *,
    refresh_cooldown_seconds: float = _UNKNOWN_KEY_REFRESH_COOLDOWN_SECONDS,
    cache_ttl_seconds: float = _JWKS_CACHE_TTL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> KeyResolver:
    """A resolver backed by the issuer's published JWKS.

    PyJWKClient caches fetched keys, but its first fetch (and any refresh on
    an unknown 'kid') is a blocking HTTP call. The ASGI wrapper therefore
    runs the resolver in a worker thread, so a slow issuer stalls one
    request, not the event loop every other tenant shares.
    """
    client = jwt.PyJWKClient(jwks_uri, cache_keys=True, timeout=5)
    keys_by_id: dict[str, Any] = {}
    refresh_lock = threading.Lock()
    refresh_after = 0.0
    keys_expire_at = 0.0

    def resolve(token: str) -> Any:
        nonlocal keys_by_id, keys_expire_at, refresh_after
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in ALLOWED_ALGORITHMS:
            raise jwt.InvalidAlgorithmError("token algorithm is not allowed")
        if not isinstance(key_id, str) or not key_id:
            raise jwt.PyJWKClientError("token header has no usable kid")

        now = monotonic()
        cached = keys_by_id.get(key_id)
        if cached is not None and now < keys_expire_at:
            return cached

        # Single-flight both the fetch and a failed/unknown-key cooldown.
        # Cached valid keys bypass this lock and remain usable during an
        # issuer outage or an attack made of novel key ids.
        with refresh_lock:
            now = monotonic()
            cached = keys_by_id.get(key_id)
            if cached is not None and now < keys_expire_at:
                return cached
            if now < refresh_after:
                raise jwt.PyJWKClientError("unknown kid; JWKS refresh is cooling down")
            try:
                signing_keys = client.get_signing_keys(refresh=True)
                fresh_keys: dict[str, Any] = {}
                for signing_key in signing_keys:
                    if isinstance(signing_key.key_id, str) and signing_key.key_id:
                        fresh_keys[signing_key.key_id] = signing_key.key
            except Exception:
                refresh_after = monotonic() + refresh_cooldown_seconds
                raise
            keys_by_id = fresh_keys
            keys_expire_at = monotonic() + cache_ttl_seconds
            cached = keys_by_id.get(key_id)
            if cached is None:
                refresh_after = monotonic() + refresh_cooldown_seconds
                raise jwt.PyJWKClientError("unable to find a signing key for token kid")
            return cached

    return resolve


@dataclass(frozen=True)
class BearerIdentity:
    issuer: str
    subject: str
    scopes: tuple[str, ...]


def validate_access_token(
    token: str, settings: OAuthSettings, resolve_key: KeyResolver
) -> BearerIdentity:
    """Verify a JWT access token, or raise BearerError.

    Every failure — bad signature, wrong issuer or audience, expired, a
    header naming a forbidden algorithm, an unknown key id, an unreachable
    JWKS — collapses to BearerError so the caller cannot tell them apart.
    """
    try:
        key = resolve_key(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=list(ALLOWED_ALGORITHMS),
            audience=settings.audience,
            issuer=settings.issuer,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iss", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise BearerError(type(exc).__name__) from exc
    except Exception as exc:  # noqa: BLE001 — a JWKS/network failure must fail closed
        raise BearerError(f"key-resolution:{type(exc).__name__}") from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise BearerError("empty-subject")
    raw_scope = claims.get("scope", claims.get("scp", ""))
    if isinstance(raw_scope, str):
        scopes = tuple(raw_scope.split())
    elif isinstance(raw_scope, (list, tuple)) and all(
        isinstance(scope, str) for scope in raw_scope
    ):
        scopes = tuple(raw_scope)
    else:
        raise BearerError("malformed-scope")
    return BearerIdentity(issuer=settings.issuer, subject=subject, scopes=scopes)


def _challenge(
    settings: OAuthSettings, local_mcp_path: str, error: str | None = None
) -> PlainTextResponse:
    params = [f'resource_metadata="{settings.resource_metadata_url(local_mcp_path)}"']
    if error:
        params.insert(0, f'error="{error}"')
    return PlainTextResponse(
        "Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": "Bearer " + ", ".join(params)},
    )


def _bearer_token(scope: Scope) -> str | None | BearerError:
    """The one bearer token on the request, None if no credentials at all,
    or a BearerError if the header is present but unusable."""
    values: list[str] = [
        bytes(value).decode("latin-1")
        for key, value in scope.get("headers", [])
        if bytes(key).lower() == b"authorization"
    ]
    if not values:
        return None
    if len(values) != 1:
        return BearerError("multiple-authorization-headers")
    scheme, _, credential = str(values[0]).partition(" ")
    if scheme.lower() != "bearer" or not credential or credential != credential.strip():
        return BearerError("malformed-authorization")
    if " " in credential:
        return BearerError("malformed-authorization")
    return credential


def _metadata_paths(settings: OAuthSettings, local_mcp_path: str) -> list[str]:
    """Where to serve Protected Resource Metadata (RFC 9728 section 3).

    The well-known path is the resource's own path appended after the
    well-known prefix. Also served at the local route's path and at the bare
    prefix, because a front that strips a mount prefix (a Tailscale funnel,
    #417) means the path the client computed is not the path we see.
    """
    audience_path = urlparse(settings.audience).path.rstrip("/")
    candidates = [
        f"{_WELL_KNOWN}{audience_path}",
        f"{_WELL_KNOWN}{local_mcp_path}",
        _WELL_KNOWN,
    ]
    seen: list[str] = []
    for path in candidates:
        if path not in seen:
            seen.append(path)
    return seen
