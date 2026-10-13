"""#618: manual binding covers both configured surfaces, read from service config.

One WorkOS user has the same full `sub` under the browser-session issuer and
the AuthKit MCP issuer (#582). `oauth-bind` and `oauth-approve` therefore bind
the given subject under every issuer the shared service trusts, and that set
is read from the service's own OAuth env files — never from a flag. If the
files cannot be read, or the given issuer is not one of them, nothing is bound.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure import oauth_service_issuers
from wingman.infrastructure.oauth_bearer import (
    IdentityMap,
    bind_trusted_identity,
    reserve_trusted_identity,
)
from wingman.infrastructure.oauth_onboarding import (
    OAuthOnboardingStore,
    onboarding_path_for,
)
from wingman.infrastructure.oauth_service_issuers import (
    ServiceIssuersError,
    read_service_issuers,
)
from wingman.infrastructure.tenants import load_registry

cli = CliRunner()
MCP_ISSUER = "https://example-staging.authkit.app"
CLIENT_ID = "client_01EXAMPLE"
BROWSER_ISSUER = f"https://api.workos.com/user_management/{CLIENT_ID}"
SUB = "user_01PRIVATESUBJECTVALUE"
SECRET = "sk_test_NEVER_PRINT_THIS_SECRET"
SERVICE_USER = pwd.getpwuid(os.getuid()).pw_name


def _write_oauth_env(path: Path, issuer: str = MCP_ISSUER) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"WINGMAN_OAUTH_ISSUER={issuer}\n"
        "WINGMAN_OAUTH_AUDIENCE=https://wingman.example/shared/mcp\n"
        f"WINGMAN_OAUTH_JWKS_URI={issuer}/oauth2/jwks\n"
        "WINGMAN_OAUTH_IDENTITIES=/home/wingman-shared/.config/wingman/oauth-identities.toml\n"
        f"WINGMAN_OAUTH_ARGS=--oauth-issuer {issuer} "
        "--oauth-audience https://wingman.example/shared/mcp "
        f"--oauth-jwks-uri {issuer}/oauth2/jwks "
        "--oauth-identities /home/wingman-shared/.config/wingman/oauth-identities.toml\n",
        encoding="utf-8",
    )
    return path


def _write_oauth_web_env(path: Path, client_id: str = CLIENT_ID) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"WINGMAN_OAUTH_WEB_CLIENT_SECRET={SECRET}\n"
        f"WINGMAN_OAUTH_WEB_ARGS=--oauth-web-client-id {client_id} "
        "--oauth-web-authorize-url https://api.workos.com/user_management/authorize "
        "--oauth-web-token-url https://api.workos.com/user_management/authenticate "
        "--oauth-web-redirect-uri https://wingman.example/shared/oauth/callback\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def service_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The default locations, as root sees them on the box (no flags passed)."""
    etc = tmp_path / "etc-wingman"
    oauth = _write_oauth_env(etc / "oauth.env")
    web = _write_oauth_web_env(etc / "oauth-web.env")
    monkeypatch.setattr(oauth_service_issuers, "OAUTH_CONFIG_POINTER", etc / "oauth-config-path")
    monkeypatch.setattr(oauth_service_issuers, "DEFAULT_OAUTH_CONFIG_PATH", oauth)
    monkeypatch.setattr(oauth_service_issuers, "DEFAULT_OAUTH_WEB_CONFIG_PATH", web)
    return etc


def _registry(tmp_path: Path, *slugs: str) -> Path:
    path = tmp_path / "tenants.toml"
    rows: list[str] = []
    for slug in slugs:
        data_dir = tmp_path / slug
        data_dir.mkdir()
        rows.extend(("[[tenant]]", f'slug = "{slug}"', f'data_dir = "{data_dir}"', ""))
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


def _bind(
    slug: str, identities: Path, registry: Path | None, *extra: str, issuer: str = BROWSER_ISSUER
) -> list[str]:
    args = [
        "tenant",
        "oauth-bind",
        slug,
        "--issuer",
        issuer,
        "--subject",
        SUB,
        "--identities",
        str(identities),
    ]
    if registry is not None:
        args += ["--registry", str(registry)]
    return [*args, *extra]


