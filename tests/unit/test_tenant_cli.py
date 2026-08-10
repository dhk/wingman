"""'wingman tenant url' / 'wingman tenant rotate-token' (RFC-048, #209/#210)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.storage import Storage

cli = CliRunner()


def _make_tenant(tmp_path: Path, slug: str, token: str | None) -> Path:
    data_dir = tmp_path / slug
    data_dir.mkdir()
    Storage(data_dir / "wingman.db").close()
    if token is not None:
        (data_dir / "mcp-http-token").write_text(token, encoding="utf-8")
    return data_dir


def _write_registry(tmp_path: Path, *entries: tuple[str, Path]) -> Path:
    registry = tmp_path / "tenants.toml"
    lines = []
    for slug, data_dir in entries:
        lines += ["[[tenant]]", f'slug = "{slug}"', f'data_dir = "{data_dir}"', ""]
    registry.write_text("\n".join(lines), encoding="utf-8")
    return registry


def test_tenant_url_prints_connector_urls(tmp_path: Path) -> None:
    data_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    registry = _write_registry(tmp_path, ("jason", data_dir))
    result = cli.invoke(
        app, ["tenant", "url", "jason", "--registry", str(registry), "--port", "9920"]
    )
    assert result.exit_code == 0
    assert "tok-jason" in result.output
    assert "9920" in result.output


def test_tenant_url_tunnel_prefix_reaches_the_tunnel_line(tmp_path: Path) -> None:
    """RFC-048's shared process sits behind a STRIPPING tailscale front
    ('tailscale funnel --set-path /shared') -- the printed tunnel URL
    needs that prefix even though the loopback URL never does. Found
    live migrating dhk's own account to the shared process (Phase 3):
    the un-prefixed URL 404'd at the tunnel, not at wingman."""
    data_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    registry = _write_registry(tmp_path, ("jason", data_dir))
    result = cli.invoke(
        app,
        [
            "tenant",
            "url",
            "jason",
            "--registry",
            str(registry),
            "--port",
            "8789",
            "--allowed-host",
            "lobster.tail.ts.net",
            "--tunnel-prefix",
            "/shared",
        ],
    )
    assert result.exit_code == 0
    assert "http://127.0.0.1:8789/mcp/tok-jason" in result.output  # loopback: no prefix
    assert "https://lobster.tail.ts.net/shared/mcp/tok-jason" in result.output  # tunnel: has it


def test_tenant_url_defaults_connector_name_to_wingman_slug(tmp_path: Path) -> None:
    """Issue #253: 'wingman tenant url <slug>' auto-derives
    'wingman-<slug>' with no flag needed."""
    data_dir = _make_tenant(tmp_path, "taylor", "tok-taylor")
    registry = _write_registry(tmp_path, ("taylor", data_dir))
    result = cli.invoke(app, ["tenant", "url", "taylor", "--registry", str(registry)])
    assert result.exit_code == 0
    assert (
        "claude mcp add --transport http wingman-taylor http://127.0.0.1:8787/mcp/tok-taylor"
        in (result.output)
    )


def test_tenant_url_connector_name_override_and_suppression(tmp_path: Path) -> None:
    data_dir = _make_tenant(tmp_path, "taylor", "tok-taylor")
    registry = _write_registry(tmp_path, ("taylor", data_dir))

    overridden = cli.invoke(
        app,
        ["tenant", "url", "taylor", "--registry", str(registry), "--connector-name", "my-taylor"],
    )
    assert overridden.exit_code == 0
    assert "claude mcp add --transport http my-taylor " in overridden.output

    suppressed = cli.invoke(
        app, ["tenant", "url", "taylor", "--registry", str(registry), "--connector-name", ""]
    )
    assert suppressed.exit_code == 0
    assert "claude mcp add" not in suppressed.output


