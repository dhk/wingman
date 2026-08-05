"""The shared multi-tenant server: --tenant-registry argparse validation,
and the tenant_url MCP tool's operator-only gate (RFC-048, #209)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wingman.infrastructure.storage import Storage
from wingman.mcp_server import main
from wingman.mcp_server import tenant_url as tenant_url_tool


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "ws"
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(data_dir))
    return data_dir


def _make_tenant(tmp_path: Path, slug: str, token: str) -> Path:
    data_dir = tmp_path / slug
    data_dir.mkdir()
    Storage(data_dir / "wingman.db").close()
    (data_dir / "mcp-http-token").write_text(token, encoding="utf-8")
    return data_dir


def _write_registry(tmp_path: Path, *entries: tuple[str, Path]) -> Path:
    registry = tmp_path / "tenants.toml"
    lines = []
    for slug, data_dir in entries:
        lines += ["[[tenant]]", f'slug = "{slug}"', f'data_dir = "{data_dir}"', ""]
    registry.write_text("\n".join(lines), encoding="utf-8")
    return registry


def test_tenant_registry_requires_http(workspace: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--tenant-registry", "/tmp/whatever.toml"])


def test_tenant_registry_rejects_rotate_token(workspace: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--http", "--tenant-registry", "/tmp/whatever.toml", "--rotate-token"])


def test_tenant_url_tool_refuses_from_a_tenant_session(tmp_path: Path) -> None:
    """The core leak this tool must never have: a tenant's own scoped
    Config (strict_provider_keys=True by construction) must never be
    able to successfully call this operator-only tool."""
    from wingman.infrastructure.config import tenant_config_scope
    from wingman.infrastructure.tenants import Tenant

    tenant = Tenant(slug="jason", data_dir=tmp_path / "jason")
    with tenant_config_scope(tenant.config()):
        result = tenant_url_tool("jason")
    assert "operator-only" in result


def test_tenant_url_tool_works_outside_tenant_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WINGMAN_DATA_DIR", raising=False)
    data_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    registry = _write_registry(tmp_path, ("jason", data_dir))
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    result = tenant_url_tool("jason", port=9921)
    assert "tok-jason" in result
    assert "9921" in result


def test_tenant_url_tool_defaults_connector_name_to_wingman_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #253: the MCP tool auto-derives 'wingman-<slug>' with no
    argument needed, matching the CLI's own default."""
    monkeypatch.delenv("WINGMAN_DATA_DIR", raising=False)
    data_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    registry = _write_registry(tmp_path, ("jason", data_dir))
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    result = tenant_url_tool("jason", port=9921)
    assert "claude mcp add --transport http wingman-jason " in result


def test_tenant_url_tool_reports_unknown_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WINGMAN_DATA_DIR", raising=False)
    registry = _write_registry(tmp_path)  # empty
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    result = tenant_url_tool("nobody")
    assert "No tenant 'nobody'" in result


# --- tenant_urls (plural, #238's carve-off follow-up) ----------------------


def test_tenant_urls_tool_refuses_from_a_tenant_session(tmp_path: Path) -> None:
    from wingman.infrastructure.config import tenant_config_scope
    from wingman.infrastructure.tenants import Tenant
    from wingman.mcp_server import tenant_urls as tenant_urls_tool

    tenant = Tenant(slug="jason", data_dir=tmp_path / "jason")
    with tenant_config_scope(tenant.config()):
        result = tenant_urls_tool()
    assert "operator-only" in result


def test_tenant_urls_tool_with_slug_matches_tenant_url_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import tenant_urls as tenant_urls_tool

    monkeypatch.delenv("WINGMAN_DATA_DIR", raising=False)
    data_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    registry = _write_registry(tmp_path, ("jason", data_dir))
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    singular = tenant_url_tool("jason", port=9921)
    plural = tenant_urls_tool("jason", port=9921)
    assert singular == plural


def test_tenant_urls_tool_with_unknown_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import tenant_urls as tenant_urls_tool

    monkeypatch.delenv("WINGMAN_DATA_DIR", raising=False)
    registry = _write_registry(tmp_path)  # empty
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    result = tenant_urls_tool("nobody")
    assert "No tenant 'nobody'" in result


def test_tenant_urls_tool_without_slug_lists_every_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import tenant_urls as tenant_urls_tool

    monkeypatch.delenv("WINGMAN_DATA_DIR", raising=False)
    connected = _make_tenant(tmp_path, "jason", "tok-jason")
    not_connected = tmp_path / "bob"
    not_connected.mkdir()
    Storage(not_connected / "wingman.db").close()  # no token file written
    registry = _write_registry(tmp_path, ("jason", connected), ("bob", not_connected))
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    result = tenant_urls_tool(port=9921)
    assert "jason:" in result
    assert "tok-jason" in result
    assert "bob: not yet connected (no token minted)" in result


def test_tenant_urls_tool_without_slug_derives_a_name_per_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #253: the roster path can't apply one fixed connector name to
    every tenant, so each gets its own 'wingman-<slug>' automatically."""
    from wingman.mcp_server import tenant_urls as tenant_urls_tool

    monkeypatch.delenv("WINGMAN_DATA_DIR", raising=False)
    jason = _make_tenant(tmp_path, "jason", "tok-jason")
    bob = _make_tenant(tmp_path, "bob", "tok-bob")
    registry = _write_registry(tmp_path, ("jason", jason), ("bob", bob))
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    result = tenant_urls_tool()
    assert "claude mcp add --transport http wingman-jason " in result
    assert "claude mcp add --transport http wingman-bob " in result


def test_tenant_urls_tool_without_slug_and_empty_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import tenant_urls as tenant_urls_tool

    monkeypatch.delenv("WINGMAN_DATA_DIR", raising=False)
    registry = _write_registry(tmp_path)  # empty
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    result = tenant_urls_tool()
    assert "No tenants in the registry" in result


def test_tenant_registry_path_resolves_from_host_setting(tmp_path: Path) -> None:
    from wingman.infrastructure.tenants import DEFAULT_REGISTRY_PATH, tenant_registry_path

    home = tmp_path / "home"
    assert tenant_registry_path(home) == DEFAULT_REGISTRY_PATH  # no override set

    settings_dir = home / ".config" / "wingman"
    settings_dir.mkdir(parents=True)
    (settings_dir / "wingman.env").write_text(
        f'WINGMAN_TENANT_REGISTRY="{tmp_path / "custom-tenants.toml"}"\n', encoding="utf-8"
    )
    assert tenant_registry_path(home) == tmp_path / "custom-tenants.toml"
