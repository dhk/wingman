"""A tenant can be told their OWN URLs (#412).

The gate that hid these was mis-scoped rather than wrong. Another tenant's
URL is a total compromise of that workspace — the URL contains the
capability token. A tenant's own URL discloses nothing: they already hold
the token, and the request asking is authenticated BY that token.

What it cost was concrete. The web UI is the upload surface, so a tenant
who could not find its address had to hand their CV or LinkedIn export to
whoever runs the machine. The gate did not protect their privacy; it made
somebody else handle their private data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from wingman.infrastructure.config import ENV_DATA_DIR
from wingman.infrastructure.tenant_asgi import (
    TenantRoutingASGIApp,
    current_request_origin,
    funnel_prefix,
    request_origin,
    resolve_prefix,
)
from wingman.infrastructure.tenants import Tenant, TenantIndex

TOKEN = "tok-dhk"


def _scope(*, host: str, path: str, proto: str | None = None, port: int = 8789) -> Scope:
    headers = [(b"host", host.encode())]
    if proto:
        headers.append((b"x-forwarded-proto", proto.encode()))
    return {
        "headers": headers,
        "raw_path": path.encode(),
        "path": path,
        "scheme": "http",
        "server": ("127.0.0.1", port),
    }


#: What 'tailscale serve status --json' actually returns on this box.
FUNNEL = json.dumps(
    {
        "Web": {
            "lobster.tail08dfce.ts.net:443": {
                "Handlers": {
                    "/shared": {"Proxy": "http://127.0.0.1:8789"},
                    "/alexandria": {"Proxy": "http://127.0.0.1:8797"},
                }
            }
        }
    }
)


def test_a_stripping_funnel_leaves_no_prefix_in_the_request() -> None:
    """The assumption that shipped a 404 (#417).

    A Tailscale funnel mounted with '--set-path /shared' REMOVES the prefix
    before proxying, so the process is handed '/mcp/<token>' and the
    request cannot report where the public address begins. The previous
    test here fed '/shared/mcp/<token>' into the scope and asserted the
    prefix survived — it encoded my assumption instead of the funnel's
    behaviour, and passed against code that 404s in production.
    """
    origin = request_origin(
        _scope(host="lobster.tail08dfce.ts.net", path=f"/mcp/{TOKEN}", proto="https"), TOKEN
    )

    assert origin is not None
    assert origin.prefix == "", "a stripping front leaves nothing to derive"


def test_the_live_funnel_supplies_what_the_request_cannot() -> None:
    """Ground truth, and the same discovery wingman-register-service.sh has
    done since #288 for the same reason."""
    origin = request_origin(
        _scope(host="lobster.tail08dfce.ts.net", path=f"/mcp/{TOKEN}", proto="https", port=8789),
        TOKEN,
    )
    assert origin is not None

    prefix = resolve_prefix(origin, capture=lambda _argv: FUNNEL)

    assert prefix == "/shared"
    assert origin.ui_url(prefix) == f"https://lobster.tail08dfce.ts.net/shared/ui/{TOKEN}/"


def test_an_unreadable_funnel_means_no_public_url_is_offered() -> None:
    """Silence beats a URL that 404s — the property #412 promised and then
    broke."""
    origin = request_origin(
        _scope(host="lobster.tail08dfce.ts.net", path=f"/mcp/{TOKEN}", proto="https", port=8789),
        TOKEN,
    )
    assert origin is not None

    assert resolve_prefix(origin, capture=lambda _argv: None) is None


def test_two_mounts_for_one_port_is_ambiguous_not_a_guess() -> None:
    """Guessing which mount a given tenant came through is exactly the
    wrong-URL failure this exists to prevent."""
    ambiguous = json.dumps(
        {
            "Web": {
                "h:443": {
                    "Handlers": {
                        "/shared": {"Proxy": "http://127.0.0.1:8789"},
                        "/also": {"Proxy": "http://127.0.0.1:8789"},
                    }
                }
            }
        }
    )
    assert funnel_prefix(8789, capture=lambda _argv: ambiguous) is None


def test_a_front_that_announces_its_prefix_is_believed_first() -> None:
    scope = _scope(host="public.example", path=f"/mcp/{TOKEN}", proto="https", port=8789)
    scope["headers"].append((b"x-forwarded-prefix", b"/wingman"))
    origin = request_origin(scope, TOKEN)
    assert origin is not None

    prefix = resolve_prefix(
        origin, headers={"x-forwarded-prefix": "/wingman"}, capture=lambda _argv: FUNNEL
    )

    assert prefix == "/wingman", "an explicit announcement beats discovery"


def test_loopback_needs_no_prefix_and_never_shells_out() -> None:
    """A request that arrived on 127.0.0.1 is already the complete address."""
    origin = request_origin(_scope(host="127.0.0.1:8789", path=f"/mcp/{TOKEN}", port=8789), TOKEN)
    assert origin is not None

    def explode(_argv: list[str]) -> str | None:
        raise AssertionError("must not run tailscale for a loopback request")

    assert resolve_prefix(origin, capture=explode) == ""
    assert origin.mcp_url("") == f"http://127.0.0.1:8789/mcp/{TOKEN}"


def test_a_forwarded_host_wins_over_the_host_header() -> None:
    scope = _scope(host="127.0.0.1:8789", path=f"/shared/mcp/{TOKEN}", proto="https")
    scope["headers"].append((b"x-forwarded-host", b"public.example"))

    origin = request_origin(scope, TOKEN)

    assert origin is not None
    assert origin.authority == "public.example"


def test_a_request_with_no_host_at_all_is_unreconstructable() -> None:
    """Reported as unknown rather than guessed: a wrong URL is one that does
    not work, and pretending otherwise sends somebody hunting."""
    assert request_origin({"headers": [], "path": f"/mcp/{TOKEN}"}, TOKEN) is None


def _tenant(tmp_path: Path) -> Tenant:
    from wingman.infrastructure.storage import Storage

    data_dir = tmp_path / "dhk"
    for directory in (data_dir, data_dir / "inbox", data_dir / "reports"):
        directory.mkdir(parents=True, exist_ok=True)
    (data_dir / "mcp-http-token").write_text(TOKEN, encoding="utf-8")
    Storage(data_dir / "wingman.db").close()
    return Tenant(slug="dhk", data_dir=data_dir)


class _EchoOriginApp:
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        origin = current_request_origin()
        await PlainTextResponse(origin.ui_url() if origin else "none")(scope, receive, send)


def test_the_origin_is_bound_for_the_request_and_cleared_after(tmp_path: Path) -> None:
    index = TenantIndex([_tenant(tmp_path)])
    app = Starlette(
        routes=[Route("/mcp/{token}", endpoint=TenantRoutingASGIApp(_EchoOriginApp(), index))]
    )

    body = TestClient(app).get(f"/mcp/{TOKEN}").text

    assert body.endswith(f"/ui/{TOKEN}/")
    assert current_request_origin() is None, "must not leak past the request"


def test_outside_a_request_the_tool_says_so_rather_than_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stdio and the CLI have no public address. Printing a plausible one
    would send somebody chasing a URL that does not work."""
    from typer.testing import CliRunner

    from wingman.cli.main import app as cli
    from wingman.mcp_server import my_urls

    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    CliRunner().invoke(cli, ["init"])

    answer = my_urls()

    assert "did not arrive over HTTP" in answer
    assert "http://" not in answer


def test_the_tool_needs_no_operator_privilege() -> None:
    """RFC-068 gates tools that act on OTHER people's data. This shows the
    caller their own address, proved by the token the request carried —
    gating it would put somebody's own address behind their own address."""
    import inspect

    from wingman import mcp_server

    source = inspect.getsource(mcp_server.my_urls)
    assert "operator_only_refusal" not in source


def test_the_docstring_carries_the_secret_warning_and_the_upload_reason() -> None:
    """The token now lands in a transcript rather than only a config file.
    That is a real if modest increase in exposure, and the output says so
    rather than glossing it."""
    from wingman import mcp_server

    doc = mcp_server.my_urls.__doc__ or ""
    assert "bearer token" in doc
    assert "password manager" in doc
    assert "upload" in doc


def test_both_urls_are_printed_because_the_difference_is_not_guessable(tmp_path: Path) -> None:
    """They differ by one path segment, '/mcp' vs '/ui', and nobody should
    have to work that out."""
    index = TenantIndex([_tenant(tmp_path)])

    class _CallTool:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            from wingman.mcp_server import my_urls

            await PlainTextResponse(my_urls())(scope, receive, send)

    app = Starlette(
        routes=[Route("/mcp/{token}", endpoint=TenantRoutingASGIApp(_CallTool(), index))]
    )

    body = TestClient(app).get(f"/mcp/{TOKEN}").text

    assert f"/mcp/{TOKEN}" in body
    assert f"/ui/{TOKEN}/" in body
    assert "password" in body
