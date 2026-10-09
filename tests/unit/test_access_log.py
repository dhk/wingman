from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from wingman.infrastructure.access_log import (
    AccessLogMiddleware,
    loggable_path,
    sanitize_user_agent,
)

TOKEN = "cap_9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c"
CODE = "oauthcode_SECRET_abc123"


MOUNTS = frozenset({"/shared"})
# The real capability-token shape: secrets.token_urlsafe(24) -> 32 URL-safe
# characters. All-lowercase is a legal token, so it must not look like a mount.
LOWER_TOKEN = "a" * 32


@pytest.mark.parametrize(
    ("seen", "logged"),
    [
        ("/mcp", "/mcp"),
        ("/shared/mcp", "/shared/mcp"),
        ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource"),
        ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource/mcp"),
        (
            "/.well-known/oauth-protected-resource/shared/mcp",
            "/.well-known/oauth-protected-resource/shared/mcp",
        ),
        (
            "/shared/.well-known/oauth-protected-resource/mcp",
            "/shared/.well-known/oauth-protected-resource/mcp",
        ),
        ("/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server"),
        ("/login", "/login"),
        ("/oauth/callback", "/oauth/callback"),
        ("/shared/setup/", "/shared/setup/"),
        ("/setup/keys", "/setup/keys"),
        ("/health", "/health"),
        # Capability routes: the token IS the credential. Never verbatim.
        (f"/mcp/{TOKEN}", "/mcp/<token>"),
        (f"/shared/mcp/{TOKEN}", "/shared/mcp/<token>"),
        (f"/ui/{TOKEN}/", "/ui/<token>/…"),
        (f"/ui/{TOKEN}/file/inbox/Private Resume.pdf", "/ui/<token>/…"),
        (f"/admin/{TOKEN}/installations", "/admin/<token>/…"),
        # Anything not on the allowlist could be a mistyped capability URL.
        (f"/{TOKEN}", "<other>"),
        ("/wp-admin/setup-config.php", "<other>"),
        (f"/mcp-{TOKEN}", "<other>"),
        # Codex review on #571: an unconfigured leading segment and a free-form
        # well-known suffix were both logged verbatim.
        (f"/{LOWER_TOKEN}/mcp", "<other>"),
        (f"/{LOWER_TOKEN}/mcp/{TOKEN}", "<other>"),
        (f"/.well-known/{LOWER_TOKEN}", "/.well-known/<other>"),
        (
            f"/.well-known/oauth-protected-resource/{LOWER_TOKEN}",
            "/.well-known/oauth-protected-resource/…",
        ),
        (f"/shared/.well-known/{LOWER_TOKEN}/x", "/shared/.well-known/<other>"),
    ],
)
def test_paths_are_logged_only_in_known_safe_forms(seen: str, logged: str) -> None:
    assert loggable_path(seen, MOUNTS) == logged


def test_without_a_configured_mount_no_leading_segment_is_trusted() -> None:
    assert loggable_path("/shared/mcp") == "<other>"
    assert loggable_path(f"/shared/mcp/{TOKEN}") == "<other>"


def test_user_agent_keeps_only_the_leading_product_token() -> None:
    assert sanitize_user_agent(b'Claude-User/1.0 "x"\r\nInjected: yes') == "Claude-User/1.0"
    assert sanitize_user_agent(b"python-httpx/0.28.1") == "python-httpx/0.28.1"
    assert len(sanitize_user_agent(b"A" * 200)) == 40
    assert sanitize_user_agent(b'"quoted"') == "-"
    assert sanitize_user_agent(None) == "-"


def _app() -> Starlette:
    async def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def unauthorized(request: Request) -> PlainTextResponse:
        return PlainTextResponse("Unauthorized", status_code=401)

    async def boom(request: Request) -> PlainTextResponse:
        raise RuntimeError("inner failure")

    return Starlette(
        routes=[
            Route("/mcp", unauthorized, methods=["GET", "POST"]),
            Route("/mcp/{token}", ok, methods=["GET", "POST"]),
            Route("/ui/{token}/file/{path:path}", ok),
            Route("/oauth/callback", ok),
            Route("/health", boom),
        ]
    )


def _access_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "wingman.access"]


