"""Security boundary and operator workflow for retiring capability URLs (#616)."""

from __future__ import annotations

import os
import tomllib
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from typer.testing import CliRunner

from wingman.admin import register_admin
from wingman.cli.main import app
from wingman.infrastructure import tenants
from wingman.infrastructure.tenant_asgi import TenantRoutingASGIApp
from wingman.mcp_server import server
from wingman.webui import configure_tenant_index, register_ui

cli = CliRunner()


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    workspace = tmp_path / "alice"
    workspace.mkdir()
    (workspace / "mcp-http-token").write_text("synthetic-secret-token")
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        '# keep this comment\n[defaults]\nfeature_repo = "example/issues"\n'
        f'[[tenant]]\nslug = "alice"\ndata_dir = "{workspace}"\nfunded = true\n'
        "custom = { nested = [1, 2] }\n"
        f'[[tenant]]\nslug = "operator"\ndata_dir = "{tmp_path / "op"}"\nprivileged = true\n'
    )
    identities = tmp_path / "identities.toml"
    identities.write_text(
        '[[identity]]\niss = "https://mcp.example"\nsub = "synthetic-subject"\nslug = "alice"\n'
        '[[identity]]\niss = "https://api.workos.com/user_management/client_example"\n'
        'sub = "synthetic-subject"\nslug = "alice"\n'
    )
    oauth = tmp_path / "oauth.env"
    oauth.write_text("WINGMAN_OAUTH_ARGS=--oauth-issuer https://mcp.example\n")
    web = tmp_path / "web.env"
    web.write_text("WINGMAN_OAUTH_WEB_ARGS=--oauth-web-client-id client_example\n")
    return registry, identities, oauth, web


def command(setup: tuple[Path, Path, Path, Path], *words: str) -> Any:
    registry, identities, oauth, web = setup
    return cli.invoke(
        app,
        [
            "tenant",
            *words,
            "--registry",
            str(registry),
            "--identities",
            str(identities),
            "--oauth-config",
            str(oauth),
            "--oauth-web-config",
            str(web),
        ],
    )


def test_retire_undo_preserves_registry_and_token(setup: tuple[Path, Path, Path, Path]) -> None:
    registry = setup[0]
    original = tomllib.loads(registry.read_text())
    result = command(setup, "retire-url", "alice")
    assert result.exit_code == 0, result.output
    assert "SIGHUP" in result.output
    changed = tomllib.loads(registry.read_text())
    assert changed["tenant"][0].pop("capability_url_retired") is True
    assert changed == original
    assert "# keep this comment" in registry.read_text()
    index = tenants.TenantIndex.from_registry_path(registry)
    assert index.resolve("synthetic-secret-token") is None
    assert index.by_slug("alice") is not None
    result = command(setup, "retire-url", "alice", "--undo")
    assert result.exit_code == 0, result.output
    index.reload(registry)
    assert index.resolve("synthetic-secret-token") is not None


def test_missing_issuer_refuses_force_overrides(setup: tuple[Path, Path, Path, Path]) -> None:
    setup[1].write_text('[[identity]]\niss="https://mcp.example"\nsub="a"\nslug="alice"\n')
    before = setup[0].read_bytes()
    result = command(setup, "retire-url", "alice")
    assert result.exit_code == 1 and "every trusted issuer" in result.output
    assert setup[0].read_bytes() == before
    result = command(setup, "retire-url", "alice", "--force")
    assert result.exit_code == 0 and "WARNING" in result.output


def test_privileged_requires_force(setup: tuple[Path, Path, Path, Path]) -> None:
    result = command(setup, "retire-url", "operator")
    assert result.exit_code == 1 and "privileged" in result.output
    result = command(setup, "retire-url", "operator", "--force")
    assert result.exit_code == 0 and "WARNING" in result.output


def test_migration_is_read_only_and_redacted(setup: tuple[Path, Path, Path, Path]) -> None:
    before = {p: p.read_bytes() for p in setup}
    result = command(setup, "migration")
    assert result.exit_code == 0, result.output
    assert "alice" in result.output and "active" in result.output
    assert "never recorded" in result.output
    for private in ("synthetic-subject", "synthetic-secret-token"):
        assert private not in result.output
    assert before == {p: p.read_bytes() for p in setup}


