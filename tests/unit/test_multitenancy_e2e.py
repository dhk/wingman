"""End-to-end synthesis (RFC-048 Phase 1e): two tenants sharing one
process — registry, ASGI token routing, webui key forms, and provider
key resolution — proving the exact cross-tenant leak Phase 0 exists to
prevent never happens once the pieces are wired together for real."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import wingman.webui as webui_module
from wingman.infrastructure.config import ENV_DATA_DIR
from wingman.infrastructure.keys import read_workspace_keys
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import Tenant, TenantIndex
from wingman.mcp_server import server
from wingman.providers.base import CapabilityClass
from wingman.providers.router import get_provider
from wingman.webui import configure_tenant_index, register_ui

_MODELS_TOML = '[models.extract_fast]\nprovider = "anthropic"\nmodel = "claude-x"\n'


@pytest.fixture(autouse=True)
def _reset_tenant_index() -> Iterator[None]:
    yield
    configure_tenant_index(None)


def _make_tenant(tmp_path: Path, slug: str, token: str) -> Tenant:
    data_dir = tmp_path / slug
    for directory in (data_dir, data_dir / "inbox", data_dir / "reports"):
        directory.mkdir(parents=True)
    Storage(data_dir / "wingman.db").close()
    (data_dir / "mcp-http-token").write_text(token, encoding="utf-8")
    (data_dir / "models.toml").write_text(_MODELS_TOML, encoding="utf-8")
    return Tenant(slug=slug, data_dir=data_dir)


def test_two_tenants_key_forms_never_cross_contaminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(webui_module.VALIDATORS, "anthropic", lambda value: None)  # accept any key

    jason = _make_tenant(tmp_path, "jason", "tok-jason-e2e")
    bob = _make_tenant(tmp_path, "bob", "tok-bob-e2e")
    configure_tenant_index(TenantIndex([jason, bob]))
    register_ui(server, prefix="/e2e-keys")
    client = TestClient(server.streamable_http_app())

    # Each tenant submits their OWN key through their OWN form.
    jason_response = client.post(
        "/e2e-keys/ui/tok-jason-e2e/keys", data={"anthropic": "sk-ant-jason-own"}
    )
    bob_response = client.post(
        "/e2e-keys/ui/tok-bob-e2e/keys", data={"anthropic": "sk-ant-bob-own"}
    )
    assert "verified and live now" in jason_response.text
    assert "verified and live now" in bob_response.text

    # Each tenant's own workspace file has exactly its own key.
    assert read_workspace_keys(jason.data_dir) == {"ANTHROPIC_API_KEY": "sk-ant-jason-own"}
    assert read_workspace_keys(bob.data_dir) == {"ANTHROPIC_API_KEY": "sk-ant-bob-own"}

    # Neither submission touched the shared process's environment at all —
    # the exact cross-tenant leak Phase 0 (keys.store_workspace_key) closed.
    assert "ANTHROPIC_API_KEY" not in os.environ

    # A provider built for each tenant's OWN Config gets ITS OWN key, never
    # the other tenant's and never a process-env fallback (strict mode).
    jason_provider = get_provider(CapabilityClass.EXTRACT_FAST, jason.config())
    bob_provider = get_provider(CapabilityClass.EXTRACT_FAST, bob.config())
    assert jason_provider._client.api_key == "sk-ant-jason-own"
    assert bob_provider._client.api_key == "sk-ant-bob-own"


def test_tenant_with_no_key_never_inherits_a_sibling_tenants_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if a shared-process ANTHROPIC_API_KEY somehow got set (e.g. by
    an operator mistake, or a leftover CLI export), a tenant with no key
    of their own must fail loud, never silently borrow it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leftover-from-somewhere")
    jason = _make_tenant(tmp_path, "jason", "tok-jason-noleak")
    bob = _make_tenant(tmp_path, "bob", "tok-bob-noleak")  # never configures a key
    configure_tenant_index(TenantIndex([jason, bob]))

    from wingman.infrastructure.keys import store_workspace_key
    from wingman.providers.base import ProviderError

    store_workspace_key(jason.data_dir, "anthropic", "sk-ant-jason-real")

    jason_provider = get_provider(CapabilityClass.EXTRACT_FAST, jason.config())
    assert jason_provider._client.api_key == "sk-ant-jason-real"
    with pytest.raises(ProviderError, match="no Anthropic key configured"):
        get_provider(CapabilityClass.EXTRACT_FAST, bob.config())


def test_env_override_only_affects_single_tenant_mode_not_registered_tenants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config(env=...) is the ordinary CLI/test escape hatch (Phase
    1b) — it must never accidentally resolve to a REGISTERED tenant's
    workspace just because that tenant's data_dir happens to match."""
    jason = _make_tenant(tmp_path, "jason", "tok-jason-envcheck")
    configure_tenant_index(TenantIndex([jason]))
    from wingman.infrastructure.config import load_config

    config = load_config(env={ENV_DATA_DIR: str(jason.data_dir)})
    assert config.data_dir == jason.data_dir
    assert config.strict_provider_keys is False  # env path, not tenant.config()'s strict path
