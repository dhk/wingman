import os
import re
from pathlib import Path

from typer.testing import CliRunner

from wingman.cli.main import app

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Help output with Rich's styling stripped.

    Rich renders an option name as several styled segments — '--tenant'
    arrives as '\x1b[1;36m-\x1b[0m\x1b[1;36m-tenant\x1b[0m' — so a literal
    substring check passes only where colour happens to be off. It is off
    locally and on in CI, which is the worst possible way for it to fail.
    """
    return _ANSI.sub("", text)


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "doctor", "status"):
        assert command in result.stdout


def test_doctor_reports_host_config_layout() -> None:
    """RFC-046: 'wingman doctor' names the new split host-config layout,
    not just the key ladder — this is what makes a migration (or its
    absence) visible without hunting the filesystem by hand."""
    result = runner.invoke(app, ["doctor"])
    assert "host config" in result.output
    assert "wingman.env for host settings" in result.output
    assert "secrets.env for secrets" in result.output


def test_first_run_migrates_legacy_host_file_and_says_so() -> None:
    """RFC-046: an existing deployment's already-populated old
    '~/.config/keys.env' migrates automatically on the very first command
    run against it, and the migration is never silent."""
    home = Path(os.environ["HOME"])  # conftest's per-test isolated $HOME
    legacy = home / ".config" / "keys.env"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "ANTHROPIC_API_KEY=sk-ant-fixture\nWINGMAN_REPO=/home/dhk/src/wingman\n",
        encoding="utf-8",
    )

    # 'ensure_env' hydrates the REAL os.environ as a side effect (that's
    # the point), bypassing monkeypatch's tracking — restore it by hand so
    # this fixture value never leaks into a later test in the session.
    original_anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        result = runner.invoke(app, ["doctor"])

        assert "one-time host config migration" in result.output
        assert str(legacy) in result.output
        assert not legacy.exists()  # renamed away, never left in place
        new_secrets = home / ".config" / "wingman" / "secrets.env"
        new_settings = home / ".config" / "wingman" / "wingman.env"
        assert new_secrets.is_file()
        assert new_settings.is_file()
        assert "ANTHROPIC_API_KEY=sk-ant-fixture" in new_secrets.read_text(encoding="utf-8")
        assert "WINGMAN_REPO=/home/dhk/src/wingman" in new_settings.read_text(encoding="utf-8")

        # a second run is a silent no-op: no repeated migration message
        second = runner.invoke(app, ["doctor"])
        assert "one-time host config migration" not in second.output
        assert "host config" in second.output
    finally:
        if original_anthropic_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = original_anthropic_key


def test_keys_where_shows_the_resolution_order_and_paths() -> None:
    """The 'where is my key?' question answered without opening a file:
    every tier named, in the order they are consulted."""
    result = runner.invoke(app, ["keys", "where"])
    assert result.exit_code == 0
    for tier in ("workspace file", "environment", "keychain", "host file", "global file"):
        assert tier in result.output
    assert "secrets.env" in result.output
    assert "global-secrets.env" in result.output


def test_keys_where_never_prints_a_key_value(monkeypatch) -> None:
    """A diagnostic that leaks the secret it is diagnosing is worse than none."""
    secret = "sk-ant-api03-DO-NOT-PRINT-THIS-TAIL"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    result = runner.invoke(app, ["keys", "where"])
    assert result.exit_code == 0
    assert secret not in result.output
    assert "DO-NOT-PRINT-THIS-TAIL" not in result.output


def test_keys_where_no_longer_offers_the_tenant_flags() -> None:
    """They answered on the single-account ladder, which names this
    account's host file — a file no tenant ever reads. Moved to
    'wingman tenant keys' rather than left quietly wrong."""
    result = runner.invoke(app, ["keys", "where", "--help"])
    assert result.exit_code == 0
    assert "--tenant" not in plain(result.output)
    assert "--all-tenants" not in plain(result.output)


def test_keys_set_rejects_an_unknown_scope() -> None:
    result = runner.invoke(app, ["keys", "set", "anthropic", "--value", "x", "--scope", "nope"])
    assert result.exit_code == 1
    assert "unknown --scope" in result.output


def test_keys_set_rejects_tenant_without_workspace_scope() -> None:
    """--tenant names a workspace; pairing it with a box-wide tier would
    silently write somewhere other than where the operator meant."""
    result = runner.invoke(
        app, ["keys", "set", "anthropic", "--value", "x", "--scope", "host", "--tenant", "bob"]
    )
    assert result.exit_code == 1
    assert "--tenant only applies to --scope workspace" in result.output


def test_keys_set_help_names_every_scope() -> None:
    result = runner.invoke(app, ["keys", "set", "--help"])
    assert result.exit_code == 0
    for scope in ("keychain", "host", "workspace"):
        assert scope in plain(result.output)


def test_keys_list_states_the_current_precedence() -> None:
    """The old footer claimed an exported variable wins; BYOK reversed that,
    and a stale precedence note is how someone edits the wrong tier."""
    result = runner.invoke(app, ["keys", "list"])
    assert result.exit_code == 0
    assert "workspace" in result.output
    assert "keys where" in result.output


def test_keys_validate_no_longer_offers_the_tenant_flags() -> None:
    """Same reason as 'keys where', with a sharper failure: falling through
    to this account's host file would live-test a working key and report a
    pass for a tenant who has none. Moved to 'wingman tenant validate'."""
    result = runner.invoke(app, ["keys", "validate", "--help"])
    assert result.exit_code == 0
    assert "--tenant" not in plain(result.output)
    assert "--all-tenants" not in plain(result.output)


def test_keys_validate_names_the_tier_and_fails_loudly(monkeypatch) -> None:
    """An expired key must exit non-zero and say which tier to fix."""
    from wingman.infrastructure import keys as module

    monkeypatch.setattr(module, "keychain_available", lambda: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-expired-value")
    for name in module.KNOWN_KEYS:
        monkeypatch.setitem(module._LIVE_TESTS, name, lambda value: (False, "rejected"))

    result = runner.invoke(app, ["keys", "validate"])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "environment" in result.output
    assert "sk-ant-expired-value" not in result.output
    assert "keys set" in result.output