@pytest.fixture(autouse=True)
def _no_live_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wingman.infrastructure.tenant_process.signal_reload", lambda _path: None)


# --- reading the service's trusted issuers ------------------------------------


def test_reads_both_trusted_issuers_from_the_service_env_files(service_config: Path) -> None:
    issuers = read_service_issuers()

    assert issuers.all == (MCP_ISSUER, BROWSER_ISSUER)


def test_the_saved_config_pointer_names_the_oauth_file(
    service_config: Path, tmp_path: Path
) -> None:
    moved = _write_oauth_env(tmp_path / "elsewhere" / "oauth.env", "https://moved.authkit.app")
    (service_config / "oauth-config-path").write_text(f"{moved}\n", encoding="utf-8")

    assert read_service_issuers().all == ("https://moved.authkit.app", BROWSER_ISSUER)


def test_without_a_browser_login_only_the_mcp_issuer_is_trusted(service_config: Path) -> None:
    (service_config / "oauth-web.env").unlink()

    assert read_service_issuers().all == (MCP_ISSUER,)


def test_a_missing_oauth_config_is_refused_as_not_found(service_config: Path) -> None:
    (service_config / "oauth.env").unlink()

    with pytest.raises(ServiceIssuersError, match="not found"):
        read_service_issuers()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_an_unreadable_web_config_is_refused_not_treated_as_absent(service_config: Path) -> None:
    web = service_config / "oauth-web.env"
    web.chmod(0)
    try:
        with pytest.raises(ServiceIssuersError, match="permission denied") as caught:
            read_service_issuers()
    finally:
        web.chmod(0o600)
    assert "not found" not in str(caught.value)


def test_a_malformed_web_config_never_echoes_the_secret_or_other_lines(
    service_config: Path,
) -> None:
    web = service_config / "oauth-web.env"
    web.write_text(
        f"WINGMAN_OAUTH_WEB_CLIENT_SECRET={SECRET}\n"
        f"WINGMAN_OAUTH_WEB_ARGS=--oauth-web-token-url https://x.example {SECRET}\n",
        encoding="utf-8",
    )

    with pytest.raises(ServiceIssuersError) as caught:
        read_service_issuers()

    assert SECRET not in str(caught.value)
    assert "--oauth-web-client-id" in str(caught.value)


