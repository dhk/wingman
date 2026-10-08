"""OAuth browser login protects BYOK setup without capability URLs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
import urllib.parse
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

import wingman.infrastructure.oauth_browser as browser_module
import wingman.webui as webui_module
from wingman.infrastructure.oauth_bearer import (
    BearerError,
    BearerIdentity,
    IdentityMap,
    OAuthConfigError,
    OAuthSettings,
)
from wingman.infrastructure.oauth_browser import (
    OAuthBrowserSessions,
    bind_oauth_browser,
    build_oauth_browser_settings,
    validate_browser_session_token,
)
from wingman.infrastructure.tenants import Tenant, TenantIndex

ISSUER = "https://example.authkit.app"
AUDIENCE = "https://wingman.example.com/shared/mcp"
BROWSER_CLIENT_ID = "client_123"
BROWSER_ISSUER = f"https://api.workos.com/user_management/{BROWSER_CLIENT_ID}"
_SESSION_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _session_token(**overrides: object) -> str:
    claims: dict[str, object] = {
        "iss": BROWSER_ISSUER,
        "sub": "user_taylor",
        "client_id": BROWSER_CLIENT_ID,
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, _SESSION_KEY, algorithm="RS256", headers={"kid": "session-key"})


def test_browser_session_token_accepts_workos_claim_shape_without_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", "super-secret")
    settings = build_oauth_browser_settings(
        BROWSER_CLIENT_ID,
        "https://api.workos.com/user_management/authorize",
        "https://api.workos.com/user_management/authenticate",
        "https://wingman.example.com/oauth/callback",
    )

    identity = validate_browser_session_token(
        _session_token(), settings, lambda _token: _SESSION_KEY.public_key()
    )

    assert identity.issuer == BROWSER_ISSUER
    assert identity.subject == "user_taylor"


@pytest.mark.parametrize(
    ("claims", "expected_reason"),
    [
        ({"iss": "https://attacker.example"}, "InvalidIssuerError"),
        ({"client_id": "client_attacker"}, "wrong-client-id"),
    ],
)
def test_browser_session_token_rejects_wrong_trust_claims(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, object],
    expected_reason: str,
) -> None:
    monkeypatch.setenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", "super-secret")
    settings = build_oauth_browser_settings(
        BROWSER_CLIENT_ID,
        "https://api.workos.com/user_management/authorize",
        "https://api.workos.com/user_management/authenticate",
        "https://wingman.example.com/oauth/callback",
    )

    with pytest.raises(BearerError, match=expected_reason):
        validate_browser_session_token(
            _session_token(**claims), settings, lambda _token: _SESSION_KEY.public_key()
        )


def test_browser_session_token_rejects_wrong_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", "super-secret")
    settings = build_oauth_browser_settings(
        BROWSER_CLIENT_ID,
        "https://api.workos.com/user_management/authorize",
        "https://api.workos.com/user_management/authenticate",
        "https://wingman.example.com/oauth/callback",
    )
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(BearerError, match="InvalidSignatureError"):
        validate_browser_session_token(
            _session_token(), settings, lambda _token: other_key.public_key()
        )


def test_browser_session_token_rejects_symmetric_algorithm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", "super-secret")
    settings = build_oauth_browser_settings(
        BROWSER_CLIENT_ID,
        "https://api.workos.com/user_management/authorize",
        "https://api.workos.com/user_management/authenticate",
        "https://wingman.example.com/oauth/callback",
    )
    token = jwt.encode(
        {
            "iss": BROWSER_ISSUER,
            "sub": "user_taylor",
            "client_id": BROWSER_CLIENT_ID,
            "exp": int(time.time()) + 300,
        },
        "a-test-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )

    with pytest.raises(BearerError, match="InvalidAlgorithmError"):
        validate_browser_session_token(
            token,
            settings,
            lambda _token: "a-test-secret-that-is-long-enough-for-hs256",
        )


def _world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, public_mount: str = ""
) -> tuple[TestClient, Tenant, list[str]]:
    tenant = Tenant(slug="taylor", data_dir=tmp_path / "taylor")
    tenant.data_dir.mkdir(parents=True)
    index = TenantIndex([tenant])
    identities = IdentityMap({(BROWSER_ISSUER, "user_taylor"): "taylor"})
    monkeypatch.setenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", "super-secret")
    settings = build_oauth_browser_settings(
        "client_123",
        "https://api.workos.com/user_management/authorize",
        "https://api.workos.com/user_management/authenticate",
        f"https://wingman.example.com{public_mount}/oauth/callback",
    )
    validated: list[str] = []
    challenges: dict[str, str] = {}

    def validate(token: str, _settings: object, _resolver: object) -> BearerIdentity:
        validated.append(token)
        return BearerIdentity(BROWSER_ISSUER, "user_taylor", ())

    monkeypatch.setattr(browser_module, "validate_browser_session_token", validate)

    def exchange(code: str, verifier: str) -> str:
        actual = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        if challenges.get(code) != actual:
            raise OAuthConfigError("authorization code is not bound to this verifier")
        return f"access-{code}"

    pending: list[tuple[str, str]] = []
    sessions = OAuthBrowserSessions(
        settings,
        OAuthSettings(ISSUER, AUDIENCE, f"{ISSUER}/oauth2/jwks"),
        identities,
        index,
        lambda _token: object(),
        token_exchange=exchange,
        record_pending=lambda issuer, subject: not pending.append((issuer, subject)),
    )
    app = Starlette()
    app.state.oauth_challenges = challenges
    app.state.oauth_sessions = sessions
    app.state.oauth_pending = pending
    bind_oauth_browser(app, "", sessions)
    return TestClient(app, base_url="https://wingman.example.com"), tenant, validated


def _issue_code(client: TestClient, code: str, authorization_url: str) -> None:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(authorization_url).query)
    client.app.state.oauth_challenges[code] = query["code_challenge"][0]


def _sign_in(client: TestClient, code: str = "good") -> None:
    login = client.get("/login", follow_redirects=False)
    assert login.status_code == 302
    location = login.headers["location"]
    assert "code_challenge_method=S256" in location
    assert "client_secret" not in location
    assert "/ui/" not in location and "/mcp/" not in location
    _issue_code(client, code, location)
    state = re.search(r"[?&]state=([^&]+)", location)
    assert state is not None
    callback = client.get(
        f"/oauth/callback?code={code}&state={state.group(1)}",
        headers={"Cookie": f"wingman_oauth_state={client.cookies.get('wingman_oauth_state')}"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/setup/"
    cookie = callback.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "access-good" not in cookie


def _csrf(page: str) -> str:
    match = re.search(r'name="_csrf" value="([^"]+)"', page)
    assert match is not None
    return match.group(1)


def test_workos_code_exchange_uses_documented_json_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    requests: list[object] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"access_token":"access-good"}'

    def open_request(request: object, *, timeout: int):
        requests.append(request)
        assert timeout == 10
        return Response()

    monkeypatch.setattr(browser_module.urllib.request, "urlopen", open_request)
    sessions = client.app.state.oauth_sessions

    token = sessions._exchange_code("code-good", "verifier-good")

    assert token == "access-good"
    request = requests[0]
    assert request.headers["Content-type"] == "application/json"
    assert json.loads(request.data) == {
        "client_id": "client_123",
        "client_secret": "super-secret",
        "grant_type": "authorization_code",
        "code": "code-good",
        "code_verifier": "verifier-good",
    }


def test_login_revalidates_identity_and_never_exposes_capability_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, validated = _world(tmp_path, monkeypatch)
    _sign_in(client)

    page = client.get("/setup/")
    assert page.status_code == 200
    assert "Add an API key" in page.text
    assert "No model call has been made" in page.text
    assert "/ui/" not in page.text and "/mcp/" not in page.text
    # Once during callback and again for the protected page request.
    assert validated == ["access-good", "access-good"]


def test_setup_requires_csrf_and_stores_key_only_in_tenants_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, tenant, _validated = _world(tmp_path, monkeypatch)
    monkeypatch.setitem(webui_module.VALIDATORS, "anthropic", lambda _value: None)
    _sign_in(client)
    page = client.get("/setup/")

    refused = client.post("/setup/keys", data={"anthropic": "sk-ant-test"})
    assert refused.status_code == 403
    assert not (tenant.data_dir / "keys.env").exists()

    stored = client.post(
        "/setup/keys",
        data={"_csrf": _csrf(page.text), "anthropic": "sk-ant-test"},
    )
    assert stored.status_code == 200
    contents = (tenant.data_dir / "keys.env").read_text(encoding="utf-8")
    assert "sk-ant-test" in contents
    assert "sk-ant-test" not in stored.text


def test_cv_upload_is_refused_before_key_without_reading_or_storing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, tenant, _validated = _world(tmp_path, monkeypatch)
    _sign_in(client)
    page = client.get("/setup/")
    response = client.post(
        "/setup/upload",
        data={"_csrf": _csrf(page.text)},
        files={"file": ("resume.md", b"PRIVATE CV", "text/markdown")},
    )
    assert response.status_code == 200
    assert "No model call was made" in response.text
    assert not (tenant.data_dir / "inbox").exists()


def test_state_is_one_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    login = client.get("/login", follow_redirects=False)
    _issue_code(client, "good", login.headers["location"])
    state = re.search(r"[?&]state=([^&]+)", login.headers["location"])
    assert state is not None
    url = f"/oauth/callback?code=good&state={state.group(1)}"
    cookie = {"Cookie": f"wingman_oauth_state={client.cookies.get('wingman_oauth_state')}"}
    assert client.get(url, headers=cookie, follow_redirects=False).status_code == 303
    assert client.get(url, headers=cookie, follow_redirects=False).status_code == 400


def test_callback_uses_cookie_state_when_provider_omits_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    login = client.get("/login", follow_redirects=False)
    _issue_code(client, "good", login.headers["location"])

    callback = client.get("/oauth/callback?code=good", follow_redirects=False)

    assert callback.status_code == 303
    assert callback.headers["location"] == "/setup/"


def test_callback_rejects_explicit_state_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    client.get("/login", follow_redirects=False)

    callback = client.get(
        "/oauth/callback?code=good&state=attacker-controlled", follow_redirects=False
    )

    assert callback.status_code == 400


def test_callback_rejects_explicit_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    login = client.get("/login", follow_redirects=False)
    _issue_code(client, "good", login.headers["location"])

    callback = client.get("/oauth/callback?code=good&state=", follow_redirects=False)

    assert callback.status_code == 400


def test_cookie_only_callback_requires_the_browser_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    login = client.get("/login", follow_redirects=False)
    _issue_code(client, "good", login.headers["location"])
    other_browser = TestClient(client.app, base_url="https://wingman.example.com")

    callback = other_browser.get("/oauth/callback?code=good", follow_redirects=False)

    assert callback.status_code == 400


def test_cookie_only_callback_is_one_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    login = client.get("/login", follow_redirects=False)
    _issue_code(client, "good", login.headers["location"])

    assert client.get("/oauth/callback?code=good", follow_redirects=False).status_code == 303
    assert client.get("/oauth/callback?code=good", follow_redirects=False).status_code == 400


def test_cookie_only_callback_rejects_code_from_another_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    first = client.get("/login", follow_redirects=False)
    _issue_code(client, "first", first.headers["location"])
    client.get("/login", follow_redirects=False)

    callback = client.get("/oauth/callback?code=first", follow_redirects=False)

    assert callback.status_code == 401


def test_state_cookie_is_scoped_to_the_registered_public_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch, public_mount="/shared")
    login = client.get("/login", follow_redirects=False)

    assert "Path=/shared/oauth/callback" in login.headers["set-cookie"]


def test_login_selects_authkit_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)

    login = client.get("/login", follow_redirects=False)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(login.headers["location"]).query)

    assert query["provider"] == ["authkit"]


def test_browser_redirects_and_session_cookie_preserve_public_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch, public_mount="/shared")
    login = client.get("/login", follow_redirects=False)
    _issue_code(client, "good", login.headers["location"])
    state = re.search(r"[?&]state=([^&]+)", login.headers["location"])
    assert state is not None
    callback = client.get(
        f"/oauth/callback?code=good&state={state.group(1)}",
        headers={"Cookie": f"wingman_oauth_state={client.cookies.get('wingman_oauth_state')}"},
        follow_redirects=False,
    )

    assert callback.headers["location"] == "/shared/setup/"
    assert "Path=/shared/setup" in callback.headers["set-cookie"]
    unauthorized = TestClient(client.app, base_url="https://wingman.example.com").get(
        "/setup/", follow_redirects=False
    )
    assert unauthorized.headers["location"] == "/shared/login"
    session_cookie = {
        "Cookie": f"wingman_setup_session={client.cookies.get('wingman_setup_session')}"
    }
    page = client.get("/setup/", headers=session_cookie)
    logout = client.post(
        "/setup/logout",
        headers=session_cookie,
        data={"_csrf": _csrf(page.text)},
        follow_redirects=False,
    )
    assert logout.headers["location"] == "/shared/login"
    assert "Path=/shared/setup" in logout.headers["set-cookie"]


def test_token_exchange_does_not_block_other_async_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = OAuthBrowserSessions.finish

    def slow_finish(self: OAuthBrowserSessions, code: str, verifier: str) -> str:
        time.sleep(0.3)
        return original(self, code, verifier)

    monkeypatch.setattr(OAuthBrowserSessions, "finish", slow_finish)
    client, _tenant, _validated = _world(tmp_path, monkeypatch)

    async def scenario() -> float:
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://wingman.example.com"
        ) as async_client:
            login = await async_client.get("/login", follow_redirects=False)
            _issue_code(client, "good", login.headers["location"])
            state = re.search(r"[?&]state=([^&]+)", login.headers["location"])
            assert state is not None
            callback = asyncio.create_task(
                async_client.get(
                    f"/oauth/callback?code=good&state={state.group(1)}",
                    headers={
                        "Cookie": (
                            f"wingman_oauth_state={async_client.cookies.get('wingman_oauth_state')}"
                        )
                    },
                    follow_redirects=False,
                )
            )
            started = time.monotonic()
            await asyncio.sleep(0.01)
            probe = await async_client.get("/login", follow_redirects=False)
            elapsed = time.monotonic() - started
            assert probe.status_code == 302
            assert (await callback).status_code == 303
            return elapsed

    assert asyncio.run(scenario()) < 0.15


def test_missing_key_preflight_happens_before_multipart_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, tenant, _validated = _world(tmp_path, monkeypatch)
    _sign_in(client)

    async def unexpected_form_parse(_request: Request):
        raise AssertionError("multipart body was parsed before key preflight")

    monkeypatch.setattr(Request, "form", unexpected_form_parse)
    response = client.post(
        "/setup/upload",
        files={"file": ("resume.md", b"PRIVATE CV", "text/markdown")},
    )

    assert response.status_code == 200
    assert "No model call was made" in response.text
    assert not (tenant.data_dir / "inbox").exists()


def test_verified_but_unapproved_identity_is_refused_before_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    monkeypatch.setattr(
        browser_module,
        "validate_browser_session_token",
        lambda _token, _settings, _resolver: BearerIdentity(BROWSER_ISSUER, "not-approved", ()),
    )
    login = client.get("/login", follow_redirects=False)
    _issue_code(client, "good", login.headers["location"])
    state = re.search(r"[?&]state=([^&]+)", login.headers["location"])
    assert state is not None

    callback = client.get(
        f"/oauth/callback?code=good&state={state.group(1)}",
        headers={"Cookie": f"wingman_oauth_state={client.cookies.get('wingman_oauth_state')}"},
        follow_redirects=False,
    )

    assert callback.status_code == 403
    assert "not approved" in callback.text
    from wingman.infrastructure.oauth_onboarding import pending_reference

    assert f"Send reference {pending_reference(BROWSER_ISSUER, 'not-approved')}" in callback.text
    assert "not-approved" not in callback.text.replace("not approved", "")
    assert "wingman_setup_session" not in callback.headers.get("set-cookie", "")
    assert client.app.state.oauth_pending == [(BROWSER_ISSUER, "not-approved")]


def test_logout_requires_csrf_before_destroying_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _tenant, _validated = _world(tmp_path, monkeypatch)
    _sign_in(client)
    page = client.get("/setup/")

    refused = client.post("/setup/logout", follow_redirects=False)
    assert refused.status_code == 403
    assert client.get("/setup/", follow_redirects=False).status_code == 200

    logged_out = client.post(
        "/setup/logout", data={"_csrf": _csrf(page.text)}, follow_redirects=False
    )
    assert logged_out.status_code == 303
    assert client.get("/setup/", follow_redirects=False).status_code == 303


def test_browser_configuration_fails_closed_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", raising=False)
    with pytest.raises(Exception, match="WINGMAN_OAUTH_WEB_CLIENT_SECRET"):
        build_oauth_browser_settings(
            "client_123",
            "https://api.workos.com/user_management/authorize",
            "https://api.workos.com/user_management/authenticate",
            "https://wingman.example.com/shared/oauth/callback",
        )
