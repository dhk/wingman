import os
from pathlib import Path

from typer.testing import CliRunner

from wingman.cli.main import app

runner = CliRunner()


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