def test_disagreeing_issuer_keys_are_refused(service_config: Path) -> None:
    oauth = service_config / "oauth.env"
    oauth.write_text(
        oauth.read_text(encoding="utf-8").replace(
            f"WINGMAN_OAUTH_ISSUER={MCP_ISSUER}", "WINGMAN_OAUTH_ISSUER=https://other.example"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ServiceIssuersError, match="disagree"):
        read_service_issuers()


def test_quoted_values_are_read_as_systemd_reads_them(service_config: Path) -> None:
    oauth = service_config / "oauth.env"
    oauth.write_text(
        f'# comment\nWINGMAN_OAUTH_ARGS="--oauth-issuer {MCP_ISSUER} --oauth-audience x"\n',
        encoding="utf-8",
    )

    assert read_service_issuers().mcp == MCP_ISSUER


# --- oauth-bind ---------------------------------------------------------------


def test_existing_tenant_migration_binds_both_surfaces_in_one_command(
    service_config: Path, tmp_path: Path
) -> None:
    registry = _registry(tmp_path, "trent")
    identities = tmp_path / "oauth-identities.toml"

    result = cli.invoke(app, _bind("trent", identities, registry))

    assert result.exit_code == 0, result.output
    assert IdentityMap.from_toml(identities).entries() == {
        (BROWSER_ISSUER, SUB): "trent",
        (MCP_ISSUER, SUB): "trent",
    }
    assert MCP_ISSUER in result.output and BROWSER_ISSUER in result.output
    assert SUB not in result.output


def test_bind_clears_pending_rows_on_both_surfaces(service_config: Path, tmp_path: Path) -> None:
    registry = _registry(tmp_path, "trent")
    identities = tmp_path / "oauth-identities.toml"
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    store.record_pending(MCP_ISSUER, SUB, now=1.0)
    store.record_pending(BROWSER_ISSUER, SUB, now=2.0)
    store.record_pending(MCP_ISSUER, "someone_else", now=3.0)

    result = cli.invoke(app, _bind("trent", identities, registry, issuer=MCP_ISSUER))

    assert result.exit_code == 0, result.output
    assert [(row.issuer, row.subject) for row in store.pending()] == [(MCP_ISSUER, "someone_else")]


def test_rerunning_a_single_surface_binding_adds_the_other_and_then_is_idempotent(
    service_config: Path, tmp_path: Path
) -> None:
    registry = _registry(tmp_path, "trent")
    identities = tmp_path / "oauth-identities.toml"
    bind_trusted_identity(identities, BROWSER_ISSUER, SUB, "trent")  # pre-#618 state

    first = cli.invoke(app, _bind("trent", identities, registry))
    before = identities.read_text(encoding="utf-8")
    second = cli.invoke(app, _bind("trent", identities, registry))

    assert first.exit_code == 0, first.output
    assert IdentityMap.from_toml(identities).slug_for(MCP_ISSUER, SUB) == "trent"
    assert second.exit_code == 0, second.output
    assert "already existed" in second.output
    assert identities.read_text(encoding="utf-8") == before


def test_bind_refuses_when_the_other_surface_belongs_to_another_tenant(
    service_config: Path, tmp_path: Path
) -> None:
    registry = _registry(tmp_path, "trent", "someone")
    identities = tmp_path / "oauth-identities.toml"
    bind_trusted_identity(identities, MCP_ISSUER, SUB, "someone")
    before = identities.read_text(encoding="utf-8")

    result = cli.invoke(app, _bind("trent", identities, registry))

    assert result.exit_code == 1
    assert "already bound to 'someone'" in result.output
    assert identities.read_text(encoding="utf-8") == before  # neither binding written


def test_bind_refuses_an_issuer_the_service_does_not_trust(
    service_config: Path, tmp_path: Path
) -> None:
    registry = _registry(tmp_path, "trent")
    identities = tmp_path / "oauth-identities.toml"

    result = cli.invoke(
        app, _bind("trent", identities, registry, issuer="https://attacker.example")
    )

    assert result.exit_code == 1
    assert "not one of the issuers the shared service trusts" in result.output
    assert not identities.exists()


def test_bind_refuses_when_the_service_config_cannot_be_found(
    service_config: Path, tmp_path: Path
) -> None:
    (service_config / "oauth.env").unlink()
    registry = _registry(tmp_path, "trent")
    identities = tmp_path / "oauth-identities.toml"

    result = cli.invoke(app, _bind("trent", identities, registry))

    assert result.exit_code == 1
    assert "nothing was bound" in result.output
    assert not identities.exists()


def test_explicit_config_paths_override_the_defaults(tmp_path: Path) -> None:
    oauth = _write_oauth_env(tmp_path / "odd" / "oauth.env")
    web = _write_oauth_web_env(tmp_path / "odd" / "oauth-web.env")
    registry = _registry(tmp_path, "trent")
    identities = tmp_path / "oauth-identities.toml"

    result = cli.invoke(
        app,
        _bind(
            "trent",
            identities,
            registry,
            "--oauth-config",
            str(oauth),
            "--oauth-web-config",
            str(web),
        ),
    )

    assert result.exit_code == 0, result.output
    assert len(IdentityMap.from_toml(identities)) == 2


def test_preflight_reports_both_surfaces_without_writing(
    service_config: Path, tmp_path: Path
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    bind_trusted_identity(identities, BROWSER_ISSUER, SUB, "trent")
    before = identities.read_text(encoding="utf-8")

    result = cli.invoke(app, _bind("trent", identities, None, "--preflight"))

    assert result.exit_code == 0, result.output
    assert f"would bind under {MCP_ISSUER}" in result.output
    assert f"already bound under {BROWSER_ISSUER}" in result.output
    assert identities.read_text(encoding="utf-8") == before


def test_provisioning_reservation_covers_and_binds_both_surfaces(
    service_config: Path, tmp_path: Path
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    reserved = cli.invoke(app, _bind("newbie", identities, None, "--reserve"))
    assert reserved.exit_code == 0, reserved.output
    token = reserved.output.strip()

    # A competing provisioning run cannot take the OTHER surface meanwhile.
    with pytest.raises(Exception, match="reserved for provisioning tenant 'newbie'"):
        reserve_trusted_identity(identities, MCP_ISSUER, SUB, "other")

    registry = _registry(tmp_path, "newbie")
    bound = cli.invoke(app, _bind("newbie", identities, registry, "--reservation", token))

    assert bound.exit_code == 0, bound.output
    assert IdentityMap.from_toml(identities).entries() == {
        (BROWSER_ISSUER, SUB): "newbie",
        (MCP_ISSUER, SUB): "newbie",
    }
    assert sorted(p.name for p in tmp_path.glob(".oauth-identities.toml.reservation-*")) == []


# --- oauth-approve --------------------------------------------------------------


def _invite_and_queue(identities: Path, registry: Path, issuer: str) -> OAuthOnboardingStore:
    reserved = cli.invoke(
        app,
        [
            "tenant",
            "oauth-invite",
            "pat",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )
    assert reserved.exit_code == 0, reserved.output
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    store.record_pending(issuer, SUB, now=1.0)
    return store


def _approve(identities: Path, registry: Path, data_root: Path, issuer: str) -> list[str]:
    return [
        "tenant",
        "oauth-approve",
        "pat",
        "--issuer",
        issuer,
        "--subject",
        SUB,
        "--identities",
        str(identities),
        "--registry",
        str(registry),
        "--data-root",
        str(data_root),
        "--service-user",
        SERVICE_USER,
    ]


def test_manual_approval_binds_both_surfaces_and_clears_both_queues(
    service_config: Path, tmp_path: Path
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    registry = tmp_path / "tenants.toml"
    registry.write_text("", encoding="utf-8")
    store = _invite_and_queue(identities, registry, BROWSER_ISSUER)
    store.record_pending(MCP_ISSUER, SUB, now=2.0)

    result = cli.invoke(app, _approve(identities, registry, tmp_path / "tenants", BROWSER_ISSUER))

    assert result.exit_code == 0, result.output
    assert IdentityMap.from_toml(identities).entries() == {
        (BROWSER_ISSUER, SUB): "pat",
        (MCP_ISSUER, SUB): "pat",
    }
    assert store.pending() == []
    assert SUB not in result.output


def test_manual_approval_refuses_an_untrusted_issuer_before_any_change(
    service_config: Path, tmp_path: Path
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    registry = tmp_path / "tenants.toml"
    registry.write_text("", encoding="utf-8")
    _invite_and_queue(identities, registry, "https://attacker.example")

    result = cli.invoke(
        app, _approve(identities, registry, tmp_path / "tenants", "https://attacker.example")
    )

    assert result.exit_code == 1
    assert "not one of the issuers the shared service trusts" in result.output
    assert not (tmp_path / "tenants" / "pat").exists()
    assert load_registry(registry) == []


def test_manual_approval_refuses_when_service_config_is_missing(
    service_config: Path, tmp_path: Path
) -> None:
    (service_config / "oauth.env").unlink()
    identities = tmp_path / "oauth-identities.toml"
    registry = tmp_path / "tenants.toml"
    registry.write_text("", encoding="utf-8")
    _invite_and_queue(identities, registry, MCP_ISSUER)

    result = cli.invoke(app, _approve(identities, registry, tmp_path / "tenants", MCP_ISSUER))

    assert result.exit_code == 1
    assert "nothing was bound" in result.output
    assert not (tmp_path / "tenants" / "pat").exists()


def test_manual_approval_refuses_when_other_surface_is_another_tenants(
    service_config: Path, tmp_path: Path
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    registry = tmp_path / "tenants.toml"
    registry.write_text("", encoding="utf-8")
    store = _invite_and_queue(identities, registry, BROWSER_ISSUER)
    bind_trusted_identity(identities, MCP_ISSUER, SUB, "someone")

    result = cli.invoke(app, _approve(identities, registry, tmp_path / "tenants", BROWSER_ISSUER))

    assert result.exit_code == 1
    assert "already bound to 'someone'" in result.output
    assert IdentityMap.from_toml(identities).entries() == {(MCP_ISSUER, SUB): "someone"}
    assert load_registry(registry) == []
    assert [invite.slug for invite in store.invites()] == ["pat"]  # still approvable later
