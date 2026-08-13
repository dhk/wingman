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

from typing import Any

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from wingman.infrastructure.config import Config, tenant_config_scope
from wingman.infrastructure.tenants import Tenant, TenantIndex


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

    def __init__(self, inner: Any, index: TenantIndex) -> None:
        self._inner = inner
        self._index = index

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
        with tenant_config_scope(lambda: self._config_for(tenant)):
            await self._inner(scope, receive, send)

    def _config_for(self, resolved: Tenant) -> Config:
        """This tenant's Config as the registry stands right now.

        Falls back to the tenant resolved for this request if the slug has
        since left the registry — unreachable in practice, because the
        token check above is the access authority and already refuses a
        tenant that no longer exists. Falling back rather than raising
        keeps a mid-flight request from failing on a registry edit.
        """
        current = self._index.by_slug(resolved.slug)
        return (current or resolved).config()


def bind_tenant_routing(app: Starlette, path: str, index: TenantIndex) -> None:
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
            route.app = TenantRoutingASGIApp(route.app, index)
            return
    raise RuntimeError(
        f"no Starlette route at {path!r} to bind tenant routing to — "
        "did server.settings.streamable_http_path change?"
    )