def test_tenant_url_unknown_slug_exits_nonzero(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path)  # empty
    result = cli.invoke(app, ["tenant", "url", "nobody", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "No tenant 'nobody'" in result.output


def test_tenant_url_missing_token_exits_nonzero(tmp_path: Path) -> None:
    data_dir = _make_tenant(tmp_path, "jason", token=None)  # no token file yet
    registry = _write_registry(tmp_path, ("jason", data_dir))
    result = cli.invoke(app, ["tenant", "url", "jason", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "has no token yet" in result.output


def test_tenant_url_malformed_registry_exits_nonzero(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    registry.write_text("not [ valid toml", encoding="utf-8")
    result = cli.invoke(app, ["tenant", "url", "jason", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "malformed" in result.output


def test_tenant_rotate_token_invalidates_old_and_issues_new(tmp_path: Path) -> None:
    data_dir = _make_tenant(tmp_path, "jason", "tok-old")
    registry = _write_registry(tmp_path, ("jason", data_dir))
    result = cli.invoke(app, ["tenant", "rotate-token", "jason", "--registry", str(registry)])
    assert result.exit_code == 0
    assert "No running shared process found" in result.output  # nothing running in this test
    new_token = (data_dir / "mcp-http-token").read_text(encoding="utf-8").strip()
    assert new_token != "tok-old"
    assert new_token in result.output


def test_tenant_rotate_token_defaults_connector_name_to_wingman_slug(tmp_path: Path) -> None:
    data_dir = _make_tenant(tmp_path, "jason", "tok-old")
    registry = _write_registry(tmp_path, ("jason", data_dir))
    result = cli.invoke(app, ["tenant", "rotate-token", "jason", "--registry", str(registry)])
    assert result.exit_code == 0
    assert "claude mcp add --transport http wingman-jason " in result.output


def test_tenant_rotate_token_tunnel_prefix_reaches_the_tunnel_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # rotate-token has no --allowed-host of its own (unlike 'tenant url') --
    # it only ever sees a tunnel host via WINGMAN_ALLOWED_HOSTS/Tailscale
    # auto-detection, so that's how this test supplies one.
    monkeypatch.setenv("WINGMAN_ALLOWED_HOSTS", "lobster.tail.ts.net")
    data_dir = _make_tenant(tmp_path, "jason", "tok-old")
    registry = _write_registry(tmp_path, ("jason", data_dir))
    result = cli.invoke(
        app,
        [
            "tenant",
            "rotate-token",
            "jason",
            "--registry",
            str(registry),
            "--tunnel-prefix",
            "/shared",
        ],
    )
    assert result.exit_code == 0
    assert "/shared/mcp/" in result.output


def test_tenant_rotate_token_unknown_slug_exits_nonzero(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path)
    result = cli.invoke(app, ["tenant", "rotate-token", "nobody", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "No tenant 'nobody'" in result.output


# The "signals a running process" path is covered at the unit level
# (test_tenant_process.py::test_signal_reload_sends_sighup_to_the_verified_pid,
# with an injected command_of) and proved live against a real running
# server in the RFC-048 smoke test — signal_reload's command_of default
# is bound at function-definition time, so it isn't reachable to
# monkeypatch through this CLI-level default-argument call, matching how
# the existing mcp_process CLI commands (mcp_status/mcp_stop) are also
# only tested this way at the unit level, not through the CLI.


# --- 'wingman tenant urls' (plural, #238's carve-off follow-up) -----------


def test_tenant_urls_with_slug_matches_tenant_url(tmp_path: Path) -> None:
    """A slug given to 'urls' behaves exactly like the singular 'url'."""
    data_dir = _make_tenant(tmp_path, "jason", "tok-jason")
    registry = _write_registry(tmp_path, ("jason", data_dir))
    singular = cli.invoke(
        app, ["tenant", "url", "jason", "--registry", str(registry), "--port", "9920"]
    )
    plural = cli.invoke(
        app, ["tenant", "urls", "jason", "--registry", str(registry), "--port", "9920"]
    )
    assert singular.exit_code == plural.exit_code == 0
    assert singular.output == plural.output


def test_tenant_urls_with_unknown_slug_exits_nonzero(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path)  # empty
    result = cli.invoke(app, ["tenant", "urls", "nobody", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "No tenant 'nobody'" in result.output


def test_tenant_urls_with_slug_missing_token_exits_nonzero(tmp_path: Path) -> None:
    data_dir = _make_tenant(tmp_path, "jason", token=None)
    registry = _write_registry(tmp_path, ("jason", data_dir))
    result = cli.invoke(app, ["tenant", "urls", "jason", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "has no token yet" in result.output


def test_tenant_urls_without_slug_lists_every_tenant(tmp_path: Path) -> None:
    connected = _make_tenant(tmp_path, "jason", "tok-jason")
    not_connected = _make_tenant(tmp_path, "bob", token=None)
    registry = _write_registry(tmp_path, ("jason", connected), ("bob", not_connected))
    result = cli.invoke(app, ["tenant", "urls", "--registry", str(registry), "--port", "9920"])
    assert result.exit_code == 0
    assert "jason:" in result.output
    assert "tok-jason" in result.output
    assert "bob: not yet connected (no token minted)" in result.output


def test_tenant_urls_without_slug_derives_a_name_per_tenant(tmp_path: Path) -> None:
    """Issue #253: the roster view can't apply one fixed connector name
    to every tenant, so each gets its own 'wingman-<slug>' automatically."""
    jason = _make_tenant(tmp_path, "jason", "tok-jason")
    bob = _make_tenant(tmp_path, "bob", "tok-bob")
    registry = _write_registry(tmp_path, ("jason", jason), ("bob", bob))
    result = cli.invoke(app, ["tenant", "urls", "--registry", str(registry)])
    assert result.exit_code == 0
    assert "claude mcp add --transport http wingman-jason " in result.output
    assert "claude mcp add --transport http wingman-bob " in result.output


def test_tenant_urls_without_slug_and_empty_registry(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path)  # empty
    result = cli.invoke(app, ["tenant", "urls", "--registry", str(registry)])
    assert result.exit_code == 0
    assert "No tenants in the registry" in result.output


def test_tenant_urls_without_slug_malformed_registry_exits_nonzero(tmp_path: Path) -> None:
    registry = tmp_path / "tenants.toml"
    registry.write_text("not [ valid toml", encoding="utf-8")
    result = cli.invoke(app, ["tenant", "urls", "--registry", str(registry)])
    assert result.exit_code == 1
    assert "malformed" in result.output


def test_tenant_urls_tunnel_prefix_reaches_every_tenant(tmp_path: Path) -> None:
    connected = _make_tenant(tmp_path, "jason", "tok-jason")
    registry = _write_registry(tmp_path, ("jason", connected))
    result = cli.invoke(
        app,
        [
            "tenant",
            "urls",
            "--registry",
            str(registry),
            "--port",
            "8789",
            "--allowed-host",
            "lobster.tail.ts.net",
            "--tunnel-prefix",
            "/shared",
        ],
    )
    assert result.exit_code == 0
    assert "http://127.0.0.1:8789/mcp/tok-jason" in result.output  # loopback: no prefix
    assert "https://lobster.tail.ts.net/shared/mcp/tok-jason" in result.output  # tunnel: has it