def test_no_secret_reaches_any_log_record(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(AccessLogMiddleware(_app()))
    with caplog.at_level(logging.DEBUG):
        client.post(
            f"/mcp/{TOKEN}?session={TOKEN}",
            headers={"Authorization": f"Bearer {TOKEN}", "Cookie": f"s={TOKEN}"},
            content=f'{{"token": "{TOKEN}"}}',
        )
        client.get(f"/ui/{TOKEN}/file/inbox/secret-plans.pdf")
        client.get(f"/oauth/callback?code={CODE}&state={CODE}")

    # Every server-side record. httpx/httpcore are the TEST CLIENT logging its
    # own outgoing URLs; the shared service makes no such requests.
    everything = "\n".join(
        record.getMessage()
        for record in caplog.records
        if not record.name.startswith(("httpx", "httpcore"))
    )
    assert TOKEN not in everything
    assert CODE not in everything
    assert "secret-plans" not in everything
    assert len(_access_records(caplog)) == 3


def test_each_request_logs_method_path_status_and_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(AccessLogMiddleware(_app()))
    with caplog.at_level(logging.INFO, logger="wingman.access"):
        client.post("/mcp", headers={"User-Agent": "Claude-User/1.0"})

    (record,) = _access_records(caplog)
    message = record.getMessage()
    assert message.startswith("method=POST path=/mcp status=401 ms=")
    assert message.endswith('ua="Claude-User/1.0"')
    assert record.levelno == logging.INFO


def test_an_exception_is_logged_as_500_and_still_raised(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(AccessLogMiddleware(_app()))
    with caplog.at_level(logging.INFO, logger="wingman.access"):
        with pytest.raises(RuntimeError, match="inner failure"):
            client.get("/health")

    (record,) = _access_records(caplog)
    assert "path=/health status=500" in record.getMessage()


def test_a_cancelled_request_is_aborted_not_500(caplog: pytest.LogCaptureFixture) -> None:
    import anyio

    async def started_then_cancelled(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise anyio.get_cancelled_exc_class()()

    async def cancelled_before_start(scope: Any, receive: Any, send: Any) -> None:
        raise anyio.get_cancelled_exc_class()()

    async def noop_send(message: Any) -> None:
        return None

    async def run(inner: Any) -> None:
        scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
        with pytest.raises(anyio.get_cancelled_exc_class()):
            await AccessLogMiddleware(inner)(scope, None, noop_send)

    with caplog.at_level(logging.INFO, logger="wingman.access"):
        anyio.run(run, cancelled_before_start)
        anyio.run(run, started_then_cancelled)

    before, after = (record.getMessage() for record in _access_records(caplog))
    assert "status=- " in before and before.endswith(" aborted=1")
    assert "status=200 " in after and after.endswith(" aborted=1")


def test_non_http_scopes_pass_through_unlogged(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    import anyio

    async def run() -> None:
        await AccessLogMiddleware(inner)({"type": "lifespan"}, None, None)

    with caplog.at_level(logging.INFO, logger="wingman.access"):
        anyio.run(run)
    assert seen == ["lifespan"]
    assert _access_records(caplog) == []


def test_shared_server_serves_its_app_through_the_access_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    import uvicorn

    from wingman import mcp_server

    data_dir = tmp_path / "jason"
    data_dir.mkdir()
    registry = tmp_path / "tenants.toml"
    registry.write_text(f'[[tenant]]\nslug = "jason"\ndata_dir = "{data_dir}"\n', encoding="utf-8")
    served: list[Any] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **_kwargs: served.append(app))
    # _run_tenant_server configures the module-global FastMCP instance and
    # the web UI; give it throwaway ones so no other test sees the change.
    from mcp.server.fastmcp import FastMCP

    monkeypatch.setattr(mcp_server, "server", FastMCP("access-log-test"))
    monkeypatch.setattr("wingman.webui.configure_tenant_index", lambda _index: None)
    monkeypatch.setattr("wingman.webui.register_ui", lambda *_a, **_k: None)
    monkeypatch.setattr("wingman.admin.register_admin", lambda *_a, **_k: None)
    monkeypatch.setattr(mcp_server, "_probe_bind", lambda *_args: None)
    monkeypatch.setattr(
        "wingman.infrastructure.tenant_process.register_reload_handler", lambda *_a: None
    )
    args = argparse.Namespace(
        tenant_registry=str(registry),
        host="127.0.0.1",
        port=8789,
        allowed_host=[],
        oauth_issuer=None,
    )

    mcp_server._run_tenant_server(args, "")

    assert len(served) == 1
    assert isinstance(served[0], AccessLogMiddleware)
    assert served[0].mounts == frozenset()
