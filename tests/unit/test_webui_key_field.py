"""A tenant must always be able to type their own key into the Manage form.

'ensure_env' hydrates the box-wide global file into the SHARED process's
environment at startup. 'key_status_rows' used to read that environment for
every caller, so on a funded box every tenant's field rendered readonly
("set in the service environment") — locking them out of the one action the
panel exists for, and naming a tier their calls never consult
(strict_provider_keys, RFC-048).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wingman.infrastructure.config import Config
from wingman.webui import _key_field, key_status_rows


def _config(tmp_path: Path, *, strict: bool, funded: bool) -> Config:
    return Config(
        data_dir=tmp_path,
        data_dir_source="test",
        strict_provider_keys=strict,
        funded=funded,
    )


def _source(rows, short="anthropic"):
    return next(source for s, _, source in rows if s == short)


def test_tenant_never_reports_environment_even_when_process_env_is_set(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-service-account-key")
    rows = key_status_rows(tmp_path, _config(tmp_path, strict=True, funded=False))
    assert _source(rows) == "not set"


def test_single_account_still_reports_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-operator-own-key")
    rows = key_status_rows(tmp_path, _config(tmp_path, strict=False, funded=False))
    assert _source(rows) == "environment"


def test_workspace_key_wins_for_a_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-service-account-key")
    (tmp_path / "keys.env").write_text("ANTHROPIC_API_KEY=sk-ant-tenant-own\n", encoding="utf-8")
    rows = key_status_rows(tmp_path, _config(tmp_path, strict=True, funded=True))
    assert _source(rows) == "workspace file"


@pytest.mark.parametrize("source", ["not set", "workspace file", "global file (funded)"])
def test_every_tenant_reachable_state_renders_an_editable_named_input(source):
    html = _key_field("anthropic", "ANTHROPIC_API_KEY", source)
    assert 'name="anthropic"' in html, "field must submit — a tenant has to be able to set a key"
    assert "readonly" not in html


def test_environment_state_is_the_only_readonly_one():
    html = _key_field("anthropic", "ANTHROPIC_API_KEY", "environment")
    assert "readonly" in html
    assert 'name="anthropic"' not in html


def test_funded_fallback_says_the_shared_key_is_in_use_and_can_be_overridden():
    html = _key_field("anthropic", "ANTHROPIC_API_KEY", "global file (funded)")
    assert "funded" in html
    assert "set your own here" in html
