"""OAuth 2.1 bearer-token validation for the shared multi-tenant process.

SPIKE for docs/RFC-081-DRAFT-hosted-oauth.md. Off unless the operator passes
every '--oauth-*' flag; with it off nothing here is imported at request time
and the capability-token path behaves exactly as before.

Wingman is a pure *resource server* here (MCP authorization spec): it never
issues tokens. An external authorization server does that; this module only
checks that a presented access token is one it may accept, then maps the
verified identity to a tenant.

What a request must satisfy to reach a tool body:

  1. exactly one 'Authorization: Bearer <jwt>' header (a token in the query
     string is never read);
  2. an asymmetric signature (RS256/ES256 only — 'none' and the HMAC family
     are refused by construction) by a key the issuer publishes;
  3. 'iss' equal to the configured issuer, 'aud' containing this server's
     canonical resource URI (RFC 8707), and 'exp', 'sub' present and valid;
  4. the verified '(iss, sub)' listed in the identity map, which names the
     tenant. Authenticated but unlisted is 403, not 401: the caller proved
     who they are, and is simply not provisioned.

Failures answer 'WWW-Authenticate: Bearer resource_metadata=...' (RFC 9728) so
a spec-following client can discover the authorization server. The response
never says *why* a token was refused beyond RFC 6750's 'invalid_token'.

Not in this slice (each is named in the RFC, none silently assumed): scope
enforcement, revocation/introspection, public signup, and the web UI (which
cannot send a bearer header from a browser link).
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import anyio
import jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from wingman.infrastructure.config import tenant_config_scope
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.tenant_asgi import (
    TenantRoutingASGIApp,
    current_tenant_config,
    request_origin_scope,
)
from wingman.infrastructure.tenants import TenantIndex

_logger = get_logger("oauth_bearer")

#: Asymmetric only. Deliberately not configurable in the spike: allowing HS*
#: with a public key as the "secret" is the classic algorithm-confusion hole.
ALLOWED_ALGORITHMS = ("RS256", "ES256")

#: Seconds of clock skew tolerated on 'exp'.
_LEEWAY_SECONDS = 30

_WELL_KNOWN = "/.well-known/oauth-protected-resource"

#: Raw token -> the key that should have signed it. In production this is a
#: JWKS lookup; tests inject a fixed key.
KeyResolver = Callable[[str], Any]


class OAuthConfigError(ValueError):
    """The operator's --oauth-* settings cannot be served safely."""


class IdentityMapError(Exception):
    """The identity map file is missing, malformed, or ambiguous."""


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


def jwks_key_resolver(jwks_uri: str) -> KeyResolver:
    """A resolver backed by the issuer's published JWKS.

    PyJWKClient caches fetched keys, but its first fetch (and any refresh on
    an unknown 'kid') is a blocking HTTP call. The ASGI wrapper therefore
    runs the resolver in a worker thread, so a slow issuer stalls one
    request, not the event loop every other tenant shares.
    """
    client = jwt.PyJWKClient(jwks_uri, cache_keys=True, timeout=5)

    def resolve(token: str) -> Any:
        return client.get_signing_key_from_jwt(token).key

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
    scopes = tuple(raw_scope.split()) if isinstance(raw_scope, str) else tuple(raw_scope or ())
    return BearerIdentity(issuer=settings.issuer, subject=subject, scopes=scopes)


