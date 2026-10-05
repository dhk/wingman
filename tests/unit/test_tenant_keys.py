"""The strict tenant ladder (RFC-048): a tenant reads their own workspace
file, then — only if funded, or always for the shared GitHub key — the
shared process account's host file and then the global file. Never the
process environment.

'keys where' reported the single-account ladder for tenants, which named a
file no tenant ever reads. These pin the ladder that matches
'providers.router.metered_key' and 'feature_request._resolve_github_key',
and the reporting rule that an unreadable tier suppresses any winner below
it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wingman.infrastructure.keys import describe_tenant_key_locations


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _row(rows, tier_starts_with):
    return next(r for r in rows["anthropic"] if r.tier.startswith(tier_starts_with))


def test_workspace_key_wins_and_global_is_not_offered_when_unfunded(tmp_path):
    data_dir = tmp_path / "ws"
    _write(data_dir / "keys.env", "ANTHROPIC_API_KEY=sk-ant-tenant-own\n")
    glob = _write(tmp_path / "global.env", "ANTHROPIC_API_KEY=sk-ant-box-wide\n")

    rows = describe_tenant_key_locations(data_dir, funded=False, global_path=glob)

    assert [r.tier for r in rows["anthropic"]] == ["workspace file"]
    assert _row(rows, "workspace").winner is True


def test_funded_tenant_falls_back_to_global(tmp_path):
    data_dir = tmp_path / "ws"
    data_dir.mkdir()
    glob = _write(tmp_path / "global.env", "ANTHROPIC_API_KEY=sk-ant-box-wide\n")

    rows = describe_tenant_key_locations(data_dir, funded=True, global_path=glob)

    assert _row(rows, "workspace").present is False
    assert _row(rows, "global").winner is True


def test_funded_tenant_with_own_key_does_not_use_global(tmp_path):
    data_dir = tmp_path / "ws"
    _write(data_dir / "keys.env", "ANTHROPIC_API_KEY=sk-ant-tenant-own\n")
    glob = _write(tmp_path / "global.env", "ANTHROPIC_API_KEY=sk-ant-box-wide\n")

    rows = describe_tenant_key_locations(data_dir, funded=True, global_path=glob)

    assert _row(rows, "workspace").winner is True
    global_row = _row(rows, "global")
    assert global_row.present is True
    assert global_row.winner is False


def test_unfunded_tenant_with_no_workspace_key_has_nothing(tmp_path):
    data_dir = tmp_path / "ws"
    data_dir.mkdir()
    glob = _write(tmp_path / "global.env", "ANTHROPIC_API_KEY=sk-ant-box-wide\n")

    rows = describe_tenant_key_locations(data_dir, funded=False, global_path=glob)

    assert all(not r.winner for r in rows["anthropic"])
    assert all(not r.present for r in rows["anthropic"])


def test_fingerprints_never_carry_the_value(tmp_path):
    data_dir = tmp_path / "ws"
    _write(data_dir / "keys.env", "ANTHROPIC_API_KEY=sk-ant-secret-tail-abcdef\n")

    rows = describe_tenant_key_locations(data_dir, funded=False, global_path=tmp_path / "none.env")

    fp = _row(rows, "workspace").fingerprint
    assert fp is not None
    assert "secret-tail-abcdef" not in fp
    assert fp.startswith("sk-ant-s")


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root can read anything, so a permission denial cannot be staged",
)
def test_unreadable_workspace_suppresses_any_winner(tmp_path):
    """The bug this command exists to not repeat: a readable lower tier must
    not be announced as USED while the tier above it went unread."""
    data_dir = tmp_path / "ws"
    _write(data_dir / "keys.env", "ANTHROPIC_API_KEY=sk-ant-tenant-own\n")
    (data_dir / "keys.env").chmod(0o000)
    glob = _write(tmp_path / "global.env", "ANTHROPIC_API_KEY=sk-ant-box-wide\n")

    rows = describe_tenant_key_locations(data_dir, funded=True, global_path=glob)

    workspace = _row(rows, "workspace")
    assert workspace.readable is False
    assert workspace.winner is False
    # funded fallback is present and readable, but must NOT be crowned
    assert _row(rows, "global").winner is False


def _home_with(tmp_path: Path, text: str) -> Path:
    home = tmp_path / "service-home"
    _write(home / ".config" / "wingman" / "secrets.env", text)
    return home


def test_a_funded_tenant_spends_the_host_key_before_the_global_one(tmp_path):
    """'declared_shared_key' reads the host file before the global file, so
    a funded tenant with no key of their own is billed to the host copy.
    Reporting workspace → global named the global key as spent while a
    different one was."""
    data_dir = tmp_path / "ws"
    data_dir.mkdir()
    glob = _write(tmp_path / "global.env", "ANTHROPIC_API_KEY=sk-ant-box-wide\n")
    home = _home_with(tmp_path, "ANTHROPIC_API_KEY=sk-ant-service-account\n")

    rows = describe_tenant_key_locations(data_dir, funded=True, global_path=glob, home=home)

    assert [r.tier for r in rows["anthropic"]] == [
        "workspace file",
        "host file (funded fallback)",
        "global file (funded fallback)",
    ]
    assert _row(rows, "host").winner is True
    assert _row(rows, "global").winner is False


def test_an_unfunded_tenant_still_reaches_the_shared_github_key(tmp_path):
    """The issues key is access, not spend (#506): every tenant files
    through the operator's declared copy, funded or not. Reporting it as
    unconfigured sends somebody to fix a key that works."""
    data_dir = tmp_path / "ws"
    data_dir.mkdir()
    glob = _write(tmp_path / "global.env", "GITHUB_SHARED_ISSUES_KEY=github_pat_shared\n")

    rows = describe_tenant_key_locations(
        data_dir, funded=False, global_path=glob, home=tmp_path / "empty-home"
    )

    github = rows["github"]
    assert [r.tier for r in github] == [
        "workspace file",
        "host file (shared)",
        "global file (shared)",
    ]
    assert next(r for r in github if r.winner).tier == "global file (shared)"


def test_an_unfunded_tenant_is_never_offered_the_host_model_key(tmp_path):
    data_dir = tmp_path / "ws"
    data_dir.mkdir()
    home = _home_with(tmp_path, "ANTHROPIC_API_KEY=sk-ant-service-account\n")

    rows = describe_tenant_key_locations(
        data_dir, funded=False, global_path=tmp_path / "none.env", home=home
    )

    assert [r.tier for r in rows["anthropic"]] == ["workspace file"]
    assert not any(r.winner for r in rows["anthropic"])
