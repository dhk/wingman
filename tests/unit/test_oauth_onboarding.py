from __future__ import annotations

import os
import pwd
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.oauth_onboarding import (
    OAuthOnboardingError,
    OAuthOnboardingStore,
    onboarding_path_for,
)

cli = CliRunner()
ISSUER = "https://idp.example.com"


def test_invite_pending_and_approval_create_an_unprivileged_unfunded_tenant(
    tmp_path: Path, monkeypatch
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    registry.write_text("", encoding="utf-8")
    data_root = tmp_path / "tenants"

    reserved = cli.invoke(
        app,
        [
            "tenant",
            "oauth-invite",
            "taylor",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )
    assert reserved.exit_code == 0, reserved.output

    store = OAuthOnboardingStore(onboarding_path_for(identities))
    assert store.record_pending(ISSUER, "user_taylor", now=100.0)

    pending = cli.invoke(app, ["tenant", "oauth-pending", "--identities", str(identities)])
    assert pending.exit_code == 0, pending.output
    assert "user_taylor" in pending.output
    assert "taylor" in pending.output

    monkeypatch.setattr("wingman.infrastructure.tenant_process.signal_reload", lambda _path: None)
    approved = cli.invoke(
        app,
        [
            "tenant",
            "oauth-approve",
            "taylor",
            "--issuer",
            ISSUER,
            "--subject",
            "user_taylor",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
            "--data-root",
            str(data_root),
            "--service-user",
            pwd.getpwuid(os.getuid()).pw_name,
        ],
    )
    assert approved.exit_code == 0, approved.output

    from wingman.infrastructure.config import load_config
    from wingman.infrastructure.oauth_bearer import IdentityMap, OAuthSettings, bind_oauth_routing
    from wingman.infrastructure.tenant_asgi import bind_tenant_routing
    from wingman.infrastructure.tenants import TenantIndex, load_registry

    tenants = load_registry(registry)
    assert [(tenant.slug, tenant.privileged, tenant.funded) for tenant in tenants] == [
        ("taylor", False, False)
    ]
    assert tenants[0].data_dir == data_root / "taylor"
    assert (data_root / "taylor" / "wingman.db").is_file()
    assert IdentityMap.from_toml(identities).slug_for(ISSUER, "user_taylor") == "taylor"
    assert store.pending() == []
    assert store.invites() == []

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    audience = "https://wingman.example/mcp"
    settings = OAuthSettings(ISSUER, audience, f"{ISSUER}/jwks")

    class EchoTenant:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await PlainTextResponse(str(load_config().data_dir))(scope, receive, send)

    tenant_index = TenantIndex.from_registry_path(registry)
    identity_map = IdentityMap.from_toml(identities)
    web = Starlette(routes=[Route("/mcp/{token}", endpoint=EchoTenant())])
    bind_tenant_routing(web, "/mcp/{token}", tenant_index)
    bind_oauth_routing(
        web,
        "/mcp/{token}",
        "/mcp",
        tenant_index,
        identity_map,
        settings,
        lambda _token: key.public_key(),
    )
    client = TestClient(web)

    def token(subject: str) -> str:
        return jwt.encode(
            {
                "iss": ISSUER,
                "aud": audience,
                "sub": subject,
                "exp": int(time.time()) + 300,
            },
            key,
            algorithm="RS256",
        )

    approved_request = client.get(
        "/mcp", headers={"Authorization": f"Bearer {token('user_taylor')}"}
    )
    second_identity = client.get(
        "/mcp", headers={"Authorization": f"Bearer {token('someone_else')}"}
    )
    assert approved_request.status_code == 200
    assert approved_request.text == str(data_root / "taylor")
    assert second_identity.status_code == 403
    assert str(data_root / "taylor") not in second_identity.text


def test_pending_reference_is_short_stable_and_reveals_nothing() -> None:
    import re

    from wingman.infrastructure.oauth_onboarding import pending_reference

    ref = pending_reference(ISSUER, "user_taylor")
    assert re.fullmatch(r"[A-Z2-7]{4}-[A-Z2-7]{4}", ref)
    assert ref == pending_reference(ISSUER, "user_taylor")
    assert ref != pending_reference("https://other.example", "user_taylor")
    assert ref != pending_reference(ISSUER, "user_taylo")
    assert "taylor" not in ref.lower()


def test_oauth_pending_lists_the_reference_and_first_sign_in(tmp_path: Path) -> None:
    from wingman.infrastructure.oauth_onboarding import pending_reference

    identities = tmp_path / "oauth-identities.toml"
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    assert store.record_pending(ISSUER, "user_taylor", now=100.0)
    assert store.record_pending(ISSUER, "user_taylor", now=200.0)

    result = cli.invoke(app, ["tenant", "oauth-pending", "--identities", str(identities)])

    assert result.exit_code == 0, result.output
    assert f"ref={pending_reference(ISSUER, 'user_taylor')}" in result.output
    assert "first_seen=1970-01-01T00:01:40+00:00" in result.output
    assert "last_seen=1970-01-01T00:03:20+00:00" in result.output
    assert "sign_ins=2" in result.output


def test_pending_queue_is_bounded_and_keeps_existing_entries(tmp_path: Path) -> None:
    store = OAuthOnboardingStore(tmp_path / "onboarding.json", pending_limit=2)

    assert store.record_pending(ISSUER, "first", now=1.0)
    assert store.record_pending(ISSUER, "second", now=2.0)
    assert not store.record_pending(ISSUER, "third", now=3.0)
    assert [item.subject for item in store.pending()] == ["first", "second"]


def test_discard_pending_removes_only_the_exact_identity(tmp_path: Path) -> None:
    store = OAuthOnboardingStore(tmp_path / "onboarding.json")
    assert store.record_pending(ISSUER, "first", now=1.0)
    assert store.record_pending("https://other.example", "first", now=2.0)

    assert store.discard_pending(ISSUER, "first")
    assert not store.discard_pending(ISSUER, "first")
    assert [(item.issuer, item.subject) for item in store.pending()] == [
        ("https://other.example", "first")
    ]


def test_disk_binding_wins_over_a_stale_live_map_when_recording_pending(tmp_path: Path) -> None:
    from wingman.infrastructure.oauth_bearer import bind_trusted_identity
    from wingman.infrastructure.oauth_onboarding import record_verified_pending

    identities = tmp_path / "identities.toml"
    bind_trusted_identity(identities, ISSUER, "already-approved", "taylor")
    store = OAuthOnboardingStore(onboarding_path_for(identities))

    assert not record_verified_pending(identities, store, ISSUER, "already-approved")
    assert store.pending() == []


def test_malformed_queue_row_is_rejected_as_an_onboarding_error(tmp_path: Path) -> None:
    path = tmp_path / "onboarding.json"
    path.write_text(
        '{"version": 1, "invites": [], "pending": [{"issuer": "only-one-field"}]}\n',
        encoding="utf-8",
    )

    with pytest.raises(OAuthOnboardingError, match="malformed pending entry"):
        OAuthOnboardingStore(path).pending()


def test_approval_reports_reload_as_signaled_not_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    registry.write_text("", encoding="utf-8")
    service_user = pwd.getpwuid(os.getuid()).pw_name
    reserve = cli.invoke(
        app,
        [
            "tenant",
            "oauth-invite",
            "taylor",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )
    assert reserve.exit_code == 0, reserve.output
    OAuthOnboardingStore(onboarding_path_for(identities)).record_pending(ISSUER, "user_taylor")
    monkeypatch.setattr("wingman.infrastructure.tenant_process.signal_reload", lambda _path: 42)

    approved = cli.invoke(
        app,
        [
            "tenant",
            "oauth-approve",
            "taylor",
            "--issuer",
            ISSUER,
            "--subject",
            "user_taylor",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
            "--data-root",
            str(tmp_path / "tenants"),
            "--service-user",
            service_user,
        ],
    )

    assert approved.exit_code == 0, approved.output
    assert "Signaled the running shared service (pid 42) to reload" in approved.output
    assert "Reloaded the running shared service" not in approved.output


def test_approval_refuses_an_identity_that_never_completed_sign_in(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    registry.write_text("", encoding="utf-8")
    reserve = cli.invoke(
        app,
        [
            "tenant",
            "oauth-invite",
            "taylor",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )
    assert reserve.exit_code == 0

    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-approve",
            "taylor",
            "--issuer",
            ISSUER,
            "--subject",
            "never-signed-in",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
            "--data-root",
            str(tmp_path / "tenants"),
            "--service-user",
            pwd.getpwuid(os.getuid()).pw_name,
        ],
    )
    assert result.exit_code == 1
    assert "not in the verified pending queue" in result.output
    assert not (tmp_path / "tenants" / "taylor").exists()


def test_slug_reservation_refuses_existing_tenant_and_duplicate_invite(tmp_path: Path) -> None:
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        f'[[tenant]]\nslug = "existing"\ndata_dir = "{tmp_path / "existing"}"\n',
        encoding="utf-8",
    )

    existing = cli.invoke(
        app,
        [
            "tenant",
            "oauth-invite",
            "existing",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )
    assert existing.exit_code == 1

    first = cli.invoke(
        app,
        [
            "tenant",
            "oauth-invite",
            "new-person",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )
    second = cli.invoke(
        app,
        [
            "tenant",
            "oauth-invite",
            "new-person",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )
    assert first.exit_code == 0
    assert second.exit_code == 1
