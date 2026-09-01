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


def test_load_registry_defaults_privileged_to_false_and_reads_an_explicit_true(
    tmp_path: Path,
) -> None:
    """RFC-068 (#271): an entry that says nothing is unprivileged. The
    default is what makes every registry written before this flag existed
    keep meaning what it meant."""
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        f'[[tenant]]\nslug = "dhk"\ndata_dir = "{tmp_path / "dhk"}"\nprivileged = true\n\n'
        f'[[tenant]]\nslug = "trent"\ndata_dir = "{tmp_path / "trent"}"\n',
        encoding="utf-8",
    )
    by_slug = {tenant.slug: tenant for tenant in load_registry(registry)}
    assert by_slug["dhk"].privileged is True
    assert by_slug["trent"].privileged is False


def test_load_registry_rejects_a_privileged_value_that_is_not_a_boolean(tmp_path: Path) -> None:
    """Refused, not coerced: truthiness would grant the flag to the string
    "false", and silent strictness would DENY an operator who wrote the
    quoted form and walked away believing they had granted it."""
    registry = tmp_path / "tenants.toml"
    for bad in ('"true"', "1", '"yes"'):
        registry.write_text(
            f'[[tenant]]\nslug = "dhk"\ndata_dir = "{tmp_path / "dhk"}"\nprivileged = {bad}\n',
            encoding="utf-8",
        )
        with pytest.raises(TenantRegistryError, match="malformed 'privileged'"):
            load_registry(registry)


def test_load_registry_refuses_a_box_wide_privileged_default(tmp_path: Path) -> None:
    """'[defaults] privileged = true' is the fail-open this flag exists to
    prevent, so it is not supported — and it is refused rather than ignored,
    because an operator who wrote it would otherwise believe a grant (or a
    revocation) had taken effect. Same for the bare top-level form."""
    registry = tmp_path / "tenants.toml"
    entry = f'[[tenant]]\nslug = "dhk"\ndata_dir = "{tmp_path / "dhk"}"\n'
    for header in ("[defaults]\nprivileged = true\n\n", "privileged = false\n\n"):
        registry.write_text(header + entry, encoding="utf-8")
        with pytest.raises(TenantRegistryError, match="one tenant at a time"):
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
    own workspace file — never process env, even when set. The shared
    issues key still prefers a tenant's OWN value here (BYOK); what #506
    changed is only what happens when they have none — see
    test_feature_request's declared-tier coverage."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-shared-process-env")
    monkeypatch.setenv("GITHUB_SHARED_ISSUES_KEY", "ghp-shared-process-env")
    data_dir = tmp_path / "jason"
    store_workspace_key(data_dir, "anthropic", "sk-ant-jasons-own")
    store_workspace_key(data_dir, "github", "ghp-jasons-own")
    tenant = Tenant(slug="jason", data_dir=data_dir)
    config = tenant.config()
    assert config.strict_provider_keys is True
    assert config.anthropic_api_key == "sk-ant-jasons-own"  # workspace file, not env
    assert config.voyage_api_key is None  # no workspace value for voyage
    assert config.github_shared_issues_key == "ghp-jasons-own"  # workspace file, not env


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


def _tenant_with_token(tmp_path: Path, slug: str, token: str) -> Tenant:
    data_dir = tmp_path / slug
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "mcp-http-token").write_text(token + "\n", encoding="utf-8")
    return Tenant(slug=slug, data_dir=data_dir)


def test_a_token_shared_by_two_tenants_resolves_to_neither(tmp_path: Path) -> None:
    """#327 — the isolation bug. The lookup was last-writer-wins, so a
    duplicated token silently handed one tenant the other's workspace, and
    which one depended on registry order.

    Fail closed: a token that identifies two people identifies nobody. Both
    lose it; each can be issued a fresh one.
    """
    shared = "same-token-in-two-places"
    first = _tenant_with_token(tmp_path, "jason", shared)
    second = _tenant_with_token(tmp_path, "taylor", shared)

    index = TenantIndex([first, second])

    assert index.resolve(shared) is None
    # ...and neither tenant has been dropped from the registry itself.
    assert {t.slug for t in index.tenants} == {"jason", "taylor"}


