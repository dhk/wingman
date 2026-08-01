"""Per-request tenant routing at the ASGI layer (RFC-048).

Validates the one genuinely uncertain mechanism in the multi-tenant
design: that Starlette's own path-parameter resolution ('{token}' in a
Route's path) populates 'scope["path_params"]' before a wrapped raw ASGI
endpoint runs, so a hand-rolled wrapper can resolve the token to a
tenant and bind that tenant's Config for the request's duration with no
FastMCP-internal hook required.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from wingman.infrastructure.config import load_config
from wingman.infrastructure.tenant_asgi import TenantRoutingASGIApp, bind_tenant_routing
from wingman.infrastructure.tenants import Tenant, TenantIndex
from wingman.mcp_server import server


class _EchoConfigApp:
    """A minimal inner ASGI app standing in for FastMCP's real
    StreamableHTTPASGIApp — just enough to prove which tenant's Config
    was bound when it ran."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = PlainTextResponse(str(load_config().data_dir))
        await response(scope, receive, send)


def _make_tenant(tmp_path: Path, slug: str, token: str) -> Tenant:
    data_dir = tmp_path / slug
    data_dir.mkdir()
    (data_dir / "mcp-http-token").write_text(token, encoding="utf-8")
    return Tenant(slug=slug, data_dir=data_dir)


@pytest.fixture
def two_tenant_app(tmp_path: Path) -> tuple[Starlette, Tenant, Tenant]:
    jason = _make_tenant(tmp_path, "jason", "tok-jason")
    bob = _make_tenant(tmp_path, "bob", "tok-bob")
    index = TenantIndex([jason, bob])
    wrapped = TenantRoutingASGIApp(_EchoConfigApp(), index)
    app = Starlette(routes=[Route("/mcp/{token}", endpoint=wrapped)])
    return app, jason, bob


def test_request_resolves_to_the_matching_tenant(
    two_tenant_app: tuple[Starlette, Tenant, Tenant],
) -> None:
    app, jason, bob = two_tenant_app
    client = TestClient(app)
    assert client.get("/mcp/tok-jason").text == str(jason.data_dir)
    assert client.get("/mcp/tok-bob").text == str(bob.data_dir)


def test_unknown_token_is_rejected_before_reaching_the_inner_app(
    two_tenant_app: tuple[Starlette, Tenant, Tenant],
) -> None:
    app, _jason, _bob = two_tenant_app
    response = TestClient(app).get("/mcp/tok-nobody")
    assert response.status_code == 401


def test_empty_token_path_segment_is_rejected(
    two_tenant_app: tuple[Starlette, Tenant, Tenant],
) -> None:
    app, _jason, _bob = two_tenant_app
    # A trailing slash with nothing after it still matches Starlette's
    # {token} segment as an empty string on some route configs — confirm
    # it's rejected rather than accidentally matching every tenant.
    response = TestClient(app).get("/mcp/")
    assert response.status_code in (401, 404)


def test_concurrent_requests_never_cross_tenant_context(
    two_tenant_app: tuple[Starlette, Tenant, Tenant],
) -> None:
    """The end-to-end proof: two tenants' requests running concurrently
    through the real ASGI wrapper (not just the bare ContextVar) must
    never see each other's Config."""
    app, jason, bob = two_tenant_app

    async def run() -> tuple[str, str]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            jason_task = http.get("/mcp/tok-jason")
            bob_task = http.get("/mcp/tok-bob")
            jason_response, bob_response = await asyncio.gather(jason_task, bob_task)
            return jason_response.text, bob_response.text

    jason_seen, bob_seen = asyncio.run(run())
    assert jason_seen == str(jason.data_dir)
    assert bob_seen == str(bob.data_dir)


def test_bind_tenant_routing_wraps_a_real_fastmcp_app_without_disturbing_other_routes(
    tmp_path: Path,
) -> None:
    """Proves the intended production wiring: replacing the route inside
    a REAL 'server.streamable_http_app()' output, leaving every other
    route (the web UI's custom routes) untouched."""
    from wingman.webui import register_ui

    jason = _make_tenant(tmp_path, "jason", "tok-jason-real")
    index = TenantIndex([jason])

    register_ui(server)
    server.settings.streamable_http_path = "/mcp/{token}"
    app = server.streamable_http_app()
    bind_tenant_routing(app, "/mcp/{token}", index)

    client = TestClient(app)
    # A bad token must be rejected by OUR wrapper (401) — never delegated
    # into FastMCP's own session/message handling, which would otherwise
    # produce a different (JSON-RPC-shaped) error for a malformed request.
    assert client.get("/mcp/not-a-real-token").status_code == 401
    # Every other route FastMCP mounted (the UI) is completely unaffected —
    # this wrapper touched exactly one Route, not the whole app.
    assert client.get("/ui/wrong-token").status_code == 404  # webui's own gate, reached fine


def test_bind_tenant_routing_raises_on_a_path_mismatch(tmp_path: Path) -> None:
    app = Starlette(routes=[Route("/mcp/{token}", endpoint=_EchoConfigApp())])
    index = TenantIndex([])
    with pytest.raises(RuntimeError, match="no Starlette route"):
        bind_tenant_routing(app, "/some/other/path", index)