def test_atomic_failure_preserves_registry(setup: tuple[Path, Path, Path, Path]) -> None:
    before = setup[0].read_bytes()
    with patch("os.replace", side_effect=OSError("synthetic replacement failure")):
        result = command(setup, "retire-url", "alice")
    assert result.exit_code == 1 and "preserved" in result.output
    assert setup[0].read_bytes() == before


@pytest.mark.parametrize("route", ["mcp", "ui", "admin"])
def test_retired_response_equals_unknown(setup: tuple[Path, Path, Path, Path], route: str) -> None:
    result = command(setup, "retire-url", "alice")
    assert result.exit_code == 0, result.output
    index = tenants.TenantIndex.from_registry_path(setup[0])
    if route == "mcp":

        async def inner(request: object) -> PlainTextResponse:
            return PlainTextResponse("success")

        endpoint = Route("/mcp/{token}", inner)
        endpoint.app = TenantRoutingASGIApp(endpoint.app, index)
        client = TestClient(Starlette(routes=[endpoint]))
        suffix = ""
    else:
        configure_tenant_index(index)
        register_ui(server, prefix="/retirement-test")
        register_admin(server)
        client = TestClient(server.streamable_http_app())
        suffix = "/installations" if route == "admin" else "/"
    prefix = "" if route in {"mcp", "admin"} else "/retirement-test"
    try:
        retired = client.get(f"{prefix}/{route}/synthetic-secret-token{suffix}")
        unknown = client.get(f"{prefix}/{route}/unknown{suffix}")
        assert retired.status_code in (401, 404)
        assert (retired.status_code, retired.content, retired.headers) == (
            unknown.status_code,
            unknown.content,
            unknown.headers,
        )
    finally:
        configure_tenant_index(None)


