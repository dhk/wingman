"""OAuth browser login protects BYOK setup without capability URLs."""

from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

import wingman.infrastructure.oauth_browser as browser_module
import wingman.webui as webui_module
from wingman.infrastructure.oauth_bearer import BearerIdentity, IdentityMap, OAuthSettings
from wingman.infrastructure.oauth_browser import (
    OAuthBrowserSessions,
    bind_oauth_browser,
    build_oauth_browser_settings,
)
from wingman.infrastructure.tenants import Tenant, TenantIndex

ISSUER = "https://example.authkit.app"
AUDIENCE = "https://wingman.example.com/shared/mcp"


def _world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, public_mount: str = ""
) -> tuple[TestClient, Tenant, list[str]]:
    tenant = Tenant(slug="taylor", data_dir=tmp_path / "taylor")
    tenant.data_dir.mkdir(parents=True)
    index = TenantIndex([tenant])
    identities = IdentityMap({(ISSUER, "user_taylor"): "taylor"})
    monkeypatch.setenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", "super-secret")
    settings = build_oauth_browser_settings(
        "client_123",
        "https://api.workos.com/user_management/authorize",
        "https://api.workos.com/user_management/authenticate",
        f"https://wingman.example.com{public_mount}/oauth/callback",
    )
    validated: list[str] = []

    def validate(token: str, _settings: OAuthSettings, _resolver: object) -> BearerIdentity:
        validated.append(token)
        return BearerIdentity(ISSUER, "user_taylor", ())

    monkeypatch.setattr(browser_module, "validate_access_token", validate)
    sessions = OAuthBrowserSessions(
        settings,
        OAuthSettings(ISSUER, AUDIENCE, f"{ISSUER}/oauth2/jwks"),
        identities,
        index,
        lambda _token: object(),
        token_exchange=lambda code, _verifier: f"access-{code}",
    )
    app = Starlette()
    bind_oauth_browser(app, "", sessions)
    return TestClient(app, base_url="https://wingman.example.com"), tenant, validated


def _sign_in(client: TestClient, code: str = "good") -> None:
    login = client.get("/login", follow_redirects=False)
    assert login.status_code == 302
    location = login.headers["location"]
    assert "code_challenge_method=S256" in location
    assert "client_secret" not in location
    assert "/ui/" not in location and "/mcp/" not in location
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
    state = re.search(r"[?&]state=([^&]+)", login.headers["location"])
    assert state is not None
    url = f"/oauth/callback?code=good&state={state.group(1)}"
    cookie = {"Cookie": f"wingman_oauth_state={client.cookies.get('wingman_oauth_state')}"}
    assert client.get(url, headers=cookie, follow_redirects=False).status_code == 303
    assert client.get(url, headers=cookie, follow_redirects=False).status_code == 400


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
        "validate_access_token",
        lambda _token, _settings, _resolver: BearerIdentity(ISSUER, "not-approved", ()),
    )
    login = client.get("/login", follow_redirects=False)
    state = re.search(r"[?&]state=([^&]+)", login.headers["location"])
    assert state is not None

    callback = client.get(
        f"/oauth/callback?code=good&state={state.group(1)}",
        headers={"Cookie": f"wingman_oauth_state={client.cookies.get('wingman_oauth_state')}"},
        follow_redirects=False,
    )

    assert callback.status_code == 403
    assert "not approved" in callback.text
    assert "wingman_setup_session" not in callback.headers.get("set-cookie", "")


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
