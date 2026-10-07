"""Route one HTTP request to its tenant, at the ASGI layer (RFC-048).

FastMCP's own 'streamable_http_app()' bakes a single, fixed capability
token into 'server.settings.streamable_http_path' once at process
startup (mcp_server._http_token) and mounts exactly one Starlette Route
at that literal path — one process, one token, one route. For N tenants
sharing one process, the route becomes a path *parameter*,
'{prefix}/mcp/{token}', matching any token value, and this module
resolves which tenant a given request's token belongs to, replacing
"baked in once at startup" with "looked up per request."

FastMCP exposes no public hook to inject that lookup before its own
routing runs. Rather than subclass or monkeypatch FastMCP's internals
(which change shape across 'mcp' package versions), this wraps the
*Starlette Route's own '.app' attribute* after the fact —
'starlette.routing.Route.path'/'.endpoint'/'.app' is long-standing,
widely-depended-on public Starlette API (see 'Route.matches'/'.handle'
in starlette/routing.py), not an FastMCP-private detail. FastMCP itself
stays a black box that happens to produce a Starlette app; only that
app's public route shape is touched.
"""

from __future__ import annotations

import contextvars
import json
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from wingman.infrastructure.config import Config, tenant_config_scope
from wingman.infrastructure.tenants import Tenant, TenantIndex

#: (argv) -> stdout, or None if the command could not be run.
Capture = Callable[[list[str]], str | None]

_MCP_SESSION_ID = b"mcp-session-id"
_SESSION_BINDING_TTL_SECONDS = 1800.0
_MAX_SESSION_BINDINGS = 4096