class IdentityMap:
    """Which verified identity is which tenant.

    Keyed on (issuer, subject) — never on an email address, which a provider
    may hide or let a person change. Several identities may name one slug;
    that is how one person with two sign-in methods is linked, by an explicit
    line an operator (or a provisioning service) wrote, never by guessing.
    """

    def __init__(self, entries: dict[tuple[str, str], str]) -> None:
        self._entries = dict(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def slug_for(self, issuer: str, subject: str) -> str | None:
        return self._entries.get((issuer, subject))

    def slugs(self) -> set[str]:
        return set(self._entries.values())

    def entries(self) -> dict[tuple[str, str], str]:
        """A copy for operator provisioning; callers cannot mutate live routing."""
        return dict(self._entries)

    def reload(self, path: Path) -> None:
        """Replace the live mapping only after a complete new file validates."""
        self.replace_with(type(self).from_toml(path))

    def replace_with(self, fresh: IdentityMap) -> None:
        """Install a fully parsed candidate mapping without rereading its file."""
        self._entries = fresh._entries

    @classmethod
    def from_toml(cls, path: Path) -> IdentityMap:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise IdentityMapError(f"identity map {path} could not be read: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise IdentityMapError(f"identity map {path} is not valid TOML: {exc}") from exc
        rows = data.get("identity", [])
        if not isinstance(rows, list):
            raise IdentityMapError(f"identity map {path}: 'identity' must be an array of tables")
        entries: dict[tuple[str, str], str] = {}
        for position, row in enumerate(rows, start=1):
            issuer = row.get("iss") if isinstance(row, dict) else None
            subject = row.get("sub") if isinstance(row, dict) else None
            slug = row.get("slug") if isinstance(row, dict) else None
            if not (
                isinstance(issuer, str)
                and issuer
                and isinstance(subject, str)
                and subject
                and isinstance(slug, str)
                and slug
            ):
                raise IdentityMapError(
                    f"identity map {path}: entry {position} needs non-empty string "
                    "'iss', 'sub' and 'slug'"
                )
            key = (issuer, subject)
            if key in entries:
                # Fail closed: an identity that names two tenants must not
                # quietly pick whichever came first.
                raise IdentityMapError(
                    f"identity map {path}: ({issuer!r}, {subject!r}) appears more than once"
                )
            entries[key] = slug
        return cls(entries)


@contextmanager
def _identity_map_lock(path: Path) -> Iterator[None]:
    """Serialize all read-modify-write updates across operator processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def bind_trusted_identity(path: Path, issuer: str, subject: str, slug: str) -> bool:
    """Atomically bind one operator-approved identity; False means already exact."""
    if not issuer or not subject or not slug:
        raise IdentityMapError("issuer, subject and slug must be non-empty")
    with _identity_map_lock(path):
        try:
            identities = IdentityMap.from_toml(path)
        except IdentityMapError as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                raise
            identities = IdentityMap({})
        entries = identities.entries()
        key = (issuer, subject)
        existing = entries.get(key)
        if existing == slug:
            return False
        if existing is not None:
            raise IdentityMapError(
                f"identity ({issuer!r}, {subject!r}) is already bound to {existing!r}"
            )
        entries[key] = slug
        lines: list[str] = []
        for (row_issuer, row_subject), row_slug in sorted(entries.items()):
            lines.extend(
                (
                    "[[identity]]",
                    f"iss = {json.dumps(row_issuer)}",
                    f"sub = {json.dumps(row_subject)}",
                    f"slug = {json.dumps(row_slug)}",
                    "",
                )
            )
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).chmod(0o600)
            os.replace(temporary, path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
    return True


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


class BearerRoutingASGIApp:
    """Wraps the MCP endpoint: verify the bearer, resolve the tenant, bind its
    Config for the request, delegate. Anything that does not clear every step
    is answered here and never reaches FastMCP or a tool body."""

    def __init__(
        self,
        inner: ASGIApp,
        index: TenantIndex,
        identities: IdentityMap,
        settings: OAuthSettings,
        resolve_key: KeyResolver,
        local_mcp_path: str = "/mcp",
    ) -> None:
        self._inner = inner
        self._index = index
        self._identities = identities
        self._settings = settings
        self._resolve_key = resolve_key
        self._local_mcp_path = local_mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Fail closed. (The capability-token wrapper passes non-HTTP
            # scopes straight to the inner app; this one does not.)
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        presented = _bearer_token(scope)
        if presented is None:
            await _challenge(self._settings, self._local_mcp_path)(scope, receive, send)
            return
        if isinstance(presented, BearerError):
            _logger.info("bearer refused: %s", presented.reason)
            await _challenge(self._settings, self._local_mcp_path, "invalid_request")(
                scope, receive, send
            )
            return
        try:
            identity = await anyio.to_thread.run_sync(
                validate_access_token, presented, self._settings, self._resolve_key
            )
        except BearerError as exc:
            _logger.info("bearer refused: %s", exc.reason)
            await _challenge(self._settings, self._local_mcp_path, "invalid_token")(
                scope, receive, send
            )
            return
        slug = self._identities.slug_for(identity.issuer, identity.subject)
        tenant = self._index.by_slug(slug) if slug else None
        if tenant is None:
            # Verified, but not provisioned (or provisioned to a tenant that
            # has left the registry). Same answer for both, so it cannot be
            # used to learn which tenants exist.
            _logger.info("bearer verified but unprovisioned: sub=%s", identity.subject)
            await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)
            return
        # A resolver, not a frozen Config — same reason as the token path (#404).
        with (
            tenant_config_scope(lambda: current_tenant_config(self._index, tenant)),
            request_origin_scope(None),
        ):
            await self._inner(scope, receive, send)


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


def bind_oauth_routing(
    app: Starlette,
    legacy_path: str,
    oauth_path: str,
    index: TenantIndex,
    identities: IdentityMap,
    settings: OAuthSettings,
    resolve_key: KeyResolver,
) -> None:
    """Add a bearer-authenticated MCP route and the metadata routes.

    The capability-token route at 'legacy_path' is left exactly as it is.
    Raises if it is not there, for the same reason bind_tenant_routing does:
    a silent no-op here would look like OAuth was enabled when it was not.
    """
    for route in app.routes:
        if isinstance(route, Route) and route.path == legacy_path:
            endpoint = route.app
            inner = endpoint.inner if isinstance(endpoint, TenantRoutingASGIApp) else endpoint
            break
    else:
        raise RuntimeError(
            f"no Starlette route at {legacy_path!r} to take the MCP endpoint from — "
            "did server.settings.streamable_http_path change?"
        )
    document: dict[str, Any] = {
        "resource": settings.audience,
        "authorization_servers": [settings.issuer],
        "bearer_methods_supported": ["header"],
    }
    if settings.scopes:
        document["scopes_supported"] = list(settings.scopes)

    async def metadata(_request: Request) -> JSONResponse:
        return JSONResponse(document)

    wrapped = BearerRoutingASGIApp(
        inner, index, identities, settings, resolve_key, local_mcp_path=oauth_path
    )
    added: list[Route] = [Route(oauth_path, endpoint=wrapped)]
    added.extend(
        Route(path, endpoint=metadata, methods=["GET"])
        for path in _metadata_paths(settings, oauth_path)
    )
    app.router.routes[0:0] = added
