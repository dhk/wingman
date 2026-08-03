"""'wingman tenant url' / 'wingman tenant rotate-token' (RFC-048, #209/#210)."""

from __future__ import annotations

from pathlib import Path

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