def _session_id(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Return the one well-formed MCP session id, or reject ambiguity."""
    values = [
        bytes(value).decode("latin-1")
        for key, value in headers
        if bytes(key).lower() == _MCP_SESSION_ID
    ]
    if not values:
        return None
    if len(values) != 1 or not values[0] or values[0] != values[0].strip():
        raise ValueError("malformed MCP session id")
    return values[0]


class TenantSessionBindings:
    """Bind FastMCP session ids to the tenant that authenticated them.

    Both the capability and OAuth routes share one instance. FastMCP keeps a
    long-lived task per streamable-HTTP session; without this outer binding,
    a valid credential for tenant B could resume a task created under tenant
    A's context merely by presenting A's session id.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = _SESSION_BINDING_TTL_SECONDS,
        max_bindings: int = _MAX_SESSION_BINDINGS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_bindings <= 0:
            raise ValueError("session binding TTL and capacity must be positive")
        self._lock = threading.Lock()
        self._tenants: dict[str, tuple[str, float]] = {}
        self._ttl_seconds = ttl_seconds
        self._max_bindings = max_bindings
        self._monotonic = monotonic

    def _purge_expired(self, now: float) -> None:
        expired = [
            session_id
            for session_id, (_slug, last_seen) in self._tenants.items()
            if now - last_seen >= self._ttl_seconds
        ]
        for session_id in expired:
            del self._tenants[session_id]

    def authorize(self, session_id: str, slug: str) -> str:
        """Return ok, unknown, or other for a presented session id."""
        with self._lock:
            now = self._monotonic()
            self._purge_expired(now)
            binding = self._tenants.get(session_id)
            if binding is None:
                return "unknown"
            owner, _last_seen = binding
            if owner != slug:
                return "other"
            self._tenants[session_id] = (owner, now)
            return "ok"

    def bind(self, session_id: str, slug: str) -> str:
        """Return ok, other, or capacity while binding an issued session."""
        with self._lock:
            now = self._monotonic()
            self._purge_expired(now)
            binding = self._tenants.get(session_id)
            if binding is not None:
                owner, _last_seen = binding
                if owner != slug:
                    return "other"
                self._tenants[session_id] = (owner, now)
                return "ok"
            if len(self._tenants) >= self._max_bindings:
                return "capacity"
            self._tenants[session_id] = (slug, now)
            return "ok"

    def release(self, session_id: str, slug: str) -> None:
        """Forget only the authenticated tenant's successfully deleted session."""
        with self._lock:
            binding = self._tenants.get(session_id)
            if binding is not None and binding[0] == slug:
                del self._tenants[session_id]

    async def call(
        self,
        slug: str,
        inner: Any,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        try:
            presented = _session_id(list(scope.get("headers", [])))
        except ValueError:
            await PlainTextResponse("Malformed MCP session", status_code=400)(scope, receive, send)
            return
        if presented is not None:
            authorization = self.authorize(presented, slug)
            if authorization == "unknown":
                await PlainTextResponse("Unknown or expired MCP session", status_code=404)(
                    scope, receive, send
                )
                return
            if authorization == "other":
                await PlainTextResponse("MCP session belongs to another tenant", status_code=403)(
                    scope, receive, send
                )
                return

        blocked = False

        async def bind_response(message: dict[str, Any]) -> None:
            nonlocal blocked
            if blocked:
                return
            if message["type"] == "http.response.start":
                try:
                    issued = _session_id(list(message.get("headers", [])))
                except ValueError:
                    blocked = True
                    await PlainTextResponse("Malformed MCP session", status_code=500)(
                        scope, receive, send
                    )
                    return
                if issued is not None:
                    binding = self.bind(issued, slug)
                    if binding != "ok":
                        blocked = True
                        if binding == "capacity":
                            await PlainTextResponse(
                                "MCP session capacity reached; reconnect after an idle session expires",
                                status_code=503,
                            )(scope, receive, send)
                        else:
                            await PlainTextResponse(
                                "MCP session belongs to another tenant", status_code=403
                            )(scope, receive, send)
                        return
                status = int(message.get("status", 0))
                if (
                    presented is not None
                    and scope.get("method") == "DELETE"
                    and 200 <= status < 300
                ):
                    self.release(presented, slug)
            await send(message)

        await inner(scope, receive, bind_response)


def _default_capture(argv: list[str]) -> str | None:
    try:
        result = subprocess.run(  # fixed argv, no shell, hard timeout
            argv, capture_output=True, text=True, timeout=10.0, check=False
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        return None
    return result.stdout if result.returncode == 0 else None


def funnel_prefix(port: int, capture: Capture = _default_capture) -> str | None:
    """The path a Tailscale funnel mounts this port under, read from the
    LIVE funnel — or None if it cannot be determined.

    Ground truth rather than assumption, and the same discovery
    'wingman-register-service.sh' has done since #288 for the same reason:
    a route added or moved since provisioning is picked up instead of
    drifting silently. Ported here because a stripping front removes the
    prefix before this process sees it, so the request cannot report it and
    a derived URL 404s (#417).

    Never raises: no tailscale, daemon down, odd output, nothing mounted on
    this port — all mean None, and None makes the caller say it does not
    know rather than emit a wrong address.
    """
    raw = capture(["tailscale", "serve", "status", "--json"])
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    target = f"http://127.0.0.1:{port}"
    paths = sorted(
        path
        for host in (payload.get("Web") or {}).values()
        for path, handler in ((host or {}).get("Handlers") or {}).items()
        if (handler or {}).get("Proxy") == target
    )
    # '/' is a mount with no prefix; anything else is the prefix itself.
    # Several mounts for one port is ambiguous, and guessing which one a
    # given tenant came through would be exactly the wrong-URL failure.
    if len(paths) != 1:
        return None
    return "" if paths[0] == "/" else paths[0].rstrip("/")


def resolve_prefix(
    origin: RequestOrigin,
    headers: Mapping[str, str] | None = None,
    capture: Capture = _default_capture,
) -> str | None:
    """The path prefix this tenant's public URL needs, or None if unknown.

    Order: a front that announces itself, then the path we were given, then
    loopback (which needs none), then the live funnel. None means say so —
    a URL that 404s sends somebody hunting for a page that is not there,
    which is the failure this whole surface promised to avoid.
    """
    announced = (headers or {}).get("x-forwarded-prefix", "").strip()
    if announced:
        return "/" + announced.strip("/")
    if origin.prefix:
        return origin.prefix
    if origin.is_loopback:
        return ""
    if origin.local_port is None:
        return None
    return funnel_prefix(origin.local_port, capture=capture)


@dataclass(frozen=True)
class RequestOrigin:
    """Where this tenant actually reached us (#412).

    Derived from the request rather than from configuration, because the
    process does not know its own public address. It runs with no
    '--prefix'; '/shared' is a Tailscale funnel path nobody ever told it
    about, and the CLI's 'tenant url' only knows it because an operator
    types --tunnel-prefix. The tenant, however, arrived AT the real URL —
    so the request is the one authority that cannot drift from the
    deployment.
    """

    scheme: str
    authority: str
    #: Everything before '/mcp/<token>' in the path we were actually given.
    #: Usually EMPTY behind a stripping front — a Tailscale funnel mounted
    #: with '--set-path /shared' removes the prefix before proxying, so the
    #: process never sees it (#417). Never treat this as authoritative for
    #: a public URL; ask 'resolve_prefix' instead.
    prefix: str
    #: The capability token for legacy routes, or None for OAuth.
    token: str | None
    #: The port this process was reached on, from the ASGI scope. What the
    #: live funnel lookup matches against.
    local_port: int | None = None

    @property
    def is_loopback(self) -> bool:
        """A request that arrived on loopback needs no prefix — it is
        already the complete address."""
        host = self.authority.split(":")[0]
        return host in {"127.0.0.1", "::1", "localhost"}

    def mcp_url(self, prefix: str = "") -> str:
        suffix = "/mcp" if self.token is None else f"/mcp/{self.token}"
        return f"{self.scheme}://{self.authority}{prefix}{suffix}"

    def ui_url(self, prefix: str = "") -> str:
        if self.token is None:
            raise ValueError("OAuth requests do not have a capability-token web UI URL")
        return f"{self.scheme}://{self.authority}{prefix}/ui/{self.token}/"


def request_origin(scope: Scope, token: str | None) -> RequestOrigin | None:
    """The public base this request arrived on, or None if unreconstructable.

    Trusts the forwarded headers a reverse proxy sets, because the only
    front here is one the operator configured — and getting this wrong
    prints a URL that does not work, never one that grants anything. The
    token in the resulting URL is the same token the request already
    carried.
    """
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    authority = headers.get("x-forwarded-host") or headers.get("host")
    if not authority:
        return None
    scheme = headers.get("x-forwarded-proto") or scope.get("scheme") or "http"
    # The mount point is whatever precedes this route: a stripping front
    # (tailscale funnel --set-path /shared) leaves it in the raw path while
    # the app itself never sees a prefix.
    raw = scope.get("raw_path") or b""
    path = raw.decode("latin-1") if raw else scope.get("path", "")
    marker = f"/mcp/{token}" if token is not None else "/mcp"
    prefix = path.split(marker)[0] if marker in path else ""
    server = tuple(scope.get("server") or ())
    local_port = server[1] if len(server) > 1 and isinstance(server[1], int) else None
    return RequestOrigin(
        scheme=scheme, authority=authority, prefix=prefix, token=token, local_port=local_port
    )


_request_origin: contextvars.ContextVar[RequestOrigin | None] = contextvars.ContextVar(
    "wingman_request_origin", default=None
)


@contextmanager
def request_origin_scope(origin: RequestOrigin | None) -> Iterator[None]:
    """Bind where this request came from, for the length of the request."""
    marker = _request_origin.set(origin)
    try:
        yield
    finally:
        _request_origin.reset(marker)


def current_request_origin() -> RequestOrigin | None:
    """Where the caller reached us, or None outside an HTTP request."""
    return _request_origin.get()


def current_tenant_config(index: TenantIndex, resolved: Tenant) -> Config:
    """A tenant's Config as the registry stands right now (#404).

    Shared by every route that resolves a tenant, whatever the credential
    (capability token, or the OAuth bearer path in oauth_bearer.py), so the
    "look the slug up per call, fall back to the resolved tenant" rule lives
    in one place.
    """
    current = index.by_slug(resolved.slug)
    return (current or resolved).config()


class TenantRoutingASGIApp:
    """Wraps one Route's ASGI app: resolves the request's 'token' path
    parameter against a TenantIndex, binds the matching tenant's Config
    for the request's duration (via 'tenant_config_scope' — safe under
    concurrent asyncio requests, since a ContextVar is copied per task,
    never shared process-wide the way 'os.environ' would be), then
    delegates.

    A token that resolves to no tenant gets a plain 401 and is never
    delegated — an unrecognized token must never reach FastMCP's own
    session/message handling at all, let alone any tool body.
    """

    def __init__(
        self,
        inner: Any,
        index: TenantIndex,
        session_bindings: TenantSessionBindings | None = None,
    ) -> None:
        self._inner = inner
        self._index = index
        self._session_bindings = session_bindings or TenantSessionBindings()

    @property
    def inner(self) -> Any:
        """The app this wraps — for a second route (see oauth_bearer) that
        fronts the same MCP endpoint with a different credential."""
        return self._inner

    @property
    def session_bindings(self) -> TenantSessionBindings:
        """The guard shared by every credential route to this transport."""
        return self._session_bindings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._inner(scope, receive, send)
            return
        token = scope.get("path_params", {}).get("token", "")
        tenant = self._index.resolve(token)
        if tenant is None:
            response = PlainTextResponse("Not Found", status_code=401)
            await response(scope, receive, send)
            return
        # A RESOLVER, not tenant.config() (#404). Under streamable HTTP the
        # tool body does not run in this request's task — the transport
        # creates a session task at initialize and delivers later messages
        # into it — and a ContextVar is copied at task creation. Binding a
        # Config here froze it for the life of the session, so a registry
        # reload never reached an open one: 'privileged = true' plus a
        # SIGHUP changed nothing until the process restarted. Looking the
        # slug up per call reads the index that 'reload' mutates in place.
        with (
            tenant_config_scope(lambda: self._config_for(tenant)),
            request_origin_scope(request_origin(scope, token)),
        ):
            await self._session_bindings.call(tenant.slug, self._inner, scope, receive, send)

    def _config_for(self, resolved: Tenant) -> Config:
        """This tenant's Config as the registry stands right now.

        Falls back to the tenant resolved for this request if the slug has
        since left the registry — unreachable in practice, because the
        token check above is the access authority and already refuses a
        tenant that no longer exists. Falling back rather than raising
        keeps a mid-flight request from failing on a registry edit.
        """
        return current_tenant_config(self._index, resolved)


def bind_tenant_routing(
    app: Starlette,
    path: str,
    index: TenantIndex,
    *,
    session_bindings: TenantSessionBindings | None = None,
) -> None:
    """Replace the ASGI app mounted at 'path' with a tenant-resolving
    wrapper around whatever was originally mounted there.

    Raises if no route matches 'path' exactly — a startup-time failure,
    not a silent no-op, so a future change to
    'server.settings.streamable_http_path' that drifts out of sync with
    this call is caught immediately rather than quietly serving every
    request unauthenticated.
    """
    for route in app.routes:
        if isinstance(route, Route) and route.path == path:
            route.app = TenantRoutingASGIApp(route.app, index, session_bindings)
            return
    raise RuntimeError(
        f"no Starlette route at {path!r} to bind tenant routing to — "
        "did server.settings.streamable_http_path change?"
    )
