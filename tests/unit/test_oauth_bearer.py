"""OAuth bearer validation for the shared multi-tenant process (RFC-081 draft spike).

The properties that matter, each tested directly rather than assumed:
  * a valid token reaches exactly the tenant its identity names, and two
    identities in flight at once never see each other's Config;
  * every way a token can be wrong is refused BEFORE the inner app runs;
  * the capability-token route is untouched by all of this.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from wingman.infrastructure.config import load_config
from wingman.infrastructure.oauth_bearer import (
    BearerRoutingASGIApp,
    IdentityMap,
    IdentityMapError,
    OAuthConfigError,
    OAuthSettings,
    bind_oauth_routing,
    build_oauth_settings,
)
from wingman.infrastructure.tenant_asgi import bind_tenant_routing
from wingman.infrastructure.tenants import Tenant, TenantIndex

ISSUER = "https://idp.example.com"
AUDIENCE = "https://wingman.example.com/mcp"
SETTINGS = OAuthSettings(
    issuer=ISSUER, audience=AUDIENCE, jwks_uri=f"{ISSUER}/jwks", scopes=("mcp",)
)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Inner:
    """Stands in for FastMCP's endpoint: counts calls, echoes which tenant's
    Config was bound."""

    def __init__(self, delay: float = 0.0) -> None:
        self.calls = 0
        self._delay = delay

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        await PlainTextResponse(str(load_config().data_dir))(scope, receive, send)


def _token(
    sub: str | None = "sub-jason",
    *,
    key: Any = _KEY,
    algorithm: str = "RS256",
    **overrides: Any,
) -> str:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=algorithm)


def _tenant(tmp_path: Path, slug: str, token: str) -> Tenant:
    data_dir = tmp_path / slug
    data_dir.mkdir()
    (data_dir / "mcp-http-token").write_text(token, encoding="utf-8")
    return Tenant(slug=slug, data_dir=data_dir)


class _World:
    def __init__(
        self,
        tmp_path: Path,
        inner: _Inner | None = None,
        resolve_key: Any = None,
    ) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.inner = inner or _Inner()
        self.jason = _tenant(tmp_path, "jason", "tok-jason")
        self.bob = _tenant(tmp_path, "bob", "tok-bob")
        self.index = TenantIndex([self.jason, self.bob])
        self.identities = IdentityMap(
            {
                (ISSUER, "sub-jason"): "jason",
                (ISSUER, "sub-bob"): "bob",
                # A second sign-in method for the same person, linked by hand.
                (ISSUER, "sub-jason-second-login"): "jason",
                # Provisioned to a tenant that is not in the registry.
                (ISSUER, "sub-ghost"): "ghost",
            }
        )
        self.app = Starlette(routes=[Route("/mcp/{token}", endpoint=self.inner)])
        bind_tenant_routing(self.app, "/mcp/{token}", self.index)
        bind_oauth_routing(
            self.app,
            "/mcp/{token}",
            "/mcp",
            self.index,
            self.identities,
            SETTINGS,
            resolve_key or (lambda _token: _KEY.public_key()),
        )
        self.client = TestClient(self.app)

    def get(self, token: str | None = None, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        response: httpx.Response = self.client.get("/mcp", headers=headers, **kwargs)
        return response


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


# --- the happy path and isolation ---------------------------------------------------


def test_a_valid_token_reaches_the_tenant_its_identity_names(world: _World) -> None:
    assert world.get(_token("sub-jason")).text == str(world.jason.data_dir)
    assert world.get(_token("sub-bob")).text == str(world.bob.data_dir)


def test_two_identities_of_one_person_reach_the_same_tenant(world: _World) -> None:
    assert world.get(_token("sub-jason-second-login")).text == str(world.jason.data_dir)


def test_concurrent_identities_never_see_each_others_config(tmp_path: Path) -> None:
    world = _World(tmp_path, inner=_Inner(delay=0.01))

    async def run() -> list[tuple[str, str]]:
        transport = httpx.ASGITransport(app=world.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            async def call(sub: str) -> tuple[str, str]:
                response = await client.get(
                    "/mcp", headers={"Authorization": f"Bearer {_token(sub)}"}
                )
                return sub, response.text

            subs = ["sub-jason", "sub-bob"] * 25
            return list(await asyncio.gather(*(call(sub) for sub in subs)))

    expected = {"sub-jason": str(world.jason.data_dir), "sub-bob": str(world.bob.data_dir)}
    results = asyncio.run(run())
    assert len(results) == 50
    assert all(body == expected[sub] for sub, body in results)


def test_the_capability_token_route_is_unchanged(world: _World) -> None:
    assert world.client.get("/mcp/tok-jason").text == str(world.jason.data_dir)
    assert world.client.get("/mcp/tok-nobody").status_code == 401


def test_a_bearer_header_does_not_authenticate_the_token_route(world: _World) -> None:
    """The two credentials never mix: the token route trusts its path alone."""
    response = world.client.get(
        "/mcp/tok-nobody", headers={"Authorization": f"Bearer {_token('sub-jason')}"}
    )
    assert response.status_code == 401
    assert world.inner.calls == 0


# --- every way a token can be wrong ------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda: _token(aud="https://other.example.com/mcp"), id="wrong-audience"),
        pytest.param(lambda: _token(iss="https://evil.example.com"), id="wrong-issuer"),
        pytest.param(lambda: _token(exp=int(time.time()) - 3600), id="expired"),
        pytest.param(lambda: _token(key=_OTHER_KEY), id="signed-by-another-key"),
        pytest.param(lambda: _token(exp=None), id="no-exp"),
        pytest.param(lambda: _token(sub=None), id="no-sub"),
        pytest.param(lambda: _token(aud=None), id="no-aud"),
        pytest.param(lambda: _token(iss=None), id="no-iss"),
        pytest.param(lambda: _token(key=None, algorithm="none"), id="alg-none"),
        pytest.param(
            lambda: _token(key="shared-secret-shared-secret-32b", algorithm="HS256"), id="hmac"
        ),
        pytest.param(lambda: "not-a-jwt", id="garbage"),
    ],
)
def test_a_bad_token_is_refused_before_the_inner_app_runs(world: _World, bad: Any) -> None:
    response = world.get(bad())
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]
    assert world.inner.calls == 0


def test_the_algorithm_allowlist_alone_refuses_hmac_even_with_a_matching_secret(
    tmp_path: Path,
) -> None:
    """PyJWT already refuses an RSA key object as an HMAC secret, so the 'hmac'
    case above would pass even with no allowlist. Here the resolver hands back
    a raw secret, and the token is signed with exactly that secret: nothing
    but the allowlist stands between this token and the tenant."""
    secret = "a-resolver-that-returns-a-raw-secret-32b"
    world = _World(tmp_path, resolve_key=lambda _token: secret)
    forged = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "sub-jason", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    assert world.get(forged).status_code == 401
    assert world.inner.calls == 0


def test_the_public_key_as_hmac_secret_attack_is_refused(tmp_path: Path) -> None:
    """The classic confusion attack: sign HS256 using the issuer's public key
    (PEM) as the secret, against a resolver that returns that PEM.

    Forged by hand with 'hmac': PyJWT itself refuses to encode an HS256 token
    from a PEM key, but an attacker is not obliged to use PyJWT."""
    import base64
    import hashlib
    import hmac
    import json

    from cryptography.hazmat.primitives import serialization

    pem = _KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "sub-jason", "exp": int(time.time()) + 300}
    payload = b64(json.dumps(claims).encode())
    signing_input = header + b"." + payload
    signature = b64(hmac.new(pem, signing_input, hashlib.sha256).digest())
    forged = (signing_input + b"." + signature).decode()

    world = _World(tmp_path, resolve_key=lambda _token: pem)
    assert world.get(forged).status_code == 401
    assert world.inner.calls == 0


def test_an_audience_list_containing_this_server_is_accepted(world: _World) -> None:
    token = _token(aud=["https://another.example.com", AUDIENCE])
    assert world.get(token).text == str(world.jason.data_dir)


def test_a_failing_key_lookup_fails_closed_not_500(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.app.router.routes.clear()
    world.app.router.routes.append(Route("/mcp/{token}", endpoint=world.inner))
    bind_oauth_routing(
        world.app,
        "/mcp/{token}",
        "/mcp",
        world.index,
        world.identities,
        SETTINGS,
        lambda _token: (_ for _ in ()).throw(RuntimeError("jwks unreachable")),
    )
    response = TestClient(world.app).get(
        "/mcp", headers={"Authorization": f"Bearer {_token('sub-jason')}"}
    )
    assert response.status_code == 401
    assert world.inner.calls == 0


# --- how the credential is presented -----------------------------------------------


def test_no_credentials_is_a_401_challenge_that_points_at_the_metadata(world: _World) -> None:
    response = world.get()
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert (
        'resource_metadata="https://wingman.example.com/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )
    # RFC 6750: no credentials at all carries no error code.
    assert "error=" not in challenge
    assert world.inner.calls == 0


def test_a_token_in_the_query_string_is_never_read(world: _World) -> None:
    token = _token("sub-jason")
    assert world.get(params={"access_token": token}).status_code == 401
    assert world.get(params={"token": token}).status_code == 401
    assert world.inner.calls == 0


@pytest.mark.parametrize(
    "header",
    ["Basic dXNlcjpwYXNz", "Bearer", "Bearer a b", "Bearer  padded", "token abc"],
)
def test_a_malformed_authorization_header_is_refused(world: _World, header: str) -> None:
    response = world.client.get("/mcp", headers={"Authorization": header})
    assert response.status_code == 401
    assert world.inner.calls == 0


def test_two_authorization_headers_are_refused(world: _World) -> None:
    good = f"Bearer {_token('sub-jason')}"
    response = world.client.get(
        "/mcp",
        headers=[("Authorization", good), ("Authorization", good)],
    )
    assert response.status_code == 401
    assert world.inner.calls == 0


def test_the_scheme_is_case_insensitive(world: _World) -> None:
    response = world.client.get("/mcp", headers={"Authorization": f"bEaReR {_token('sub-jason')}"})
    assert response.text == str(world.jason.data_dir)


# --- authenticated but not provisioned ---------------------------------------------


def test_a_verified_but_unmapped_identity_is_forbidden(world: _World) -> None:
    response = world.get(_token("sub-stranger"))
    assert response.status_code == 403
    assert world.inner.calls == 0


def test_an_identity_mapped_to_a_tenant_outside_the_registry_is_forbidden(world: _World) -> None:
    response = world.get(_token("sub-ghost"))
    assert response.status_code == 403
    # Indistinguishable from an unmapped identity: no oracle for which tenants exist.
    assert response.text == world.get(_token("sub-stranger")).text
    assert world.inner.calls == 0


def test_a_non_http_scope_is_closed_not_passed_through(world: _World) -> None:
    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "websocket.connect"}

    guard = BearerRoutingASGIApp(
        world.inner, world.index, world.identities, SETTINGS, lambda _t: _KEY.public_key()
    )
    asyncio.run(guard({"type": "websocket", "headers": []}, receive, send))
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert world.inner.calls == 0


# --- discovery ---------------------------------------------------------------------


def test_protected_resource_metadata_names_the_resource_and_issuer(world: _World) -> None:
    for path in (
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
    ):
        document = world.client.get(path).json()
        assert document["resource"] == AUDIENCE
        assert document["authorization_servers"] == [ISSUER]
        assert document["bearer_methods_supported"] == ["header"]
        assert document["scopes_supported"] == ["mcp"]


def test_metadata_is_public_and_needs_no_credentials(world: _World) -> None:
    assert world.client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200
    assert world.inner.calls == 0


# --- configuration is refused loudly ------------------------------------------------


@pytest.mark.parametrize(
    ("issuer", "audience", "jwks"),
    [
        ("http://idp.example.com", AUDIENCE, f"{ISSUER}/jwks"),
        (ISSUER, "http://wingman.example.com/mcp", f"{ISSUER}/jwks"),
        (ISSUER, AUDIENCE, "http://idp.example.com/jwks"),
        (ISSUER, f"{AUDIENCE}#frag", f"{ISSUER}/jwks"),
        ("idp.example.com", AUDIENCE, f"{ISSUER}/jwks"),
    ],
)
def test_insecure_or_malformed_oauth_urls_are_refused(
    issuer: str, audience: str, jwks: str
) -> None:
    with pytest.raises(OAuthConfigError):
        build_oauth_settings(issuer, audience, jwks)


def test_loopback_http_is_allowed_for_local_development() -> None:
    settings = build_oauth_settings(
        "http://127.0.0.1:9000", "http://localhost:8787/mcp", "http://127.0.0.1:9000/jwks"
    )
    assert settings.audience == "http://localhost:8787/mcp"


def test_binding_without_the_legacy_route_raises_rather_than_silently_skipping() -> None:
    with pytest.raises(RuntimeError, match="no Starlette route"):
        bind_oauth_routing(
            Starlette(routes=[]),
            "/mcp/{token}",
            "/mcp",
            TenantIndex([]),
            IdentityMap({}),
            SETTINGS,
            lambda _t: None,
        )


def test_identity_map_loads_and_links(tmp_path: Path) -> None:
    path = tmp_path / "identities.toml"
    path.write_text(
        f"""
