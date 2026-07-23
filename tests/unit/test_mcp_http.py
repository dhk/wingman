"""Remote MCP transport (RFC-017): loopback HTTP behind a capability path."""

from pathlib import Path

import pytest

from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import load_config
from wingman.mcp_server import _http_token, main, render_urls, server

cli = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "ws"
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(data_dir))
    return data_dir


class RunRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


def test_token_is_persistent_secret_and_rotatable(workspace: Path) -> None:
    config = load_config()
    token = _http_token(config)
    assert len(token) >= 24
    token_path = workspace / "mcp-http-token"
    assert token_path.exists()
    assert (token_path.stat().st_mode & 0o777) == 0o600  # owner-only
    assert _http_token(config) == token  # stable across runs
    rotated = _http_token(config, rotate=True)
    assert rotated != token  # rotation is revocation


def test_default_invocation_stays_stdio(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RunRecorder()
    monkeypatch.setattr(server, "run", recorder)
    main([])
    assert recorder.calls == [((), {})]  # no transport argument: stdio default
    assert not (workspace / "mcp-http-token").exists()  # no token unless HTTP is asked for


def test_http_invocation_configures_capability_path(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = RunRecorder()
    monkeypatch.setattr(server, "run", recorder)
    main(["--http", "--port", "9911"])
    assert recorder.calls == [((), {"transport": "streamable-http"})]
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 9911
    token = _http_token(load_config())
    assert server.settings.streamable_http_path == f"/mcp/{token}"
    out = capsys.readouterr()
    assert f"http://127.0.0.1:9911/mcp/{token}" in out.out
    assert "rotate-token" in out.out
    assert "WARNING" not in out.err  # loopback bind warns about nothing


def test_non_loopback_bind_warns(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(server, "run", RunRecorder())
    main(["--http", "--host", "0.0.0.0", "--port", "9912"])
    assert "WARNING" in capsys.readouterr().err


def test_rotate_token_requires_http(workspace: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--rotate-token"])


def test_render_urls_is_pure_formatting_over_given_inputs() -> None:
    bare = render_urls("TOK", [], host="127.0.0.1", port=8787, prefix="")
    assert bare == [
        "MCP over HTTP: http://127.0.0.1:8787/mcp/TOK",
        "Web UI (read + upload): http://127.0.0.1:8787/ui/TOK",
    ]
    tunneled = render_urls("TOK", ["lobster.tail.ts.net"], port=9911, prefix="trent/")
    assert tunneled == [
        "MCP over HTTP: http://127.0.0.1:9911/trent/mcp/TOK",
        "Web UI (read + upload): http://127.0.0.1:9911/trent/ui/TOK",
        "Tunnel MCP connector: https://lobster.tail.ts.net/trent/mcp/TOK",
        "Tunnel web UI: https://lobster.tail.ts.net/trent/ui/TOK/",
    ]  # prefix normalization ("trent/" -> "/trent") applies here too


def test_render_urls_honors_an_explicit_tunnel_port() -> None:
    """A tunnel front on a non-443 port (e.g. two instances sharing one
    Tailscale hostname on distinct funnel ports) only changes the tunnel
    lines — the loopback lines still reflect the local --port, unrelated."""
    lines = render_urls(
        "TOK", ["lobster.tail.ts.net"], port=8788, prefix="/trent", tunnel_port=8443
    )
    assert lines == [
        "MCP over HTTP: http://127.0.0.1:8788/trent/mcp/TOK",
        "Web UI (read + upload): http://127.0.0.1:8788/trent/ui/TOK",
        "Tunnel MCP connector: https://lobster.tail.ts.net:8443/trent/mcp/TOK",
        "Tunnel web UI: https://lobster.tail.ts.net:8443/trent/ui/TOK/",
    ]
    assert render_urls("TOK", ["lobster.tail.ts.net"])[2] == (
        "Tunnel MCP connector: https://lobster.tail.ts.net/mcp/TOK"
    )  # tunnel_port=None (default): no port suffix, unchanged behavior


def test_tunnel_port_cli_flag_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from wingman.mcp_server import _tunnel_port

    monkeypatch.setenv("WINGMAN_TUNNEL_PORT", "9999")
    assert _tunnel_port(8443) == 8443  # explicit value wins
    assert _tunnel_port(None) == 9999  # falls back to the env var
    monkeypatch.delenv("WINGMAN_TUNNEL_PORT")
    assert _tunnel_port(None) is None  # unset means 'omit it, assume 443'


def test_mcp_url_command_honors_explicit_tunnel_port(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = cli.invoke(
        app,
        [
            "mcp",
            "url",
            "--port",
            "8788",
            "--prefix",
            "/trent",
            "--allowed-host",
            "lobster.tail.ts.net",
            "--tunnel-port",
            "8443",
        ],
    )
    assert result.exit_code == 0
    token = _http_token(load_config())
    assert (
        f"Tunnel MCP connector: https://lobster.tail.ts.net:8443/trent/mcp/{token}" in result.output
    )
    assert f"Tunnel web UI: https://lobster.tail.ts.net:8443/trent/ui/{token}/" in result.output


def test_mcp_url_command_prints_the_capability_urls(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = cli.invoke(app, ["mcp", "url", "--port", "9914"])
    assert result.exit_code == 0
    token = _http_token(load_config())
    assert f"http://127.0.0.1:9914/mcp/{token}" in result.output
    assert f"http://127.0.0.1:9914/ui/{token}" in result.output


def test_mcp_url_command_honors_explicit_allowed_host(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = cli.invoke(
        app, ["mcp", "url", "--port", "9915", "--allowed-host", "lobster.example.ts.net"]
    )
    assert result.exit_code == 0
    token = _http_token(load_config())
    assert f"Tunnel MCP connector: https://lobster.example.ts.net/mcp/{token}" in result.output
    assert f"Tunnel web UI: https://lobster.example.ts.net/ui/{token}/" in result.output


def test_mcp_url_command_hints_when_no_tunnel_detected(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = cli.invoke(app, ["mcp", "url", "--port", "9916"])
    assert result.exit_code == 0
    assert "no tunnel hostname detected" in result.output


def test_prefix_serves_natively_on_the_folder(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--prefix /trent: the server itself owns the path segment (no proxy strip needed)."""
    monkeypatch.setattr(server, "run", RunRecorder())
    main(["--http", "--port", "9913", "--prefix", "trent/"])  # normalized either way
    token = _http_token(load_config())
    assert server.settings.streamable_http_path == f"/trent/mcp/{token}"
    out = capsys.readouterr().out
    assert f"http://127.0.0.1:9913/trent/mcp/{token}" in out
    assert f"http://127.0.0.1:9913/trent/ui/{token}" in out