def test_a_third_tenant_cannot_reinstate_an_already_ambiguous_token(
    tmp_path: Path,
) -> None:
    """The obvious fix — pop the first claim when a second appears — lets a
    THIRD tenant carrying the same token write itself back in, because by
    then the entry is gone and looks unclaimed."""
    shared = "token-in-three-places"
    tenants = [
        _tenant_with_token(tmp_path, "jason", shared),
        _tenant_with_token(tmp_path, "taylor", shared),
        _tenant_with_token(tmp_path, "morgan", shared),
    ]

    assert TenantIndex(tenants).resolve(shared) is None


def test_one_duplicated_token_does_not_disable_everybody_else(tmp_path: Path) -> None:
    """Refusing the whole registry would take every other tenant offline over
    one duplicated file — a worse outcome than the bug."""
    shared = "duplicated"
    tenants = [
        _tenant_with_token(tmp_path, "jason", shared),
        _tenant_with_token(tmp_path, "taylor", shared),
        _tenant_with_token(tmp_path, "morgan", "morgans-own-token"),
    ]

    index = TenantIndex(tenants)

    assert index.resolve(shared) is None
    resolved = index.resolve("morgans-own-token")
    assert resolved is not None
    assert resolved.slug == "morgan"


# --------------------------------------------------------------------------
# An unreadable registry is a diagnosis, never a traceback and never empty
# (#411 — #401's bug, in the library every 'wingman tenant' command uses)
# --------------------------------------------------------------------------


def test_an_unreadable_registry_raises_a_registry_error_not_a_permission_error(
    tmp_path: Path,
) -> None:
    """'Path.exists()' RAISES on EACCES rather than returning False, so this
    threw a bare PermissionError at every operator outside the wingman
    group — a traceback naming nothing they can act on."""
    directory = tmp_path / "etc"
    directory.mkdir()
    registry = directory / "tenants.toml"
    _write_registry(registry, ("jason", tmp_path / "jason"))
    directory.chmod(0o000)
    try:
        with pytest.raises(TenantRegistryError) as caught:
            load_registry(registry)
    finally:
        directory.chmod(0o755)

    assert not isinstance(caught.value, PermissionError)


def test_the_unreadable_diagnosis_is_the_one_written_for_motd(tmp_path: Path) -> None:
    """The same 'permission_problem' text #401 left behind — reused, not
    copied — so both real causes stay named in one place: not in the group
    that owns /etc/wingman, or a session that predates being added to it."""
    directory = tmp_path / "etc"
    directory.mkdir()
    registry = directory / "tenants.toml"
    _write_registry(registry, ("jason", tmp_path / "jason"))
    directory.chmod(0o000)
    try:
        with pytest.raises(TenantRegistryError) as caught:
            load_registry(registry)
    finally:
        directory.chmod(0o755)

    message = str(caught.value)
    assert "permission denied" in message
    assert "predates" in message
    assert "id -nG" in message


def test_an_unreadable_registry_is_never_read_as_an_empty_one(tmp_path: Path) -> None:
    """The quiet half of the bug. An absent registry means zero tenants, a
    valid startup state — so had 'exists()' returned False here instead of
    raising, the shared process would have come up serving NOBODY and
    called it normal. Unreadable and absent are different answers."""
    directory = tmp_path / "etc"
    directory.mkdir()
    registry = directory / "tenants.toml"
    _write_registry(registry, ("jason", tmp_path / "jason"))
    directory.chmod(0o000)
    try:
        with pytest.raises(TenantRegistryError):
            loaded = load_registry(registry)
            assert loaded != [], "an unreadable registry was reported as no tenants"
    finally:
        directory.chmod(0o755)


def test_an_absent_registry_still_returns_no_tenants(tmp_path: Path) -> None:
    """Deliberate and unchanged: a shared process with nobody configured yet
    is a valid (if useless) startup state, not an error."""
    directory = tmp_path / "etc"
    directory.mkdir()

    assert load_registry(directory / "tenants.toml") == []
    assert load_registry(tmp_path / "no" / "such" / "dir" / "tenants.toml") == []
