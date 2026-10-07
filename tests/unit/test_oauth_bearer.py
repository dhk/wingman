"""OAuth bearer validation for the shared multi-tenant process (RFC-081 draft spike).

The properties that matter, each tested directly rather than assumed:
  * a valid token reaches exactly the tenant its identity names, and two
    identities in flight at once never see each other's Config;
  * every way a token can be wrong is refused BEFORE the inner app runs;
  * the capability-token route is untouched by all of this.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import MutableMapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
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
    jwks_key_resolver,
)
from wingman.infrastructure.tenant_asgi import TenantSessionBindings, bind_tenant_routing
from wingman.infrastructure.tenants import Tenant, TenantIndex

ISSUER = "https://idp.example.com"
AUDIENCE = "https://wingman.example.com/mcp"
SETTINGS = OAuthSettings(
    issuer=ISSUER, audience=AUDIENCE, jwks_uri=f"{ISSUER}/jwks", scopes=("mcp",)
)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_EC_KEY = ec.generate_private_key(ec.SECP256R1())


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


class _SessionInner:
    """A minimal streamable-HTTP session transport.

    The initialize response creates a session whose task keeps the tenant
    context it inherited. Later requests resume that task by header, exactly
    the boundary the authentication wrappers must protect.
    """

    def __init__(self) -> None:
        self._next_session = 0
        self._tenants: dict[str, str] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {
            bytes(key).lower(): bytes(value).decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        session_id = headers.get(b"mcp-session-id")
        if scope.get("method") == "DELETE":
            await PlainTextResponse("terminated")(scope, receive, send)
            return
        if session_id is None:
            self._next_session += 1
            session_id = f"session-{self._next_session}"
            self._tenants[session_id] = str(load_config().data_dir)
            await PlainTextResponse(
                self._tenants[session_id], headers={"Mcp-Session-Id": session_id}
            )(scope, receive, send)
            return
        tenant = self._tenants.get(session_id)
        if tenant is None:
            await PlainTextResponse("unknown session", status_code=404)(scope, receive, send)
            return
        await PlainTextResponse(tenant)(scope, receive, send)


def _token(
    sub: str | None = "sub-jason",
    *,
    key: Any = _KEY,
    algorithm: str = "RS256",
    headers: dict[str, Any] | None = None,
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
    return jwt.encode(claims, key, algorithm=algorithm, headers=headers)


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
        settings: OAuthSettings = SETTINGS,
        raise_server_exceptions: bool = True,
        session_bindings: TenantSessionBindings | None = None,
        record_pending: Any = None,
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
        bind_tenant_routing(
            self.app,
            "/mcp/{token}",
            self.index,
            session_bindings=session_bindings,
        )
        bind_oauth_routing(
            self.app,
            "/mcp/{token}",
            "/mcp",
            self.index,
            self.identities,
            settings,
            resolve_key or (lambda _token: _KEY.public_key()),
            record_pending=record_pending,
        )
        self.client = TestClient(self.app, raise_server_exceptions=raise_server_exceptions)

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


def test_verified_unknown_identity_is_recorded_but_still_receives_no_access(
    tmp_path: Path,
) -> None:
    from wingman.infrastructure.oauth_onboarding import (
        OAuthOnboardingStore,
        onboarding_path_for,
    )

    identities = tmp_path / "identities.toml"
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    world = _World(tmp_path / "world", record_pending=store.record_pending)

    response = world.get(_token("verified-but-not-approved"))

    assert response.status_code == 403
    assert world.inner.calls == 0
    assert [item.subject for item in store.pending()] == ["verified-but-not-approved"]


def test_oauth_calls_preserve_truthful_http_origin_guidance(tmp_path: Path) -> None:
    from wingman.infrastructure.storage import Storage
    from wingman.mcp_server import my_urls

    class _CallMyUrls:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await PlainTextResponse(my_urls())(scope, receive, send)

    world = _World(tmp_path, inner=_CallMyUrls())
    Storage(world.jason.data_dir / "wingman.db").close()

    response = world.get(
        _token("sub-jason"),
        headers={"Host": "wingman.example.com", "X-Forwarded-Proto": "https"},
    )

    assert response.status_code == 200
    assert "OAuth-authenticated HTTP session" in response.text
    assert "did not arrive over HTTP" not in response.text
    assert "/ui/" not in response.text


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


def test_an_oauth_tenant_cannot_resume_a_capability_tenants_session(tmp_path: Path) -> None:
    world = _World(tmp_path, inner=_SessionInner())
    opened = world.client.get("/mcp/tok-jason")
    session_id = opened.headers["mcp-session-id"]

    crossed = world.get(_token("sub-bob"), headers={"Mcp-Session-Id": session_id})

    assert crossed.status_code == 403
    assert "session" in crossed.text.lower()


def test_a_capability_tenant_cannot_resume_an_oauth_tenants_session(tmp_path: Path) -> None:
    world = _World(tmp_path, inner=_SessionInner())
    opened = world.get(_token("sub-jason"))
    session_id = opened.headers["mcp-session-id"]

    crossed = world.client.get("/mcp/tok-bob", headers={"Mcp-Session-Id": session_id})

    assert crossed.status_code == 403
    assert "session" in crossed.text.lower()


def test_expired_session_bindings_fail_closed_instead_of_crossing_tenants(
    tmp_path: Path,
) -> None:
    clock = [100.0]
    bindings = TenantSessionBindings(
        ttl_seconds=30.0,
        max_bindings=10,
        monotonic=lambda: clock[0],
    )
    world = _World(tmp_path, inner=_SessionInner(), session_bindings=bindings)
    opened = world.client.get("/mcp/tok-jason")
    session_id = opened.headers["mcp-session-id"]
    clock[0] += 31.0

    owner_resume = world.client.get("/mcp/tok-jason", headers={"Mcp-Session-Id": session_id})
    crossed_resume = world.get(_token("sub-bob"), headers={"Mcp-Session-Id": session_id})

    assert owner_resume.status_code == 404
    assert crossed_resume.status_code == 404


def test_session_binding_table_refuses_new_sessions_at_its_hard_limit(
    tmp_path: Path,
) -> None:
    bindings = TenantSessionBindings(ttl_seconds=300.0, max_bindings=1)
    world = _World(tmp_path, inner=_SessionInner(), session_bindings=bindings)

    assert world.client.get("/mcp/tok-jason").status_code == 200
    refused = world.client.get("/mcp/tok-jason")

    assert refused.status_code == 503
    assert "session capacity" in refused.text.lower()


def test_successful_session_delete_releases_the_outer_tenant_binding(tmp_path: Path) -> None:
    world = _World(tmp_path, inner=_SessionInner())
    opened = world.client.get("/mcp/tok-jason")
    session_id = opened.headers["mcp-session-id"]

    deleted = world.client.delete("/mcp/tok-jason", headers={"Mcp-Session-Id": session_id})
    after_delete = world.get(_token("sub-bob"), headers={"Mcp-Session-Id": session_id})

    assert deleted.status_code == 200
    assert after_delete.status_code == 404


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


@pytest.mark.parametrize("scope", [1, {"mcp": True}, ["mcp", 7]])
def test_a_malformed_scope_claim_is_refused_before_the_inner_app_runs(
    tmp_path: Path, scope: object
) -> None:
    world = _World(tmp_path, raise_server_exceptions=False)

    response = world.get(_token("sub-jason", scope=scope))

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]
    assert world.inner.calls == 0


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ({}, ()),
        ({"scope": "mcp profile"}, ("mcp", "profile")),
        ({"scope": ["mcp", "profile"]}, ("mcp", "profile")),
        ({"scp": ("mcp",)}, ("mcp",)),
    ],
)
def test_supported_scope_claim_shapes_are_normalized(
    claim: dict[str, object], expected: tuple[str, ...]
) -> None:
    from wingman.infrastructure.oauth_bearer import validate_access_token

    identity = validate_access_token(
        _token("sub-jason", **claim), SETTINGS, lambda _token: _KEY.public_key()
    )

    assert identity.scopes == expected


class _JwksState:
    def __init__(self, keys: list[dict[str, Any]]) -> None:
        self.keys = keys
        self.status = 200
        self.requests = 0
        self.lock = threading.Lock()


@contextmanager
def _jwks_server(state: _JwksState) -> Any:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — stdlib callback name
            with state.lock:
                state.requests += 1
                status = state.status
                body = json.dumps({"keys": state.keys}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/jwks"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _public_jwk(key: Any, kid: str, algorithm: str) -> dict[str, Any]:
    algorithm_class = jwt.algorithms.get_default_algorithms()[algorithm]
    jwk = algorithm_class.to_jwk(key.public_key(), as_dict=True)
    return {**jwk, "kid": kid, "alg": algorithm, "use": "sig"}


@pytest.mark.parametrize(
    ("key", "kid", "algorithm"),
    [(_KEY, "rsa-key", "RS256"), (_EC_KEY, "ec-key", "ES256")],
)
def test_the_production_jwks_resolver_accepts_supported_keys_end_to_end(
    tmp_path: Path, key: Any, kid: str, algorithm: str
) -> None:
    state = _JwksState([_public_jwk(key, kid, algorithm)])
    with _jwks_server(state) as uri:
        settings = OAuthSettings(issuer=ISSUER, audience=AUDIENCE, jwks_uri=uri)
        world = _World(tmp_path, settings=settings, resolve_key=jwks_key_resolver(uri))

        response = world.get(
            _token("sub-jason", key=key, algorithm=algorithm, headers={"kid": kid})
        )

    assert response.status_code == 200
    assert response.text == str(world.jason.data_dir)
    assert state.requests == 1


def test_unknown_kids_share_one_failed_refresh_during_the_cooldown() -> None:
    state = _JwksState([_public_jwk(_KEY, "known", "RS256")])
    with _jwks_server(state) as uri:
        resolver = jwks_key_resolver(uri)
        tokens = [_token(headers={"kid": f"unknown-{position}"}) for position in range(12)]
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(lambda token: _resolve_error(resolver, token), tokens))

    assert all(isinstance(result, jwt.PyJWKClientError) for result in results)
    assert state.requests == 1


def _resolve_error(resolver: Any, token: str) -> Exception | None:
    try:
        resolver(token)
    except Exception as exc:  # noqa: BLE001 — test captures the resolver contract
        return exc
    return None


def test_a_cached_valid_key_still_works_during_an_unknown_key_cooldown() -> None:
    state = _JwksState([_public_jwk(_KEY, "known", "RS256")])
    known = _token(headers={"kid": "known"})
    unknown = _token(headers={"kid": "unknown"})
    with _jwks_server(state) as uri:
        resolver = jwks_key_resolver(uri)
        assert resolver(known) is not None
        with pytest.raises(jwt.PyJWKClientError):
            resolver(unknown)
        before_cached_lookup = state.requests
        assert resolver(known) is not None

    assert state.requests == before_cached_lookup


def test_cached_keys_expire_and_a_removed_key_is_refused_after_refresh() -> None:
    state = _JwksState([_public_jwk(_KEY, "rotated-out", "RS256")])
    token = _token(headers={"kid": "rotated-out"})
    clock = [100.0]
    with _jwks_server(state) as uri:
        resolver = jwks_key_resolver(uri, cache_ttl_seconds=300.0, monotonic=lambda: clock[0])
        assert resolver(token) is not None
        state.keys = [_public_jwk(_OTHER_KEY, "replacement", "RS256")]
        clock[0] += 301.0

        with pytest.raises(jwt.PyJWKClientError):
            resolver(token)

    assert state.requests == 2


def test_expired_keys_fail_closed_when_the_jwks_endpoint_is_down() -> None:
    state = _JwksState([_public_jwk(_KEY, "known", "RS256")])
    token = _token(headers={"kid": "known"})
    clock = [100.0]
    with _jwks_server(state) as uri:
        resolver = jwks_key_resolver(uri, cache_ttl_seconds=300.0, monotonic=lambda: clock[0])
        assert resolver(token) is not None
        state.status = 503
        clock[0] += 301.0

        with pytest.raises(jwt.PyJWKClientError):
            resolver(token)

    assert state.requests == 2


def test_a_jwks_outage_is_a_401_and_never_reaches_the_inner_app(tmp_path: Path) -> None:
    state = _JwksState([_public_jwk(_KEY, "known", "RS256")])
    state.status = 503
    with _jwks_server(state) as uri:
        settings = OAuthSettings(issuer=ISSUER, audience=AUDIENCE, jwks_uri=uri)
        world = _World(tmp_path, settings=settings, resolve_key=jwks_key_resolver(uri))
        response = world.get(_token(headers={"kid": "known"}))

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


def test_a_stripping_front_keeps_the_metadata_url_inside_its_public_mount(
    tmp_path: Path,
) -> None:
    settings = OAuthSettings(
        issuer=ISSUER,
        audience="https://wingman.example.com/oauth-spike/mcp",
        jwks_uri=f"{ISSUER}/jwks",
    )
    world = _World(tmp_path)
    world.app.router.routes.clear()
    world.app.router.routes.append(Route("/mcp/{token}", endpoint=world.inner))
    bind_oauth_routing(
        world.app,
        "/mcp/{token}",
        "/mcp",
        world.index,
        world.identities,
        settings,
        lambda _token: _KEY.public_key(),
    )

    response = TestClient(world.app).get("/mcp")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        'Bearer resource_metadata="https://wingman.example.com/oauth-spike/'
        '.well-known/oauth-protected-resource/mcp"'
    )
    assert TestClient(world.app).get("/.well-known/oauth-protected-resource/mcp").status_code == 200


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


def test_explicitly_empty_oauth_flags_refuse_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman import mcp_server

    monkeypatch.setattr(
        mcp_server,
        "_run_tenant_server",
        lambda *_args: pytest.fail("empty OAuth values reached server startup"),
    )
    argv = [
        "--http",
        "--tenant-registry",
        str(tmp_path / "tenants.toml"),
        "--oauth-issuer",
        "",
        "--oauth-audience",
        "",
        "--oauth-jwks-uri",
        "",
        "--oauth-identities",
        "",
    ]

    with pytest.raises(SystemExit) as raised:
        mcp_server.main(argv)

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
