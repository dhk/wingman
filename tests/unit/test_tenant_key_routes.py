"""The single-account ladder must not answer tenant questions.

Four places applied it to tenants: 'keys where --tenant' named the
operator's host file as USED, 'keys validate --tenant' could green-check a
credential the tenant never spends, 'keys set --tenant' silently wrote the
operator's hydrated key into somebody else's BYOK file, and its closing
hint pointed back at 'keys where'. These pin the replacements.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure import keys as keys_mod
from wingman.infrastructure.keys import validate_tenant_keys

runner = CliRunner()


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["keys", "where", "--tenant", "trent"], "wingman tenant keys --tenant trent"),
        (["keys", "where", "--all-tenants"], "wingman tenant keys --all"),
        (["keys", "validate", "--tenant", "trent"], "wingman tenant validate --tenant trent"),
        (["keys", "validate", "--all-tenants"], "wingman tenant validate --all"),
    ],
)
def test_tenant_flags_redirect_instead_of_answering_on_the_wrong_ladder(argv, expected):
    result = runner.invoke(app, argv)
    assert result.exit_code == 2
    assert expected in result.output


def test_the_moved_flags_are_off_the_help_surface():
    for cmd in (["keys", "where", "--help"], ["keys", "validate", "--help"]):
        assert "--tenant" not in runner.invoke(app, cmd).output


def test_keys_set_for_a_tenant_never_takes_the_hydrated_operator_key(tmp_path, monkeypatch):
    """The live lobster failure: 'ensure_env' hydrates the operator's key
    into this process on every invocation, so the old 'value or environ'
    fallback silently pinned the OPERATOR's credential as the tenant's own
    and printed 'Stored'. The write must come from the prompt instead."""
    from wingman.infrastructure import tenants as tenants_mod

    data_dir = tmp_path / "trent"
    data_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-operator-hydrated")
    monkeypatch.setattr(tenants_mod, "tenant_registry_path", lambda *a, **k: tmp_path / "reg.toml")
    monkeypatch.setattr(
        tenants_mod,
        "load_registry",
        lambda path: [tenants_mod.Tenant(slug="trent", data_dir=data_dir)],
    )

    from wingman.cli.main import keys_set

    monkeypatch.setattr("typer.prompt", lambda text, **kw: "sk-ant-typed-by-hand")
    keys_set(name="anthropic", value="", scope="workspace", tenant="trent")

    stored = keys_mod.read_workspace_keys(data_dir)["ANTHROPIC_API_KEY"]
    assert stored == "sk-ant-typed-by-hand"
    assert stored != "sk-ant-operator-hydrated"


def test_keys_set_for_this_account_still_accepts_the_exported_variable(tmp_path, monkeypatch):
    """The non-tenant path keeps its documented convenience — the fix is
    scoped to writing into somebody ELSE's workspace."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-my-own-export")
    monkeypatch.setattr(
        "typer.prompt", lambda *a, **k: pytest.fail("must not prompt when the variable is set")
    )
    from wingman.cli.main import keys_set

    keys_set(name="anthropic", value="", scope="workspace", tenant="")
    # The caller's own resolved workspace -- which conftest's autouse HOME
    # fixture sandboxes, so this writes under tmp, never the real one.
    from wingman.infrastructure.config import load_config

    assert keys_mod.read_workspace_keys(load_config().data_dir)["ANTHROPIC_API_KEY"] == (
        "sk-ant-my-own-export"
    )


def test_validate_tenant_keys_reports_no_key_rather_than_the_operator_host_file(
    tmp_path, monkeypatch
):
    """An unfunded tenant with no key of their own has NO key. The
    single-account ladder would fall through to the host file and pass."""
    _write(tmp_path / "host.env", "ANTHROPIC_API_KEY=sk-ant-operator\n")
    monkeypatch.setattr(
        keys_mod, "test_key_value", lambda short, value: (True, "would have called out")
    )
    rows = validate_tenant_keys(tmp_path / "ws", funded=False, global_path=tmp_path / "none.env")
    anthropic = next(r for r in rows if r.short_name == "anthropic")
    assert anthropic.ok is False
    assert "not funded" in anthropic.message


def test_validate_tenant_keys_tests_the_funded_global_when_that_is_what_is_spent(
    tmp_path, monkeypatch
):
    glob = _write(tmp_path / "global.env", "ANTHROPIC_API_KEY=sk-ant-box-wide\n")
    seen: list[str] = []

    def fake_test(short: str, value: str) -> tuple[bool, str]:
        seen.append(value)
        return True, "ok"

    monkeypatch.setattr(keys_mod, "test_key_value", fake_test)
    rows = validate_tenant_keys(tmp_path / "ws", funded=True, global_path=glob)
    anthropic = next(r for r in rows if r.short_name == "anthropic")
    assert anthropic.tier == "global file (funded fallback)"
    assert "sk-ant-box-wide" in seen


def test_validate_tenant_keys_prefers_the_tenants_own_key(tmp_path, monkeypatch):
    data_dir = tmp_path / "ws"
    _write(data_dir / "keys.env", "ANTHROPIC_API_KEY=sk-ant-tenant-own\n")
    glob = _write(tmp_path / "global.env", "ANTHROPIC_API_KEY=sk-ant-box-wide\n")
    seen: list[str] = []
    monkeypatch.setattr(keys_mod, "test_key_value", lambda s, v: (seen.append(v), (True, "ok"))[1])
    rows = validate_tenant_keys(data_dir, funded=True, global_path=glob)
    assert next(r for r in rows if r.short_name == "anthropic").tier == "workspace file"
    assert "sk-ant-tenant-own" in seen
    assert "sk-ant-box-wide" not in seen


def test_validate_tenant_keys_tests_the_host_key_a_funded_tenant_actually_spends(
    tmp_path, monkeypatch
):
    glob = _write(tmp_path / "global.env", "ANTHROPIC_API_KEY=sk-ant-box-wide\n")
    home = tmp_path / "service-home"
    _write(home / ".config" / "wingman" / "secrets.env", "ANTHROPIC_API_KEY=sk-ant-service\n")
    seen: list[str] = []
    monkeypatch.setattr(keys_mod, "test_key_value", lambda s, v: (seen.append(v), (True, "ok"))[1])

    rows = validate_tenant_keys(tmp_path / "ws", funded=True, global_path=glob, home=home)

    assert next(r for r in rows if r.short_name == "anthropic").tier == (
        "host file (funded fallback)"
    )
    assert "sk-ant-service" in seen
    assert "sk-ant-box-wide" not in seen
