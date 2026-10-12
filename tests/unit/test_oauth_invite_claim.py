"""Claiming an email-bound invite on a verified sign-in (#585, RFC-081 amendment).

The operator prepares the tenant when inviting (that needs root, for the
registry). A verified sign-in whose WorkOS-verified address matches only binds
the identity — on both trusted issuers, because #582 showed one WorkOS user
has the same full subject on the browser session token and the MCP token.
"""

from __future__ import annotations

import json
import logging
import os
import pwd
import threading
from pathlib import Path

import anyio
import pytest
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from wingman.infrastructure.oauth_bearer import (
    BearerIdentity,
    BearerRoutingASGIApp,
    IdentityMap,
    OAuthSettings,
)
from wingman.infrastructure.oauth_invite_claim import (
    EmailInviteClaimer,
    userinfo_url,
    verified_email_from,
    verified_userinfo_email,
)
from wingman.infrastructure.oauth_onboarding import (
    INVITE_MARKER,
    OAuthOnboardingStore,
    onboarding_path_for,
    pending_reference,
    prepare_email_invites,
)
from wingman.infrastructure.tenants import TenantIndex, load_registry

MCP_ISSUER = "https://example.authkit.app"
BROWSER_ISSUER = "https://api.workos.com/user_management/client_123"
AUDIENCE = "https://wingman.example.com/shared/mcp"
EMAIL = "Pat@Example.com"
SUB = "user_01PATPRIVATESUBJECT"
SERVICE_USER = pwd.getpwuid(os.getuid()).pw_name


class _World:
    def __init__(self, tmp_path: Path) -> None:
        self.identity_path = tmp_path / "oauth-identities.toml"
        self.identity_path.write_text("", encoding="utf-8")
        self.registry = tmp_path / "tenants.toml"
        self.registry.write_text("", encoding="utf-8")
        self.data_root = tmp_path / "tenants"
        self.store = OAuthOnboardingStore(onboarding_path_for(self.identity_path))
        self.identities = IdentityMap({})
        self.index = TenantIndex([])

    def invite(self, email: str = EMAIL, slug: str = "pat", *, expires_days: int = 30) -> None:
        prepare_email_invites(
            identity_path=self.identity_path,
            registry_path=self.registry,
            data_root=self.data_root,
            rows=[(email, slug)],
            existing_slugs=set(),
            service_user=SERVICE_USER,
            expires_days=expires_days,
        )
        # What the operator's SIGHUP does for the live process.
        self.index.replace_with(TenantIndex(load_registry(self.registry)))

    def claimer(self) -> EmailInviteClaimer:
        return EmailInviteClaimer(
            self.identity_path,
            self.store,
            self.index,
            self.identities,
            (MCP_ISSUER, BROWSER_ISSUER),
        )

    def on_disk(self) -> dict[tuple[str, str], str]:
        return IdentityMap.from_toml(self.identity_path).entries()


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


# --- preparing the invite ----------------------------------------------------


def test_an_email_invite_prepares_an_unbound_unprivileged_tenant(world: _World) -> None:
    world.invite()

    [tenant] = load_registry(world.registry)
    assert tenant.slug == "pat"
    assert not tenant.privileged and not tenant.funded
    assert (tenant.data_dir / INVITE_MARKER).exists()
    assert world.on_disk() == {}  # nobody can reach it until a verified sign-in claims it


def test_a_failed_preparation_withdraws_the_reservation(world: _World) -> None:
    (world.data_root / "pat").mkdir(parents=True)  # someone else's directory, no marker

    with pytest.raises(Exception, match="nothing was overwritten"):
        world.invite()

    assert world.store.invites() == []
    assert load_registry(world.registry) == []


# --- the claim itself --------------------------------------------------------


def test_a_verified_matching_sign_in_binds_both_surfaces_and_consumes_the_invite(
    world: _World,
) -> None:
    world.invite()
    world.store.record_pending(MCP_ISSUER, SUB)
    claimer = world.claimer()

    assert claimer.claim(BROWSER_ISSUER, SUB, "  pat@EXAMPLE.com ") == "pat"

    assert world.on_disk() == {(BROWSER_ISSUER, SUB): "pat", (MCP_ISSUER, SUB): "pat"}
    assert world.identities.slug_for(MCP_ISSUER, SUB) == "pat"  # live map refreshed
    assert world.store.invites() == []
    assert world.store.pending() == []
    assert not (world.data_root / "pat" / INVITE_MARKER).exists()


