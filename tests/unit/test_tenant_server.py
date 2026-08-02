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
