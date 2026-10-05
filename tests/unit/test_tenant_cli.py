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


def test_tenant_urls_unreadable_registry_diagnoses_rather_than_says_malformed(
    tmp_path: Path,
) -> None:
    """The operator-facing half of #411. These commands belong to the person
    who can fix the permissions; telling them a file they cannot open is
    'malformed' sends them to edit it instead."""
    directory = tmp_path / "etc"
    directory.mkdir()
    registry = directory / "tenants.toml"
    registry.write_text("", encoding="utf-8")
    directory.chmod(0o000)
    try:
        result = cli.invoke(app, ["tenant", "urls", "--registry", str(registry)])
    finally:
        directory.chmod(0o755)

    assert result.exit_code == 1
    assert "permission denied" in result.output
    assert "predates" in result.output
    assert "malformed" not in result.output


def test_tenant_keys_by_key_groups_tenants_sharing_one_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#536: the question a leak asks is 'who is spending this key', and
    per-tenant rows answer it only by eye across every block."""
    from typer.testing import CliRunner

    from wingman.cli.main import app
    from wingman.infrastructure import keys as keys_module
    from wingman.infrastructure import tenants as tenants_module

    shared = tmp_path / "global.env"
    shared.write_text("ANTHROPIC_API_KEY=sk-ant-shared-operator\n", encoding="utf-8")
    for slug in ("jason", "bob", "dave", "newbie"):
        (tmp_path / slug).mkdir()
    (tmp_path / "dave" / "keys.env").write_text(
        "ANTHROPIC_API_KEY=sk-ant-daves-own\n", encoding="utf-8"
    )
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        "".join(
            f'[[tenant]]\nslug = "{slug}"\ndata_dir = "{tmp_path / slug}"\n'
            + ("funded = true\n" if slug in ("jason", "bob") else "")
            for slug in ("jason", "bob", "dave", "newbie")
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(keys_module, "GLOBAL_KEYS_PATH", shared)
    monkeypatch.setattr(tenants_module, "DEFAULT_REGISTRY_PATH", registry)

    out = CliRunner().invoke(app, ["tenant", "keys", "--by-key"]).output

    # The two funded tenants appear as ONE group; dave's own key is its own.
    assert "2 tenants" in out
    jason_line = next(line for line in out.splitlines() if "jason" in line)
    assert "bob" in jason_line and "dave" not in jason_line
    # A tenant with nothing is visible rather than absent.
    assert "(not configured)" in out
    assert "newbie" in out
    # Never the value.
    assert "sk-ant-shared-operator" not in out
    assert "sk-ant-daves-own" not in out


def _by_key_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tenants: dict) -> str:
    """Registry of {slug: (workspace key or None, funded)} plus a global file."""
    from typer.testing import CliRunner

    from wingman.cli.main import app
    from wingman.infrastructure import keys as keys_module
    from wingman.infrastructure import tenants as tenants_module

    shared = tmp_path / "global.env"
    shared.write_text("ANTHROPIC_API_KEY=sk-ant-shared-operator\n", encoding="utf-8")
    entries = []
    for slug, (own_key, funded) in tenants.items():
        (tmp_path / slug).mkdir()
        if own_key:
            (tmp_path / slug / "keys.env").write_text(
                f"ANTHROPIC_API_KEY={own_key}\n", encoding="utf-8"
            )
        entries.append(
            f'[[tenant]]\nslug = "{slug}"\ndata_dir = "{tmp_path / slug}"\n'
            + ("funded = true\n" if funded else "")
        )
    registry = tmp_path / "tenants.toml"
    registry.write_text("".join(entries), encoding="utf-8")
    monkeypatch.setattr(keys_module, "GLOBAL_KEYS_PATH", shared)
    monkeypatch.setattr(tenants_module, "DEFAULT_REGISTRY_PATH", registry)
    return CliRunner().invoke(app, ["tenant", "keys", "--by-key"]).output


def _anthropic_block(out: str) -> list[str]:
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if "ANTHROPIC_API_KEY" in line)
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].startswith("  ") and "_KEY" in lines[i]
        ),
        len(lines),
    )
    return lines[start:end]


def test_by_key_never_merges_two_keys_that_share_a_display_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The displayed digest is six hex characters. Two different keys that
    collide on it are two blast radii, not one."""
    from wingman.infrastructure import keys as keys_module

    monkeypatch.setattr(keys_module, "fingerprint", lambda value: "sk-ant-...#000000 (len 9)")
    out = _by_key_fixture(
        tmp_path, monkeypatch, {"amy": ("sk-ant-one", False), "ben": ("sk-ant-two", False)}
    )

    block = _anthropic_block(out)
    assert not any("2 tenants" in line for line in block)
    assert sum("1 tenant " in line for line in block) == 2


def test_by_key_lists_unconfigured_after_every_real_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three people with no key is not a blast radius; one shared key is."""
    out = _by_key_fixture(
        tmp_path,
        monkeypatch,
        {
            "amy": ("sk-ant-shared-own", False),
            "ben": ("sk-ant-shared-own", False),
            "cy": (None, False),
            "di": (None, False),
            "ed": (None, False),
        },
    )
    block = _anthropic_block(out)
    shared_at = next(i for i, line in enumerate(block) if "amy" in line)
    unconfigured_at = next(i for i, line in enumerate(block) if "(not configured)" in line)
    assert shared_at < unconfigured_at


def test_by_key_names_every_tier_a_shared_key_is_spent_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One person pasted the operator's key into their workspace; another is
    funded from the global file. Same key, two sources — naming only the
    first hides the copy that has to be rotated too."""
    out = _by_key_fixture(
        tmp_path,
        monkeypatch,
        {"amy": ("sk-ant-shared-operator", False), "ben": (None, True)},
    )
    from_line = next(line for line in _anthropic_block(out) if "from " in line)
    assert "workspace file" in from_line
    assert "global file" in from_line


def test_by_key_does_not_call_an_unreadable_tenant_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key may be in the file nobody could read. Staged without chmod,
    which root ignores."""
    from wingman.infrastructure import keys as keys_module

    real_read = keys_module._read_known_keys_file
    blocked = tmp_path / "amy" / "keys.env"

    def read(path: Path) -> tuple[dict[str, str], bool]:
        return ({}, True) if path == blocked else real_read(path)

    monkeypatch.setattr(keys_module, "_read_known_keys_file", read)
    out = _by_key_fixture(tmp_path, monkeypatch, {"amy": ("sk-ant-hidden", False)})

    block = "\n".join(_anthropic_block(out))
    assert "(not configured)" not in block
    assert "unreadable: workspace file" in block
    assert "amy" in block