def test_a_mismatched_or_expired_invite_is_not_claimed(world: _World) -> None:
    world.invite()
    claimer = world.claimer()
    assert claimer.claim(MCP_ISSUER, SUB, "someone-else@example.com") is None

    world.store.revoke_invite("pat")
    world.invite("pat@example.com", "pat2", expires_days=1)
    later = world.claimer()
    later._now = lambda: 10e12  # type: ignore[method-assign]  # far past expiry
    assert later.claim(MCP_ISSUER, SUB, EMAIL) is None
    assert world.on_disk() == {}


def test_an_untrusted_issuer_never_claims(world: _World) -> None:
    world.invite()
    assert world.claimer().claim("https://attacker.example", SUB, EMAIL) is None
    assert world.on_disk() == {}
    assert len(world.store.invites()) == 1


def test_replay_after_claim_creates_nothing_new(world: _World) -> None:
    world.invite()
    claimer = world.claimer()
    assert claimer.claim(MCP_ISSUER, SUB, EMAIL) == "pat"
    # The same person again: already bound, same answer, nothing changes.
    assert claimer.claim(MCP_ISSUER, SUB, EMAIL) == "pat"
    # A different identity presenting the same address finds no invite left.
    assert claimer.claim(MCP_ISSUER, "user_other", EMAIL) is None
    assert world.on_disk() == {(BROWSER_ISSUER, SUB): "pat", (MCP_ISSUER, SUB): "pat"}