[[identity]]
iss = "{ISSUER}"
sub = "a"
slug = "jason"

[[identity]]
iss = "{ISSUER}"
sub = "b"
slug = "jason"
""",
        encoding="utf-8",
    )
    identities = IdentityMap.from_toml(path)
    assert len(identities) == 2
    assert identities.slug_for(ISSUER, "a") == identities.slug_for(ISSUER, "b") == "jason"
    assert identities.slug_for(ISSUER, "c") is None
    assert identities.slug_for("https://other.example.com", "a") is None


@pytest.mark.parametrize(
    "body",
    [
        'identity = "nope"',
        f'[[identity]]\niss = "{ISSUER}"\nsub = "a"\n',
        f'[[identity]]\niss = "{ISSUER}"\nsub = ""\nslug = "x"\n',
        (
            f'[[identity]]\niss = "{ISSUER}"\nsub = "a"\nslug = "x"\n\n'
            f'[[identity]]\niss = "{ISSUER}"\nsub = "a"\nslug = "y"\n'
        ),
        "this is not toml [",
    ],
    ids=["not-a-table-array", "missing-slug", "empty-sub", "duplicate-identity", "bad-toml"],
)
def test_a_bad_identity_map_is_refused(tmp_path: Path, body: str) -> None:
    path = tmp_path / "identities.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(IdentityMapError):
        IdentityMap.from_toml(path)


def test_a_missing_identity_map_is_refused(tmp_path: Path) -> None:
    with pytest.raises(IdentityMapError):
        IdentityMap.from_toml(tmp_path / "absent.toml")


# --- the operator-facing wiring in mcp_server ---------------------------------------


def _oauth_argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--http",
        "--tenant-registry",
        str(tmp_path / "tenants.toml"),
        "--oauth-issuer",
        ISSUER,
        "--oauth-audience",
        AUDIENCE,
        "--oauth-jwks-uri",
        f"{ISSUER}/jwks",
        "--oauth-identities",
        str(tmp_path / "identities.toml"),
        *extra,
    ]


def test_partial_oauth_flags_refuse_to_start(tmp_path: Path) -> None:
    from wingman.mcp_server import main

    argv = _oauth_argv(tmp_path)
    del argv[argv.index("--oauth-jwks-uri") : argv.index("--oauth-jwks-uri") + 2]
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2


def test_oauth_flags_without_a_tenant_registry_refuse_to_start(tmp_path: Path) -> None:
    from wingman.mcp_server import main

    argv = _oauth_argv(tmp_path)
    del argv[argv.index("--tenant-registry") : argv.index("--tenant-registry") + 2]
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2


def _namespace(tmp_path: Path, identities_body: str) -> Any:
    import argparse

    (tmp_path / "identities.toml").write_text(identities_body, encoding="utf-8")
    return argparse.Namespace(
        oauth_issuer=ISSUER,
        oauth_audience=AUDIENCE,
        oauth_jwks_uri=f"{ISSUER}/jwks",
        oauth_identities=str(tmp_path / "identities.toml"),
    )


def test_bind_oauth_adds_routes_when_everything_lines_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from wingman.mcp_server import _bind_oauth

    world = _World(tmp_path / "w")
    app = Starlette(routes=[Route("/mcp/{token}", endpoint=world.inner)])
    args = _namespace(tmp_path, f'[[identity]]\niss = "{ISSUER}"\nsub = "a"\nslug = "jason"\n')
    _bind_oauth(app, args, "", world.index)
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/mcp" in paths
    assert "/.well-known/oauth-protected-resource/mcp" in paths
    assert "OAuth bearer" in capsys.readouterr().out


def test_bind_oauth_refuses_an_identity_naming_a_tenant_not_in_the_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from wingman.mcp_server import _bind_oauth

    world = _World(tmp_path / "w")
    app = Starlette(routes=[Route("/mcp/{token}", endpoint=world.inner)])
    args = _namespace(tmp_path, f'[[identity]]\niss = "{ISSUER}"\nsub = "a"\nslug = "nobody"\n')
    with pytest.raises(SystemExit) as raised:
        _bind_oauth(app, args, "", world.index)
    assert raised.value.code == 1
    assert "nobody" in capsys.readouterr().err
    assert "/mcp" not in {getattr(route, "path", None) for route in app.routes}


def test_bind_oauth_refuses_a_malformed_identity_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from wingman.mcp_server import _bind_oauth

    world = _World(tmp_path / "w")
    app = Starlette(routes=[Route("/mcp/{token}", endpoint=world.inner)])
    args = _namespace(tmp_path, "this is not toml [")
    with pytest.raises(SystemExit) as raised:
        _bind_oauth(app, args, "", world.index)
    assert raised.value.code == 1
    assert "ERROR" in capsys.readouterr().err
