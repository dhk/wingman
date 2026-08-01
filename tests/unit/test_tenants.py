"""The tenant registry for a shared multi-tenant process (RFC-048)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wingman.infrastructure.keys import store_workspace_key
from wingman.infrastructure.tenants import (
    Tenant,
    TenantIndex,
    TenantRegistryError,
    load_registry,
)


def _write_registry(path: Path, *entries: tuple[str, Path]) -> None:
    lines = []
    for slug, data_dir in entries:
        lines.append("[[tenant]]")
        lines.append(f'slug = "{slug}"')
        lines.append(f'data_dir = "{data_dir}"')
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def test_load_registry_absent_file_is_empty_not_an_error(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "nope.toml") == []


def test_load_registry_parses_entries(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    jason_dir = tmp_path / "jason"
    bob_dir = tmp_path / "bob"
    _write_registry(registry, ("jason", jason_dir), ("bob", bob_dir))
    tenants = load_registry(registry)
    assert {t.slug for t in tenants} == {"jason", "bob"}
    by_slug = {t.slug: t for t in tenants}
    assert by_slug["jason"].data_dir == jason_dir
    assert by_slug["bob"].data_dir == bob_dir


def test_load_registry_rejects_duplicate_slug(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    _write_registry(registry, ("jason", tmp_path / "a"), ("jason", tmp_path / "b"))
    with pytest.raises(TenantRegistryError, match="more than once"):
        load_registry(registry)


def test_load_registry_rejects_missing_slug(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    registry.write_text('[[tenant]]\ndata_dir = "/srv/x"\n', encoding="utf-8")
    with pytest.raises(TenantRegistryError, match="no 'slug'"):
        load_registry(registry)


def test_load_registry_rejects_missing_data_dir(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    registry.write_text('[[tenant]]\nslug = "jason"\n', encoding="utf-8")
    with pytest.raises(TenantRegistryError, match="no 'data_dir'"):
        load_registry(registry)


def test_load_registry_rejects_malformed_toml(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    registry.write_text("this is not [ valid toml", encoding="utf-8")
    with pytest.raises(TenantRegistryError, match="could not be parsed"):
        load_registry(registry)


def test_tenant_read_token_none_when_absent(tmp_path: Path) -> None:
    tenant = Tenant(slug="jason", data_dir=tmp_path / "jason")
    assert tenant.read_token() is None


def test_tenant_read_token_reads_the_workspace_token_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "jason"
    data_dir.mkdir()
    (data_dir / "mcp-http-token").write_text("tok-jason-abc\n", encoding="utf-8")
    tenant = Tenant(slug="jason", data_dir=data_dir)
    assert tenant.read_token() == "tok-jason-abc"


def test_tenant_config_is_strict_and_workspace_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-048: a per-tenant Config resolves keys ONLY from that tenant's
    own workspace file — never process env, even when set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-shared-process-env")
    data_dir = tmp_path / "jason"
    store_workspace_key(data_dir, "anthropic", "sk-ant-jasons-own")
    tenant = Tenant(slug="jason", data_dir=data_dir)
    config = tenant.config()
    assert config.strict_provider_keys is True
    assert config.anthropic_api_key == "sk-ant-jasons-own"  # workspace file, not env
    assert config.voyage_api_key is None  # no workspace value for voyage


def test_tenant_index_resolves_token_to_correct_tenant(tmp_path: Path) -> None:
    jason_dir = tmp_path / "jason"
    bob_dir = tmp_path / "bob"
    jason_dir.mkdir()
    bob_dir.mkdir()
    (jason_dir / "mcp-http-token").write_text("tok-jason", encoding="utf-8")
    (bob_dir / "mcp-http-token").write_text("tok-bob", encoding="utf-8")
    index = TenantIndex(
        [Tenant(slug="jason", data_dir=jason_dir), Tenant(slug="bob", data_dir=bob_dir)]
    )
    assert index.resolve("tok-jason").slug == "jason"  # type: ignore[union-attr]
    assert index.resolve("tok-bob").slug == "bob"  # type: ignore[union-attr]
    assert index.resolve("tok-nobody") is None
    assert index.resolve("") is None
    assert len(index) == 2


def test_tenant_index_ignores_a_tenant_with_no_token_yet(tmp_path: Path) -> None:
    data_dir = tmp_path / "jason"  # no mcp-http-token written
    index = TenantIndex([Tenant(slug="jason", data_dir=data_dir)])
    assert index.resolve("anything") is None
    assert len(index) == 1  # still known by slug, just not reachable by token yet


def test_tenant_index_reload_picks_up_a_rotated_token(tmp_path: Path) -> None:
    data_dir = tmp_path / "jason"
    data_dir.mkdir()
    token_path = data_dir / "mcp-http-token"
    token_path.write_text("tok-old", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    _write_registry(registry, ("jason", data_dir))

    index = TenantIndex.from_registry_path(registry)
    assert index.resolve("tok-old") is not None

    token_path.write_text("tok-new", encoding="utf-8")
    index.reload(registry)
    assert index.resolve("tok-old") is None  # old token revoked
    assert index.resolve("tok-new") is not None  # rotation took effect, no restart


def test_tenant_index_reload_picks_up_a_newly_added_tenant(tmp_path: Path) -> None:
    jason_dir = tmp_path / "jason"
    jason_dir.mkdir()
    (jason_dir / "mcp-http-token").write_text("tok-jason", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    _write_registry(registry, ("jason", jason_dir))

    index = TenantIndex.from_registry_path(registry)
    assert len(index) == 1

    bob_dir = tmp_path / "bob"
    bob_dir.mkdir()
    (bob_dir / "mcp-http-token").write_text("tok-bob", encoding="utf-8")
    _write_registry(registry, ("jason", jason_dir), ("bob", bob_dir))
    index.reload(registry)
    assert len(index) == 2
    assert index.resolve("tok-bob").slug == "bob"  # type: ignore[union-attr]