def test_simultaneous_claims_from_both_surfaces_bind_one_tenant(world: _World) -> None:
    world.invite()
    claimer = world.claimer()
    results: list[str | None] = []
    start = threading.Barrier(2)

    def run(issuer: str) -> None:
        start.wait()
        results.append(claimer.claim(issuer, SUB, EMAIL))

    threads = [
        threading.Thread(target=run, args=(issuer,)) for issuer in (MCP_ISSUER, BROWSER_ISSUER)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == ["pat", "pat"]
    assert world.on_disk() == {(BROWSER_ISSUER, SUB): "pat", (MCP_ISSUER, SUB): "pat"}
    assert world.store.invites() == []


def test_a_slug_already_bound_to_someone_is_never_claimed(world: _World) -> None:
    world.invite()
    world.identity_path.write_text(
        f'[[identity]]\niss = "{MCP_ISSUER}"\nsub = "user_someone"\nslug = "pat"\n',
        encoding="utf-8",
    )
    assert world.claimer().claim(MCP_ISSUER, SUB, EMAIL) is None


def test_logs_name_the_reference_never_the_address_or_subject(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    world.invite()
    with caplog.at_level(logging.INFO, logger="wingman"):
        world.claimer().claim(MCP_ISSUER, SUB, EMAIL)
        world.claimer().claim(MCP_ISSUER, "user_nobody", "nobody@example.com")

    text = caplog.text
    assert f"ref={pending_reference(MCP_ISSUER, SUB)}" in text
    for private in (SUB, "user_nobody", "example.com", "Pat@"):
        assert private not in text
    on_disk = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in world.identity_path.parent.rglob("*")
        if path.is_file()
    )
    assert "example.com" not in on_disk.lower()


# --- verified email sources --------------------------------------------------


@pytest.mark.parametrize(
    "user",
    [
        {"id": SUB, "email": EMAIL, "email_verified": False},
        {"id": SUB, "email": EMAIL, "email_verified": "true"},
        {"id": SUB, "email": EMAIL},
        {"id": "user_other", "email": EMAIL, "email_verified": True},
        {"id": SUB, "email": None, "email_verified": True},
        "not-a-user",
        None,
    ],
)
def test_only_a_verified_address_for_the_same_subject_counts(user: object) -> None:
    assert verified_email_from(user, SUB, id_field="id") is None


def test_a_verified_address_for_the_same_subject_counts() -> None:
    user = {"id": SUB, "email": EMAIL, "email_verified": True}
    assert verified_email_from(user, SUB, id_field="id") == EMAIL


def test_userinfo_is_only_ever_sent_to_the_configured_https_issuer() -> None:
    assert userinfo_url(MCP_ISSUER) == "https://example.authkit.app/oauth2/userinfo"
    for bad in ("http://example.authkit.app", "example.authkit.app", "https://"):
        with pytest.raises(ValueError):
            userinfo_url(bad)
    calls: list[str] = []
    assert (
        verified_userinfo_email(
            "http://evil.example", "tok", SUB, fetch=lambda u, m, t: calls.append(u) or (200, b"")
        )
        is None
    )
    assert calls == []


def test_userinfo_requires_200_matching_sub_and_verified_flag() -> None:
    good = json.dumps({"sub": SUB, "email": EMAIL, "email_verified": True}).encode()

    def answer(status: int, body: bytes):
        return lambda _url, _method, _token: (status, body)

    assert verified_userinfo_email(MCP_ISSUER, "tok", SUB, fetch=answer(200, good)) == EMAIL
    assert verified_userinfo_email(MCP_ISSUER, "tok", SUB, fetch=answer(401, good)) is None
    other = json.dumps({"sub": "user_other", "email": EMAIL, "email_verified": True}).encode()
    assert verified_userinfo_email(MCP_ISSUER, "tok", SUB, fetch=answer(200, other)) is None
    unverified = json.dumps({"sub": SUB, "email": EMAIL, "email_verified": False}).encode()
    assert verified_userinfo_email(MCP_ISSUER, "tok", SUB, fetch=answer(200, unverified)) is None
    assert verified_userinfo_email(MCP_ISSUER, "tok", SUB, fetch=answer(200, b"<html>")) is None

    def timeout(_url: str, _method: str, _token: str) -> tuple[int, bytes]:
        raise TimeoutError

    assert verified_userinfo_email(MCP_ISSUER, "tok", SUB, fetch=timeout) is None

    methods: list[str] = []

    def post_only(_url: str, method: str, _token: str) -> tuple[int, bytes]:
        methods.append(method)
        return (405, b"") if method == "GET" else (200, good)

    assert verified_userinfo_email(MCP_ISSUER, "tok", SUB, fetch=post_only) == EMAIL
    assert methods == ["GET", "POST"]


# --- the two surfaces --------------------------------------------------------


def _bearer_app(world: _World, claimer: EmailInviteClaimer | None) -> TestClient:
    async def inner(scope, receive, send):  # type: ignore[no-untyped-def]
        await PlainTextResponse("served")(scope, receive, send)

    app = BearerRoutingASGIApp(
        inner,
        world.index,
        world.identities,
        OAuthSettings(MCP_ISSUER, AUDIENCE, "https://example.authkit.app/oauth2/jwks"),
        lambda _token: None,
        record_pending=lambda iss, sub: world.store.record_pending(iss, sub),
        invite_claimer=claimer,
    )
    return TestClient(app)


def test_mcp_first_sign_in_with_an_invited_address_is_served(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.infrastructure.oauth_bearer as bearer_module

    monkeypatch.setattr(
        bearer_module,
        "validate_access_token",
        lambda _token, _settings, _resolver: BearerIdentity(MCP_ISSUER, SUB, ()),
    )
    world.invite()
    lookups: list[str] = []

    def fetch(url: str, _method: str, token: str) -> tuple[int, bytes]:
        lookups.append(url)
        assert token == "the-token"
        return 200, json.dumps({"sub": SUB, "email": EMAIL, "email_verified": True}).encode()

    claimer = world.claimer()
    claimer._fetch = fetch  # type: ignore[method-assign]
    client = _bearer_app(world, claimer)

    first = client.post("/mcp", headers={"Authorization": "Bearer the-token"})
    second = client.post("/mcp", headers={"Authorization": "Bearer the-token"})

    assert first.status_code == 200 and first.text == "served"
    assert second.status_code == 200
    assert lookups == ["https://example.authkit.app/oauth2/userinfo"]  # bound: no more lookups
    assert world.store.pending() == []


def test_mcp_without_an_invite_is_refused_and_not_looked_up_on_every_request(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.infrastructure.oauth_bearer as bearer_module

    monkeypatch.setattr(
        bearer_module,
        "validate_access_token",
        lambda _token, _settings, _resolver: BearerIdentity(MCP_ISSUER, SUB, ()),
    )
    lookups: list[str] = []

    def fetch(url: str, _method: str, _token: str) -> tuple[int, bytes]:
        lookups.append(url)
        return 200, json.dumps({"sub": SUB, "email": EMAIL, "email_verified": True}).encode()

    claimer = world.claimer()
    claimer._fetch = fetch  # type: ignore[method-assign]
    client = _bearer_app(world, claimer)

    for _ in range(3):
        refused = client.post("/mcp", headers={"Authorization": "Bearer the-token"})
        assert refused.status_code == 403
        assert pending_reference(MCP_ISSUER, SUB) in refused.text

    assert len(lookups) == 1  # a waiting person costs one lookup per window, not one per request
    assert [(row.issuer, row.subject) for row in world.store.pending()] == [(MCP_ISSUER, SUB)]


def test_a_bound_identity_never_triggers_a_lookup(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.infrastructure.oauth_bearer as bearer_module

    monkeypatch.setattr(
        bearer_module,
        "validate_access_token",
        lambda _token, _settings, _resolver: BearerIdentity(MCP_ISSUER, SUB, ()),
    )
    world.invite()
    world.claimer().claim(BROWSER_ISSUER, SUB, EMAIL)  # claimed in the browser first
    claimer = world.claimer()

    def fetch(_url: str, _method: str, _token: str) -> tuple[int, bytes]:
        raise AssertionError("bound identities are never looked up")

    claimer._fetch = fetch  # type: ignore[method-assign]
    response = _bearer_app(world, claimer).post(
        "/mcp", headers={"Authorization": "Bearer the-token"}
    )

    assert response.status_code == 200  # the second surface works with no operator step


def test_browser_first_sign_in_with_an_invited_address_reaches_setup(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.infrastructure.oauth_browser as browser_module
    from wingman.infrastructure.oauth_browser import (
        OAuthBrowserSessions,
        build_oauth_browser_settings,
    )

    monkeypatch.setenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", "super-secret")
    monkeypatch.setattr(
        browser_module,
        "validate_browser_session_token",
        lambda _token, _settings, _resolver: BearerIdentity(BROWSER_ISSUER, SUB, ()),
    )
    world.invite()
    sessions = OAuthBrowserSessions(
        build_oauth_browser_settings(
            "client_123",
            "https://api.workos.com/user_management/authorize",
            "https://api.workos.com/user_management/authenticate",
            "https://wingman.example.com/oauth/callback",
        ),
        OAuthSettings(MCP_ISSUER, AUDIENCE, "https://example.authkit.app/oauth2/jwks"),
        world.identities,
        world.index,
        lambda _token: None,
        token_exchange=lambda _code, _verifier: (
            "access",
            {"id": SUB, "email": EMAIL, "email_verified": True},
        ),
        record_pending=lambda iss, sub: world.store.record_pending(iss, sub),
        invite_claimer=world.claimer(),
    )

    session_id = sessions.finish("code", "verifier")

    assert session_id
    assert world.on_disk() == {(BROWSER_ISSUER, SUB): "pat", (MCP_ISSUER, SUB): "pat"}


def test_browser_sign_in_with_an_unverified_address_stays_pending(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.infrastructure.oauth_browser as browser_module
    from wingman.infrastructure.oauth_browser import (
        OAuthBrowserSessions,
        UnapprovedIdentity,
        build_oauth_browser_settings,
    )

    monkeypatch.setenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", "super-secret")
    monkeypatch.setattr(
        browser_module,
        "validate_browser_session_token",
        lambda _token, _settings, _resolver: BearerIdentity(BROWSER_ISSUER, SUB, ()),
    )
    world.invite()
    sessions = OAuthBrowserSessions(
        build_oauth_browser_settings(
            "client_123",
            "https://api.workos.com/user_management/authorize",
            "https://api.workos.com/user_management/authenticate",
            "https://wingman.example.com/oauth/callback",
        ),
        OAuthSettings(MCP_ISSUER, AUDIENCE, "https://example.authkit.app/oauth2/jwks"),
        world.identities,
        world.index,
        lambda _token: None,
        token_exchange=lambda _code, _verifier: (
            "access",
            {"id": SUB, "email": EMAIL, "email_verified": False},
        ),
        record_pending=lambda iss, sub: world.store.record_pending(iss, sub),
        invite_claimer=world.claimer(),
    )

    with pytest.raises(UnapprovedIdentity):
        sessions.finish("code", "verifier")

    assert world.on_disk() == {}
    assert [(row.issuer, row.subject) for row in world.store.pending()] == [(BROWSER_ISSUER, SUB)]


def test_anyio_is_importable_for_the_threaded_claim() -> None:
    # The MCP claim runs on a worker thread; keep the dependency explicit.
    assert anyio.to_thread.run_sync is not None


def test_an_email_invite_can_still_be_approved_by_hand(world: _World) -> None:
    from wingman.infrastructure.oauth_onboarding import approve_pending_tenant

    world.invite()
    world.store.record_pending(MCP_ISSUER, SUB)

    data_dir = approve_pending_tenant(
        identity_path=world.identity_path,
        registry_path=world.registry,
        data_root=world.data_root,
        slug="pat",
        issuer=MCP_ISSUER,
        subject=SUB,
        service_user=SERVICE_USER,
    )

    assert data_dir == world.data_root / "pat"
    assert world.on_disk() == {(MCP_ISSUER, SUB): "pat"}
    assert [tenant.slug for tenant in load_registry(world.registry)] == ["pat"]
    assert not (data_dir / INVITE_MARKER).exists()
    assert world.store.invites() == []
