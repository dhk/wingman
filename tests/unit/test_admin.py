"""Admin installations page (#130): explicit config, own credential, launcher only."""

import asyncio
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

import httpx

from wingman.admin import (
    Instance,
    InstallationsConfigError,
    admin_token,
    load_instances,
    register_admin,
)
from wingman.cli.main import app
from wingman.infrastructure.config import load_config
from wingman.mcp_server import _http_token, server
from wingman.webui import register_ui

cli = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "ws"
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(data_dir))
    data_dir.mkdir(parents=True)
    return data_dir


@pytest.fixture
def client(workspace: Path) -> tuple[TestClient, str]:
    config = load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    token = admin_token(config)
    register_admin(server)
    return TestClient(server.streamable_http_app()), token


def test_admin_token_is_persistent_secret_separate_from_http_token(workspace: Path) -> None:
    config = load_config()
    token = admin_token(config)
    assert len(token) >= 24
    path = workspace / "installations-token"
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert admin_token(config) == token  # stable across calls
    http_token = _http_token(config)
    assert http_token != token  # distinct credential from the per-instance token


def test_load_instances_empty_when_unconfigured(workspace: Path) -> None:
    assert load_instances(load_config()) == []


def test_load_instances_parses_toml(workspace: Path) -> None:
    (workspace / "installations.toml").write_text(
        """
[[instance]]
name = "dhk"
port = 8787
token = "dhktoken"

[[instance]]
name = "trent"
port = 8788
prefix = "/trent"
token = "trenttoken"
""",
        encoding="utf-8",
    )
    instances = load_instances(load_config())
    assert [i.name for i in instances] == ["dhk", "trent"]
    assert instances[0].host == "127.0.0.1"  # default
    assert instances[0].prefix == ""
    assert instances[1].prefix == "/trent"


def test_load_instances_rejects_malformed_toml(workspace: Path) -> None:
    (workspace / "installations.toml").write_text("not valid toml [[[", encoding="utf-8")
    with pytest.raises(InstallationsConfigError):
        load_instances(load_config())


def test_load_instances_rejects_missing_required_field(workspace: Path) -> None:
    (workspace / "installations.toml").write_text(
        '[[instance]]\nname = "dhk"\nport = 8787\n',  # no token
        encoding="utf-8",
    )
    with pytest.raises(InstallationsConfigError):
        load_instances(load_config())


def test_wrong_or_missing_admin_token_is_a_plain_404(client: tuple[TestClient, str]) -> None:
    http, token = client
    assert http.get("/admin/wrong-token/installations").status_code == 404
    ok = http.get(f"/admin/{token}/installations")
    assert ok.status_code == 200


def test_page_shows_empty_state_with_no_instances_configured(
    client: tuple[TestClient, str],
) -> None:
    http, token = client
    page = http.get(f"/admin/{token}/installations").text
    assert "No instances configured" in page
    assert "installations.toml" in page


def test_page_shows_running_and_stopped_instances(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One instance is this same test process (register_ui on a real port via
    TestClient can't be polled over real sockets, so this exercises the
    mixed-state rendering directly against a fake health checker instead)."""
    import wingman.admin as admin_module

    (workspace / "installations.toml").write_text(
        """
[[instance]]
name = "dhk"
port = 8787
token = "dhktoken"

[[instance]]
name = "trent"
port = 8788
prefix = "/trent"
token = "trenttoken"
""",
        encoding="utf-8",
    )
    config = load_config()
    admin_token(config)
    register_admin(server)
    http = TestClient(server.streamable_http_app())
    token = admin_token(config)

    async def fake_check_health(instance: Instance, client: httpx.AsyncClient) -> dict[str, object]:
        if instance.name == "dhk":
            return {"running": True, "version": "0.5.0", "started_at": "2026-07-23T00:00:00"}
        return {"running": False}

    monkeypatch.setattr(admin_module, "_check_health", fake_check_health)
    monkeypatch.setattr("wingman.mcp_server._tailscale_dns_name", lambda: None)  # deterministic
    page = http.get(f"/admin/{token}/installations").text
    assert ">dhk<" in page and ">trent<" in page
    assert "running" in page and "stopped" in page
    assert "v0.5.0" in page and "2026-07-23T00:00:00" in page
    assert 'href="http://127.0.0.1:8787/ui/dhktoken/"' in page
    assert 'href="http://127.0.0.1:8788/trent/ui/trenttoken/"' in page


def test_check_health_is_async_not_blocking(workspace: Path) -> None:
    """Regression: a synchronous httpx.Client here previously deadlocked an
    instance checking its own /health, since the outgoing request and the
    incoming one it's waiting to answer share a single event loop — it
    only showed up when an instance checked itself, reading as 'stopped'
    despite the process being fine."""
    import wingman.admin as admin_module

    assert asyncio.iscoroutinefunction(admin_module._check_health)


def test_open_url_prefers_tunnel_host_over_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The launcher link is normally opened from a browser reached through
    the tunnel, not from lobster itself — a loopback link there resolves
    against the viewer's OWN machine, not lobster (issue: Trent's
    drilldown link pointed at 127.0.0.1:8788, unreachable off-box)."""
    from wingman.admin import Instance, _open_url

    instance = Instance(
        name="trent", host="127.0.0.1", port=8788, prefix="/trent", token="TOK", tunnel_port=8443
    )
    monkeypatch.setattr("wingman.mcp_server._tailscale_dns_name", lambda: None)
    assert _open_url(instance) == "http://127.0.0.1:8788/trent/ui/TOK/"  # no tunnel: loopback

    monkeypatch.setattr(
        "wingman.mcp_server._tailscale_dns_name", lambda: "lobster.tail08dfce.ts.net"
    )
    assert _open_url(instance) == "https://lobster.tail08dfce.ts.net:8443/trent/ui/TOK/"

    no_port = Instance(name="dhk", host="127.0.0.1", port=8787, prefix="", token="TOK2")
    assert _open_url(no_port) == "https://lobster.tail08dfce.ts.net/ui/TOK2/"  # no tunnel_port set


def test_health_endpoint_is_unauthenticated_and_minimal(workspace: Path) -> None:
    register_ui(server)
    http = TestClient(server.streamable_http_app())
    response = http.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"version", "started_at"}


def test_admin_url_command_prints_the_page_url(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = cli.invoke(app, ["admin", "url", "--port", "8787"])
    assert result.exit_code == 0
    token = admin_token(load_config())
    assert f"http://127.0.0.1:8787/admin/{token}/installations" in result.output


def test_admin_url_command_includes_tunnel_line_when_a_host_is_detected(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = cli.invoke(
        app,
        ["admin", "url", "--port", "8787", "--allowed-host", "lobster.tail.ts.net"],
    )
    assert result.exit_code == 0
    token = admin_token(load_config())
    assert (
        f"Tunnel installations page: https://lobster.tail.ts.net/admin/{token}/installations"
        in result.output
    )
