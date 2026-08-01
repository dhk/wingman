"""webui.py under a shared multi-tenant process (RFC-048): token->tenant
resolution in '_authorized', and withholding the self-restart button/route
that RFC-041 designed around a now-absent per-account boundary."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import wingman.webui as webui_module
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import Tenant, TenantIndex
from wingman.mcp_server import _http_token, server
from wingman.webui import configure_tenant_index, register_ui


@pytest.fixture(autouse=True)
def _reset_tenant_index() -> Iterator[None]:
    yield
    configure_tenant_index(None)  # never leak tenant mode into other test files


def _make_tenant(tmp_path: Path, slug: str, token: str) -> Tenant:
    data_dir = tmp_path / slug
    for directory in (data_dir, data_dir / "inbox", data_dir / "reports"):
        directory.mkdir(parents=True)
    Storage(data_dir / "wingman.db").close()
    (data_dir / "mcp-http-token").write_text(token, encoding="utf-8")
    return Tenant(slug=slug, data_dir=data_dir)


def test_authorized_resolves_via_tenant_index_when_configured(tmp_path: Path) -> None:
    jason = _make_tenant(tmp_path, "jason", "tok-jason")
    bob = _make_tenant(tmp_path, "bob", "tok-bob")
    configure_tenant_index(TenantIndex([jason, bob]))
    register_ui(server, prefix="/idx-test-a")
    client = TestClient(server.streamable_http_app())

    jason_page = client.get("/idx-test-a/ui/tok-jason/")
    bob_page = client.get("/idx-test-a/ui/tok-bob/")
    assert jason_page.status_code == 200
    assert bob_page.status_code == 200
    # Each request resolved to ITS OWN workspace, not the other tenant's.
    unknown = client.get("/idx-test-a/ui/not-a-real-token/")
    assert unknown.status_code == 404


def test_authorized_falls_back_to_single_file_when_no_index_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (no 'configure_tenant_index' call) is exactly today's
    single-tenant behavior — the still-separate shape-B processes (dhk,
    trent) that stay on this model through the phased migration: this
    process's OWN 'WINGMAN_DATA_DIR'-resolved token file, not a registry."""
    assert webui_module._tenant_index is None
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    token = _http_token(config)
    register_ui(server, prefix="/idx-test-b")
    client = TestClient(server.streamable_http_app())
    assert client.get(f"/idx-test-b/ui/{token}/").status_code == 200
    assert client.get("/idx-test-b/ui/wrong-token/").status_code == 404


def test_restart_route_is_not_mounted_under_tenant_index(tmp_path: Path) -> None:
    tenant = _make_tenant(tmp_path, "jason", "tok-jason-restart")
    configure_tenant_index(TenantIndex([tenant]))
    register_ui(server, prefix="/idx-test-restart")
    client = TestClient(server.streamable_http_app())
    response = client.post("/idx-test-restart/ui/tok-jason-restart/restart")
    assert response.status_code == 404  # route was never mounted, not just refused


def test_restart_panel_explains_ops_action_instead_of_offering_the_button() -> None:
    configure_tenant_index(TenantIndex([]))
    panel = webui_module._restart_panel()
    assert "<form" not in panel  # no self-service button
    assert "operator action" in panel


def test_restart_handler_refuses_even_if_directly_reached(tmp_path: Path) -> None:
    """Second, independent layer of defense (belt and suspenders) beyond
    'register_ui' not mounting the route at all."""
    tenant = _make_tenant(tmp_path, "jason", "tok-jason-direct")
    configure_tenant_index(TenantIndex([tenant]))

    class _FakeRequest:
        path_params = {"token": "tok-jason-direct"}  # a genuinely valid token

    import asyncio

    response = asyncio.run(webui_module.ui_restart(_FakeRequest()))  # type: ignore[arg-type]
    assert response.status_code == 404
