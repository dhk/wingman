"""WorkOS-backed browser sessions for the OAuth tenant setup surface.

The MCP bearer route and this browser flow share the same verified identity
map, tenant registry, JWT rules, and JWKS resolver.  The browser never receives
a capability URL.  Its cookie is an opaque, short-lived handle; the access
token stays server-side and is revalidated before every setup request.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from wingman.infrastructure.oauth_bearer import (
    BearerError,
    IdentityMap,
    KeyResolver,
    OAuthConfigError,
    OAuthSettings,
    validate_access_token,
)
from wingman.infrastructure.tenants import TenantIndex

_COOKIE = "wingman_setup_session"
_STATE_COOKIE = "wingman_oauth_state"
_STATE_TTL_SECONDS = 600.0
_SESSION_TTL_SECONDS = 1800.0
_MAX_PENDING = 256
_MAX_SESSIONS = 4096
_CALLBACK_PATH = "/oauth/callback"


@dataclass(frozen=True)
class OAuthBrowserSettings:
    client_id: str
    authorize_url: str
    token_url: str
    redirect_uri: str
    client_secret_env: str = "WINGMAN_OAUTH_WEB_CLIENT_SECRET"

    @property
    def callback_cookie_path(self) -> str:
        """Public callback path, which may include a proxy-stripped mount."""
        return urllib.parse.urlparse(self.redirect_uri).path or "/"

    @property
    def public_mount(self) -> str:
        return self.callback_cookie_path[: -len(_CALLBACK_PATH)]

    def public_path(self, path: str) -> str:
        return f"{self.public_mount}/{path.lstrip('/')}"

    def client_secret(self) -> str:
        value = os.environ.get(self.client_secret_env, "").strip()
        if not value:
            raise OAuthConfigError(f"{self.client_secret_env} is required for OAuth browser login")
        return value


def build_oauth_browser_settings(
    client_id: str,
    authorize_url: str,
    token_url: str,
    redirect_uri: str,
    *,
    client_secret_env: str = "WINGMAN_OAUTH_WEB_CLIENT_SECRET",
) -> OAuthBrowserSettings:
    """Validate every public browser-flow value before the server starts."""
    from wingman.infrastructure.oauth_bearer import _https_or_loopback

    for value, label in (
        (authorize_url, "--oauth-web-authorize-url"),
        (token_url, "--oauth-web-token-url"),
        (redirect_uri, "--oauth-web-redirect-uri"),
    ):
        _https_or_loopback(value, label)
    if not client_id.strip():
        raise OAuthConfigError("--oauth-web-client-id must not be empty")
    settings = OAuthBrowserSettings(
        client_id=client_id.strip(),
        authorize_url=authorize_url,
        token_url=token_url,
        redirect_uri=redirect_uri,
        client_secret_env=client_secret_env,
    )
    if not settings.callback_cookie_path.endswith(_CALLBACK_PATH):
        raise OAuthConfigError(f"--oauth-web-redirect-uri path must end in {_CALLBACK_PATH!r}")
    settings.client_secret()  # fail closed at startup, not after the first click
    return settings


@dataclass(frozen=True)
class _Pending:
    verifier: str
    expires_at: float


@dataclass(frozen=True)
class _Session:
    access_token: str
    csrf_token: str
    expires_at: float


TokenExchange = Callable[[str, str], str]


class OAuthBrowserSessions:
    """Bounded in-memory state, intentionally lost on restart.

    Losing it signs browsers out; it never loses tenant data or credentials.
    """

    def __init__(
        self,
        settings: OAuthBrowserSettings,
        oauth: OAuthSettings,
        identities: IdentityMap,
        index: TenantIndex,
        resolve_key: KeyResolver,
        *,
        token_exchange: TokenExchange | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._oauth = oauth
        self._identities = identities
        self._index = index
        self._resolve_key = resolve_key
        self._exchange = token_exchange or self._exchange_code
        self._monotonic = monotonic
        self._pending: dict[str, _Pending] = {}
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    @property
    def callback_cookie_path(self) -> str:
        return self._settings.callback_cookie_path

    def public_path(self, path: str) -> str:
        return self._settings.public_path(path)

    def _prune(self, now: float) -> None:
        self._pending = {
            key: value for key, value in self._pending.items() if value.expires_at > now
        }
        self._sessions = {
            key: value for key, value in self._sessions.items() if value.expires_at > now
        }

    def begin(self) -> tuple[str, str, str]:
        now = self._monotonic()
        with self._lock:
            self._prune(now)
            if len(self._pending) >= _MAX_PENDING:
                raise OAuthConfigError("too many OAuth browser logins are already pending")
            state = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
                .rstrip(b"=")
                .decode("ascii")
            )
            self._pending[state] = _Pending(verifier, now + _STATE_TTL_SECONDS)
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self._settings.client_id,
                "redirect_uri": self._settings.redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "provider": "authkit",
                "resource": self._oauth.audience,
            }
        )
        return state, verifier, f"{self._settings.authorize_url}?{query}"

    def consume_state(self, state: str, cookie_state: str) -> str | None:
        if not state or not secrets.compare_digest(state, cookie_state):
            return None
        now = self._monotonic()
        with self._lock:
            self._prune(now)
            pending = self._pending.pop(state, None)
        return pending.verifier if pending is not None else None

    def finish(self, code: str, verifier: str) -> str:
        access_token = self._exchange(code, verifier)
        identity = validate_access_token(access_token, self._oauth, self._resolve_key)
        slug = self._identities.slug_for(identity.issuer, identity.subject)
        if not slug or self._index.by_slug(slug) is None:
            raise PermissionError("authenticated identity is not provisioned")
        now = self._monotonic()
        with self._lock:
            self._prune(now)
            if len(self._sessions) >= _MAX_SESSIONS:
                raise OAuthConfigError("too many OAuth browser sessions are active")
            session_id = secrets.token_urlsafe(32)
            self._sessions[session_id] = _Session(
                access_token=access_token,
                csrf_token=secrets.token_urlsafe(32),
                expires_at=now + _SESSION_TTL_SECONDS,
            )
        return session_id

    def authorize(self, session_id: str) -> tuple[Any, str] | None:
        now = self._monotonic()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(session_id)
        if session is None:
            return None
        try:
            identity = validate_access_token(session.access_token, self._oauth, self._resolve_key)
        except BearerError:
            self.remove(session_id)
            return None
        slug = self._identities.slug_for(identity.issuer, identity.subject)
        tenant = self._index.by_slug(slug) if slug else None
        if tenant is None:
            self.remove(session_id)
            return None
        return tenant.config(), session.csrf_token

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _exchange_code(self, code: str, verifier: str) -> str:
        body = json.dumps(
            {
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret(),
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self._settings.token_url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise OAuthConfigError("OAuth token exchange failed") from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise OAuthConfigError("OAuth token exchange returned no access token")
        return token


def bind_oauth_browser(
    app: Starlette,
    local_prefix: str,
    sessions: OAuthBrowserSessions,
) -> None:
    """Mount login, callback, and the OAuth-only setup surface."""
    from wingman.webui import browser_setup_scope, ui_keys, ui_oauth_setup, ui_upload

    prefix = local_prefix.rstrip("/")

    async def login(_request: Request) -> Response:
        try:
            state, _verifier, location = sessions.begin()
        except OAuthConfigError:
            return PlainTextResponse("Login is temporarily unavailable", status_code=503)
        response = RedirectResponse(location, status_code=302)
        response.set_cookie(
            _STATE_COOKIE,
            state,
            max_age=int(_STATE_TTL_SECONDS),
            secure=True,
            httponly=True,
            samesite="lax",
            path=sessions.callback_cookie_path,
        )
        return response

    async def callback(request: Request) -> Response:
        cookie_state = request.cookies.get(_STATE_COOKIE, "")
        state = (
            request.query_params.get("state", "")
            if "state" in request.query_params
            else cookie_state
        )
        code = request.query_params.get("code", "")
        verifier = sessions.consume_state(state, cookie_state)
        if verifier is None or not code:
            return PlainTextResponse("OAuth callback was invalid or expired", status_code=400)
        try:
            session_id = await anyio.to_thread.run_sync(sessions.finish, code, verifier)
        except PermissionError:
            return PlainTextResponse("Signed in, but this account is not approved", status_code=403)
        except (BearerError, OAuthConfigError):
            return PlainTextResponse("OAuth sign-in failed", status_code=401)
        response = RedirectResponse(sessions.public_path("/setup/"), status_code=303)
        response.delete_cookie(_STATE_COOKIE, path=sessions.callback_cookie_path)
        response.set_cookie(
            _COOKIE,
            session_id,
            max_age=int(_SESSION_TTL_SECONDS),
            secure=True,
            httponly=True,
            samesite="lax",
            path=sessions.public_path("/setup"),
        )
        return response

    async def authorized(request: Request) -> tuple[Any, str, str] | None:
        session_id = request.cookies.get(_COOKIE, "")
        result = await anyio.to_thread.run_sync(sessions.authorize, session_id)
        if result is None:
            return None
        config, csrf_token = result
        return config, session_id, csrf_token

    async def setup(request: Request) -> Response:
        auth = await authorized(request)
        if auth is None:
            return RedirectResponse(sessions.public_path("/login"), status_code=303)
        config, _session_id, csrf_token = auth
        with browser_setup_scope(config, csrf_token):
            return await ui_oauth_setup(request)

    async def write(
        request: Request, endpoint: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        auth = await authorized(request)
        if auth is None:
            return PlainTextResponse("Unauthorized", status_code=401)
        config, _session_id, csrf_token = auth
        with browser_setup_scope(config, csrf_token):
            form = await request.form()
            presented = form.get("_csrf")
            if not isinstance(presented, str) or not secrets.compare_digest(presented, csrf_token):
                return PlainTextResponse("Forbidden", status_code=403)
            return await endpoint(request)

    async def keys(request: Request) -> Response:
        return await write(request, ui_keys)

    async def upload(request: Request) -> Response:
        auth = await authorized(request)
        if auth is None:
            return PlainTextResponse("Unauthorized", status_code=401)
        config, _session_id, csrf_token = auth
        with browser_setup_scope(config, csrf_token):
            # A multipart form may spool the file while parsing. Run the
            # model-key gate before parsing anything, then let ui_upload
            # produce the same deterministic user-facing explanation.
            from wingman.webui import oauth_upload_ready

            if not oauth_upload_ready(config):
                return await ui_upload(request)
            form = await request.form()
            presented = form.get("_csrf")
            if not isinstance(presented, str) or not secrets.compare_digest(presented, csrf_token):
                return PlainTextResponse("Forbidden", status_code=403)
            return await ui_upload(request)

    async def logout(request: Request) -> Response:
        auth = await authorized(request)
        if auth is None:
            return PlainTextResponse("Unauthorized", status_code=401)
        config, session_id, csrf_token = auth
        with browser_setup_scope(config, csrf_token):
            form = await request.form()
            presented = form.get("_csrf")
            if not isinstance(presented, str) or not secrets.compare_digest(presented, csrf_token):
                return PlainTextResponse("Forbidden", status_code=403)
            sessions.remove(session_id)
        response = RedirectResponse(sessions.public_path("/login"), status_code=303)
        response.delete_cookie(_COOKIE, path=sessions.public_path("/setup"))
        return response

    app.router.routes[0:0] = [
        Route(f"{prefix}/login", login, methods=["GET"]),
        Route(f"{prefix}/oauth/callback", callback, methods=["GET"]),
        Route(f"{prefix}/setup", setup, methods=["GET"]),
        Route(f"{prefix}/setup/", setup, methods=["GET"]),
        Route(f"{prefix}/setup/keys", keys, methods=["POST"]),
        Route(f"{prefix}/setup/upload", upload, methods=["POST"]),
        Route(f"{prefix}/setup/logout", logout, methods=["POST"]),
    ]


__all__ = [
    "OAuthBrowserSessions",
    "OAuthBrowserSettings",
    "bind_oauth_browser",
    "build_oauth_browser_settings",
]