def test_daily_record_is_coarse_once_and_failure_safe(
    setup: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime

    index = tenants.TenantIndex.from_registry_path(setup[0])
    tenant = index.by_slug("alice")
    assert tenant is not None

    async def inner(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    endpoint = Route("/mcp/{token}", inner)
    endpoint.app = TenantRoutingASGIApp(endpoint.app, index)
    client = TestClient(Starlette(routes=[endpoint]))
    assert client.get("/mcp/synthetic-secret-token").status_code == 200
    index.capability_usage.drain()
    record = tenant.data_dir / "capability-url-last-used"
    assert record.read_text().strip() == datetime.now(UTC).date().isoformat()
    first = record.stat().st_mtime_ns
    for _ in range(5):
        assert client.get("/mcp/synthetic-secret-token").status_code == 200
    index.capability_usage.drain()
    assert record.stat().st_mtime_ns == first
    record.unlink()
    fresh = tenants.TenantIndex.from_registry_path(setup[0])
    endpoint.app = TenantRoutingASGIApp(endpoint.app, fresh)
    # Clear only the synthetic workspace from the shared successful-date cache.
    fresh.capability_usage._scheduled.pop(tenant.data_dir, None)
    monkeypatch.setattr(os, "replace", lambda *args: (_ for _ in ()).throw(OSError("no write")))
    assert client.get("/mcp/synthetic-secret-token").status_code == 200
    fresh.capability_usage.drain()
    assert not record.exists()


def test_invalid_retirement_flag_fails_closed(setup: tuple[Path, Path, Path, Path]) -> None:
    text = (
        setup[0]
        .read_text()
        .replace("funded = true", 'funded = true\ncapability_url_retired = "false"')
    )
    setup[0].write_text(text)
    with pytest.raises(tenants.TenantRegistryError, match="capability_url_retired"):
        tenants.load_registry(setup[0])


def test_corrupt_identity_map_never_echoes_subject(setup: tuple[Path, Path, Path, Path]) -> None:
    setup[1].write_text(setup[1].read_text() * 2)
    result = command(setup, "migration")
    assert result.exit_code == 1
    assert "synthetic-subject" not in result.output
    assert "could not be read or validated" in result.output


def test_blocked_disk_write_cannot_block_capability_request(
    setup: tuple[Path, Path, Path, Path],
) -> None:
    import threading

    from wingman.infrastructure import capability_usage

    started = threading.Event()
    release = threading.Event()
    replace = os.replace

    def blocked_replace(source: str, target: Path) -> None:
        started.set()
        assert release.wait(5), "test did not release worker"
        replace(source, target)

    index = tenants.TenantIndex.from_registry_path(setup[0])

    async def inner(request: object) -> PlainTextResponse:
        return PlainTextResponse("accepted")

    endpoint = Route("/mcp/{token}", inner)
    endpoint.app = TenantRoutingASGIApp(endpoint.app, index)
    client = TestClient(Starlette(routes=[endpoint]))
    try:
        with patch.object(capability_usage.os, "replace", blocked_replace):
            response = client.get("/mcp/synthetic-secret-token")
            assert response.status_code == 200
            assert started.wait(2)
            assert not release.is_set()  # response returned before disk write could complete
            release.set()
            index.capability_usage.drain()
    finally:
        release.set()


def test_use_rolls_over_utc_days_without_rewriting_today(
    setup: tuple[Path, Path, Path, Path],
) -> None:
    from datetime import UTC, datetime

    from wingman.infrastructure import capability_usage

    class Clock(datetime):
        day = 17

        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime(2026, 10, cls.day, 0, 0, tzinfo=UTC)

    index = tenants.TenantIndex.from_registry_path(setup[0])
    tenant = index.by_slug("alice")
    assert tenant is not None

    async def inner(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    endpoint = Route("/mcp/{token}", inner)
    endpoint.app = TenantRoutingASGIApp(endpoint.app, index)
    client = TestClient(Starlette(routes=[endpoint]))
    record = tenant.data_dir / capability_usage.FILENAME
    with patch.object(capability_usage, "datetime", Clock):
        for day in (17, 18):
            Clock.day = day
            assert client.get("/mcp/synthetic-secret-token").status_code == 200
            index.capability_usage.drain()
            assert record.read_text().strip() == f"2026-10-{day}"


def test_reload_retirement_and_undo_enforce_original_route(
    setup: tuple[Path, Path, Path, Path],
) -> None:
    index = tenants.TenantIndex.from_registry_path(setup[0])

    async def inner(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    endpoint = Route("/mcp/{token}", inner)
    endpoint.app = TenantRoutingASGIApp(endpoint.app, index)
    client = TestClient(Starlette(routes=[endpoint]))
    assert client.get("/mcp/synthetic-secret-token").status_code == 200
    assert command(setup, "retire-url", "alice").exit_code == 0
    index.reload(setup[0])
    assert client.get("/mcp/synthetic-secret-token").status_code == 401
    assert command(setup, "retire-url", "alice", "--undo").exit_code == 0
    index.reload(setup[0])
    assert client.get("/mcp/synthetic-secret-token").status_code == 200


def test_undo_needs_only_registry_when_oauth_is_broken(
    setup: tuple[Path, Path, Path, Path],
) -> None:
    assert command(setup, "retire-url", "alice").exit_code == 0
    for path in setup[1:]:
        path.unlink()
    result = cli.invoke(
        app, ["tenant", "retire-url", "alice", "--undo", "--registry", str(setup[0])]
    )
    assert result.exit_code == 0, result.output
    assert (
        tenants.TenantIndex.from_registry_path(setup[0]).resolve("synthetic-secret-token")
        is not None
    )


def test_recorder_start_failure_cannot_fail_request(setup: tuple[Path, Path, Path, Path]) -> None:
    from wingman.infrastructure.capability_usage import CapabilityUsage

    index = tenants.TenantIndex.from_registry_path(setup[0])
    index.capability_usage = CapabilityUsage()

    async def inner(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    endpoint = Route("/mcp/{token}", inner)
    endpoint.app = TenantRoutingASGIApp(endpoint.app, index)
    client = TestClient(Starlette(routes=[endpoint]))
    # Scope the failure to our recorder's Thread, not TestClient's portal thread.
    import threading

    original = threading.Thread.start

    def start(thread: threading.Thread) -> None:
        if thread.name == "capability-use":
            raise RuntimeError("synthetic worker exhaustion")
        original(thread)

    with patch.object(threading.Thread, "start", start):
        assert client.get("/mcp/synthetic-secret-token").status_code == 200
